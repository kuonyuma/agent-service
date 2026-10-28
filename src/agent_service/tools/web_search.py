import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from agent_service.tools.base import Tool, ToolResult

SearchFetcher = Callable[[str, int], Awaitable[str]]

DEFAULT_MAX_RESULTS = 5
MAX_ALLOWED_RESULTS = 10
DEFAULT_SEARCH_TIMEOUT = 20.0
DUCKDUCKGO_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class SearchResultItem:
    """单条网页搜索结果结构。"""

    title: str
    url: str
    snippet: str


def _normalize_ws(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def unwrap_search_url(raw_url: str) -> str:
    """将 DuckDuckGo 的重定向链接（如 //duckduckgo.com/l/?uddg=...）解包为真实目标 URL。"""

    href = raw_url.strip()
    if not href:
        return ""
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        query_params = parse_qs(parsed.query)
        uddg_values = query_params.get("uddg")
        if uddg_values and uddg_values[0]:
            return unquote(uddg_values[0]).strip()
    return href


def _has_class(attrs: list[tuple[str, str | None]], target: str) -> bool:
    for key, value in attrs:
        if key == "class" and value:
            classes = set(value.split())
            if target in classes:
                return True
    return False


def _get_attr(attrs: list[tuple[str, str | None]], name: str) -> str:
    for key, value in attrs:
        if key == name and value is not None:
            return value
    return ""


class _DuckDuckGoHTMLParser(HTMLParser):
    """解析 DuckDuckGo HTML/Lite 搜索结果页，提取标题、URL 与摘要。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[SearchResultItem] = []

        self._in_title_link = False
        self._title_depth = 0
        self._title_chunks: list[str] = []
        self._current_url = ""

        self._in_snippet = False
        self._snippet_depth = 0
        self._snippet_chunks: list[str] = []

        self._pending_title = ""
        self._pending_url = ""

    def _flush_pending(self, snippet: str = "") -> None:
        if not self._pending_title or not self._pending_url:
            return
        url = unwrap_search_url(self._pending_url)
        if url.startswith(("http://", "https://")) and "duckduckgo.com/y.js" not in url:
            self.results.append(
                SearchResultItem(
                    title=self._pending_title,
                    url=url,
                    snippet=snippet,
                )
            )
        self._pending_title = ""
        self._pending_url = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._in_title_link:
            self._title_depth += 1
        elif tag == "a" and (
            _has_class(attrs, "result__a") or _has_class(attrs, "result-link")
        ):
            # 如果上一条结果没有 snippet，先将其落盘
            if self._pending_title and self._pending_url:
                self._flush_pending()
            self._in_title_link = True
            self._title_depth = 1
            self._title_chunks = []
            self._current_url = _get_attr(attrs, "href")
            return

        if self._in_snippet:
            self._snippet_depth += 1
        elif (
            self._pending_title
            and tag in {"a", "div", "td", "span"}
            and (
                _has_class(attrs, "result__snippet")
                or _has_class(attrs, "result-snippet")
            )
        ):
            self._in_snippet = True
            self._snippet_depth = 1
            self._snippet_chunks = []

    def handle_endtag(self, tag: str) -> None:
        if self._in_title_link:
            self._title_depth -= 1
            if self._title_depth <= 0:
                self._in_title_link = False
                self._pending_title = _normalize_ws("".join(self._title_chunks))
                self._pending_url = self._current_url
                self._title_chunks = []
                self._current_url = ""
            return

        if self._in_snippet:
            self._snippet_depth -= 1
            if self._snippet_depth <= 0:
                self._in_snippet = False
                snippet_text = _normalize_ws("".join(self._snippet_chunks))
                self._snippet_chunks = []
                self._flush_pending(snippet_text)

    def handle_data(self, data: str) -> None:
        if self._in_title_link:
            self._title_chunks.append(data)
        elif self._in_snippet:
            self._snippet_chunks.append(data)

    def close(self) -> None:
        super().close()
        if self._pending_title and self._pending_url:
            self._flush_pending()


def parse_duckduckgo_html(
    html_text: str,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> list[SearchResultItem]:
    """从 DuckDuckGo HTML 响应中提取去重后的前 N 条搜索结果。"""

    parser = _DuckDuckGoHTMLParser()
    parser.feed(html_text)
    parser.close()

    deduped: list[SearchResultItem] = []
    seen_urls: set[str] = set()
    for item in parser.results:
        if item.url in seen_urls:
            continue
        seen_urls.add(item.url)
        deduped.append(item)
        if len(deduped) >= max_results:
            break
    return deduped


async def _default_duckduckgo_fetcher(query: str, max_results: int) -> str:
    del max_results
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/143.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    async with httpx.AsyncClient(
        timeout=DEFAULT_SEARCH_TIMEOUT,
        follow_redirects=True,
    ) as client:
        response = await client.post(
            DUCKDUCKGO_HTML_ENDPOINT,
            data={"q": query},
            headers=headers,
        )
        response.raise_for_status()
        return response.text


def format_search_results(
    query: str,
    items: list[SearchResultItem],
) -> str:
    """将搜索结果格式化为高信息密度的结构化 Markdown。"""

    if not items:
        return (
            f"未找到与 `{query}` 相关的网页搜索结果，请尝试更换或精简搜索关键词。"
        )

    lines = [
        f"### 🔍 网页搜索结果：`{query}`（返回 {len(items)} 条）",
        "",
    ]
    for idx, item in enumerate(items, start=1):
        snippet = item.snippet or "（无摘要）"
        lines.append(f"{idx}. **{item.title}**")
        lines.append(f"   - URL: `{item.url}`")
        lines.append(f"   - 摘要: {snippet}")
    lines.append("")
    lines.append(
        "> 提示：若某条结果（如官方文档、GitHub Issue、StackOverflow）可能包含关键细节，"
        "请主动调用 `fetch_web_page(url=...)` 深入阅读完整正文。"
    )
    return "\n".join(lines)


class WebSearchTool(Tool):
    """参考 OpenCode websearch 设计的只读联网搜索工具（返回标题、URL 与简要摘要）。"""

    name = "web_search"
    description = (
        "在互联网上实时搜索信息，返回前 5~10 条相关性最高的结果（包含标题、URL 和简要摘要 Snippet）。"
        f"适用于查询最新技术文档、报错信息、开源库用法或超出知识截止日期的信息（当前年份：{datetime.now(UTC).year}）。"
        "当从搜索结果中发现高价值链接时，应紧接着调用 `fetch_web_page` 深入阅读页面正文。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "OBJECT",
        "properties": {
            "query": {
                "type": "STRING",
                "description": "搜索关键词（建议使用精炼的技术术语、报错原文或带年份的查询词）",
            },
            "max_results": {
                "type": "INTEGER",
                "description": "返回的搜索结果数量（默认 5，范围 1~10）",
            },
        },
        "required": ["query"],
    }
    read_only = True

    def __init__(self, fetcher: SearchFetcher | None = None) -> None:
        self._fetcher = fetcher or _default_duckduckgo_fetcher

    async def run(self, parameter: dict[str, Any]) -> ToolResult:
        raw_query = parameter.get("query", "")
        if not isinstance(raw_query, str) or not raw_query.strip():
            return ToolResult(
                content="网页搜索失败：搜索关键词 query 不能为空。",
                is_error=True,
            )
        query = raw_query.strip()

        raw_max = parameter.get("max_results", DEFAULT_MAX_RESULTS)
        if isinstance(raw_max, bool) or not isinstance(raw_max, int):
            return ToolResult(
                content="网页搜索失败：max_results 必须是 1 到 10 之间的整数。",
                is_error=True,
            )
        max_results = max(1, min(raw_max, MAX_ALLOWED_RESULTS))

        try:
            html_text = await self._fetcher(query, max_results)
        except httpx.TimeoutException:
            return ToolResult(
                content=f"网页搜索超时（>{DEFAULT_SEARCH_TIMEOUT:g}s）：`{query}`，请稍后重试。",
                is_error=True,
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            return ToolResult(
                content=f"网页搜索请求被拒绝或失败（HTTP {status_code}）。",
                is_error=True,
            )
        except (httpx.HTTPError, OSError) as exc:
            return ToolResult(
                content=f"网页搜索网络请求出错：{exc}",
                is_error=True,
            )

        items = parse_duckduckgo_html(html_text, max_results=max_results)
        return ToolResult(
            content=format_search_results(query, items),
            is_error=False,
        )
