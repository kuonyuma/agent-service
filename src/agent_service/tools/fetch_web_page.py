import ipaddress
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, ClassVar, Literal
from urllib.parse import urljoin, urlparse

import httpx

from agent_service.tools.base import Tool, ToolResult

OutputFormat = Literal["markdown", "text", "html"]
VALID_FORMATS: tuple[OutputFormat, ...] = ("markdown", "text", "html")

MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB（对齐 OpenCode MAX_RESPONSE_SIZE）
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_CHARS = 8000
MAX_ALLOWED_CHARS = 25000

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "0.0.0.0",
        "metadata.google.internal",
    }
)

_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "iframe",
        "object",
        "embed",
        "svg",
        "canvas",
        "template",
        "meta",
        "link",
        "nav",
        "footer",
    }
)

_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "main",
        "header",
        "aside",
        "blockquote",
        "table",
        "tr",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "figure",
        "figcaption",
    }
)

_HEADING_LEVELS: dict[str, int] = {
    "h1": 1,
    "h2": 2,
    "h3": 3,
    "h4": 4,
    "h5": 5,
    "h6": 6,
}

_INLINE_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MULTI_NEWLINES_RE = re.compile(r"\n{3,}")


@dataclass(frozen=True)
class FetchedPageResponse:
    """HTTP 页面抓取结果快照。"""

    url: str
    status_code: int
    content_type: str
    text: str


PageFetcher = Callable[[str, OutputFormat, float], Awaitable[FetchedPageResponse]]


def validate_public_http_url(raw_url: str) -> str:
    """校验 URL 协议与目标主机，拒绝非 http(s) 与本机/内网私有地址（防 SSRF）。"""

    if not isinstance(raw_url, str) or not raw_url.strip():
        raise ValueError("URL 不能为空。")
    url = raw_url.strip()
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL 必须以 http:// 或 https:// 开头。")

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        raise ValueError("URL 缺少有效的主机名（hostname）。")

    if hostname in _BLOCKED_HOSTNAMES or hostname.endswith(".local"):
        raise ValueError(f"安全策略禁止访问本地或内网主机：{hostname}")

    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None

    if ip is not None and (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise ValueError(f"安全策略禁止访问本地回环或私有网段地址：{hostname}")

    return url


def build_accept_header(output_format: OutputFormat) -> str:
    """参考 OpenCode webfetch 构建带权重的 Accept 请求头。"""

    if output_format == "markdown":
        return (
            "text/markdown;q=1.0, text/x-markdown;q=0.9, "
            "text/plain;q=0.8, text/html;q=0.7, */*;q=0.1"
        )
    if output_format == "text":
        return "text/plain;q=1.0, text/markdown;q=0.9, text/html;q=0.8, */*;q=0.1"
    return (
        "text/html;q=1.0, application/xhtml+xml;q=0.9, "
        "text/plain;q=0.8, text/markdown;q=0.7, */*;q=0.1"
    )


class _HTMLToTextParser(HTMLParser):
    """对齐 OpenCode extractTextFromHTML，剥离脚本与样式后提取纯文本。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if self._skip_depth > 0 or tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in _BLOCK_TAGS or tag in _HEADING_LEVELS or tag in {"br", "li", "hr"}:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag in _BLOCK_TAGS or tag in _HEADING_LEVELS or tag == "li":
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(data)


def extract_text_from_html(html_text: str) -> str:
    """从 HTML 中提取纯文本内容。"""

    parser = _HTMLToTextParser()
    parser.feed(html_text)
    parser.close()
    raw = "".join(parser._chunks)
    lines = [_INLINE_WS_RE.sub(" ", line).strip() for line in raw.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    return _MULTI_NEWLINES_RE.sub("\n\n", cleaned).strip()


class _HTMLToMarkdownParser(HTMLParser):
    """轻量级 HTML -> Markdown 转换器，保留标题、代码块、列表与链接结构。"""

    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title: str = ""

        self._skip_depth = 0
        self._in_title = False
        self._title_chunks: list[str] = []

        self._in_pre = False
        self._in_code = False

        self._link_href: str | None = None
        self._link_chunks: list[str] = []

        self._out: list[str] = []

    def _append(self, text: str) -> None:
        if self._link_href is not None and not self._in_pre:
            self._link_chunks.append(text)
        else:
            self._out.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth > 0 or tag in _SKIP_TAGS:
            self._skip_depth += 1
            return

        if tag == "title":
            self._in_title = True
            self._title_chunks = []
            return

        if tag in _HEADING_LEVELS:
            level = _HEADING_LEVELS[tag]
            self._out.append(f"\n\n{'#' * level} ")
            return

        if tag == "pre":
            self._in_pre = True
            self._out.append("\n\n```\n")
            return

        if tag == "code":
            self._in_code = True
            if not self._in_pre:
                self._append("`")
            return

        if tag == "a" and not self._in_pre:
            href = ""
            for k, v in attrs:
                if k == "href" and v:
                    href = v.strip()
                    break
            if href and not href.startswith(("javascript:", "mailto:", "#")):
                if self.base_url:
                    href = urljoin(self.base_url, href)
                self._link_href = href
                self._link_chunks = []
            return

        if tag == "li":
            self._out.append("\n- ")
            return

        if tag == "br":
            self._append("\n")
            return

        if tag == "hr":
            self._out.append("\n\n---\n\n")
            return

        if tag in _BLOCK_TAGS:
            self._out.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth > 0:
            self._skip_depth -= 1
            return

        if tag == "title":
            self._in_title = False
            if not self.title:
                self.title = _INLINE_WS_RE.sub(
                    " ", "".join(self._title_chunks)
                ).strip()
            return

        if tag in _HEADING_LEVELS:
            self._out.append("\n\n")
            return

        if tag == "pre":
            self._in_pre = False
            self._out.append("\n```\n\n")
            return

        if tag == "code":
            self._in_code = False
            if not self._in_pre:
                self._append("`")
            return

        if tag == "a" and self._link_href is not None:
            link_text = _INLINE_WS_RE.sub(
                " ", "".join(self._link_chunks)
            ).strip()
            href = self._link_href
            self._link_href = None
            self._link_chunks = []
            if link_text:
                self._out.append(f"[{link_text}]({href})")
            return

        if tag in _BLOCK_TAGS:
            self._out.append("\n\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self._title_chunks.append(data)
            return
        if self._in_pre:
            self._out.append(data)
            return
        normalized = _INLINE_WS_RE.sub(" ", data)
        if normalized:
            self._append(normalized)


def convert_html_to_markdown(html_text: str, base_url: str = "") -> tuple[str, str]:
    """将 HTML 转换为结构化 Markdown，返回 (page_title, markdown_body)。"""

    parser = _HTMLToMarkdownParser(base_url=base_url)
    parser.feed(html_text)
    parser.close()

    raw_md = "".join(parser._out)
    # 逐行整理非代码块中的多余首尾空白
    cleaned_lines: list[str] = []
    in_fence = False
    for line in raw_md.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            cleaned_lines.append(line.strip())
        elif in_fence:
            cleaned_lines.append(line.rstrip())
        else:
            cleaned_lines.append(line.strip())

    markdown = "\n".join(cleaned_lines)
    markdown = _MULTI_NEWLINES_RE.sub("\n\n", markdown).strip()
    return parser.title, markdown


async def _default_page_fetcher(
    url: str,
    output_format: OutputFormat,
    timeout_seconds: float,
) -> FetchedPageResponse:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/143.0.0.0 Safari/537.36"
        ),
        "Accept": build_accept_header(output_format),
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        follow_redirects=True,
    ) as client:
        response = await client.get(url, headers=headers)
        # 对齐 OpenCode webfetch：若遇到 Cloudflare 403 challenge，使用诚实 UA 重试一次
        if (
            response.status_code == 403
            and response.headers.get("cf-mitigated") == "challenge"
        ):
            retry_headers = {**headers, "User-Agent": "agent-service/0.1"}
            response = await client.get(url, headers=retry_headers)

        response.raise_for_status()

        # 校验最终重定向落点，防止通过 302 跳转到内网地址绕过 SSRF 检查
        final_url = str(response.url)
        validate_public_http_url(final_url)

        content_length = response.headers.get("content-length")
        if (
            content_length
            and content_length.isdigit()
            and int(content_length) > MAX_RESPONSE_BYTES
        ):
            raise ValueError("网页响应体积过大（超过 5MB 上限）。")

        raw_bytes = response.content
        if len(raw_bytes) > MAX_RESPONSE_BYTES:
            raise ValueError("网页响应体积过大（超过 5MB 上限）。")

        content_type = response.headers.get("content-type", "")
        return FetchedPageResponse(
            url=final_url,
            status_code=response.status_code,
            content_type=content_type,
            text=response.text,
        )


class FetchWebPageTool(Tool):
    """参考 OpenCode webfetch 设计的网页深度阅读工具（支持 markdown / text / html 格式）。"""

    name = "fetch_web_page"
    description = (
        "抓取指定 URL 的网页内容并转换为易于阅读的格式（默认为结构化 Markdown，保留标题、代码块与链接）。"
        "当通过 `web_search` 发现某条结果（如官方文档、GitHub Issue、StackOverflow）可能包含答案时，"
        "主动调用此工具深入阅读完整正文。此工具为只读工具，不会修改任何本地文件。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "url": {
                "type": "STRING",
                "description": "要抓取的完整网页地址（必须以 http:// 或 https:// 开头）",
            },
            "format": {
                "type": "STRING",
                "enum": ["markdown", "text", "html"],
                "description": "返回内容格式：'markdown'（默认，推荐阅读代码与文档）、'text'（纯文本）、'html'（原始 HTML）",
            },
            "max_chars": {
                "type": "INTEGER",
                "description": f"返回正文的最大字符数（默认 {DEFAULT_MAX_CHARS}，上限 {MAX_ALLOWED_CHARS}）",
            },
            "timeout": {
                "type": "INTEGER",
                "description": "请求超时时间（秒，默认 30，最大 120）",
            },
        },
        "required": ["url"],
    }
    read_only = True

    def __init__(self, fetcher: PageFetcher | None = None) -> None:
        self._fetcher = fetcher or _default_page_fetcher

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        raw_url = parameter.get("url", "")
        try:
            url = validate_public_http_url(raw_url)
        except ValueError as exc:
            return ToolResult(content=f"抓取网页被拒绝：{exc}", is_error=True)

        raw_format = parameter.get("format", "markdown")
        fmt_str = (
            raw_format.strip().lower()
            if isinstance(raw_format, str) and raw_format.strip()
            else "markdown"
        )
        if fmt_str not in VALID_FORMATS:
            return ToolResult(
                content=(
                    f"抓取网页失败：不支持的 format='{fmt_str}'，"
                    "仅支持 'markdown'、'text' 或 'html'。"
                ),
                is_error=True,
            )
        output_format: OutputFormat = fmt_str  # type: ignore[assignment]

        raw_max_chars = parameter.get("max_chars", DEFAULT_MAX_CHARS)
        if isinstance(raw_max_chars, bool) or not isinstance(raw_max_chars, int):
            return ToolResult(
                content="抓取网页失败：max_chars 必须是正整数。",
                is_error=True,
            )
        max_chars = max(500, min(raw_max_chars, MAX_ALLOWED_CHARS))

        raw_timeout = parameter.get("timeout", int(DEFAULT_TIMEOUT_SECONDS))
        if isinstance(raw_timeout, bool) or not isinstance(
            raw_timeout, (int, float)
        ):
            return ToolResult(
                content="抓取网页失败：timeout 必须是正数。",
                is_error=True,
            )
        timeout_seconds = float(
            max(1.0, min(float(raw_timeout), MAX_TIMEOUT_SECONDS))
        )

        try:
            fetched = await self._fetcher(url, output_format, timeout_seconds)
        except ValueError as exc:
            return ToolResult(content=f"抓取网页被拒绝：{exc}", is_error=True)
        except httpx.TimeoutException:
            return ToolResult(
                content=f"抓取网页超时（>{timeout_seconds:g}s）：`{url}`",
                is_error=True,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            return ToolResult(
                content=f"抓取网页失败：HTTP {status_code}（`{url}`）",
                is_error=True,
            )
        except (httpx.HTTPError, OSError) as exc:
            return ToolResult(
                content=f"抓取网页网络请求出错：{exc}",
                is_error=True,
            )

        content_type = fetched.content_type.lower()
        is_html = "text/html" in content_type or "application/xhtml+xml" in content_type
        page_title = ""

        if output_format == "markdown":
            if is_html:
                page_title, body = convert_html_to_markdown(
                    fetched.text,
                    base_url=fetched.url,
                )
            else:
                body = fetched.text.strip()
        elif output_format == "text":
            if is_html:
                body = extract_text_from_html(fetched.text)
            else:
                body = fetched.text.strip()
        else:
            body = fetched.text.strip()

        if not body:
            return ToolResult(
                content=f"网页 `{fetched.url}` 内容为空或未能提取到可读文本。",
                is_error=False,
            )

        total_chars = len(body)
        truncated = False
        if total_chars > max_chars:
            body = body[:max_chars]
            truncated = True

        header_lines = [f"### 🌐 网页内容：`{fetched.url}`"]
        if page_title:
            header_lines.append(f"- **标题**：{page_title}")
        header_lines.append(
            f"- **类型**：`{fetched.content_type or 'unknown'}` | **格式**：`{output_format}`"
        )
        header_lines.append("")
        header_lines.append(body)
        if truncated:
            header_lines.append(
                f"\n\n...[网页内容已按 max_chars={max_chars} 截断，原文共 {total_chars} 字符]"
            )

        return ToolResult(
            content="\n".join(header_lines),
            is_error=False,
        )
