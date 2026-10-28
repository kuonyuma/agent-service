"""FetchWebPageTool 的单元测试。"""

import httpx
import pytest

from agent_service.tools.fetch_web_page import (
    FetchedPageResponse,
    FetchWebPageTool,
    OutputFormat,
    build_accept_header,
    convert_html_to_markdown,
    extract_text_from_html,
    validate_public_http_url,
)

SAMPLE_ARTICLE_HTML = """
<!DOCTYPE html>
<html>
<head>
  <title>StackOverflow - Asyncio Gather Example</title>
  <style>body { color: black; }</style>
  <script>console.log("tracking");</script>
</head>
<body>
  <nav><a href="/home">Home Navigation</a></nav>
  <main>
    <h1>How to run coroutines concurrently?</h1>
    <p>Use <code>asyncio.gather</code> to run tasks in parallel. See <a href="/docs/asyncio">official docs</a>.</p>
    <pre><code>import asyncio

async def main():
    await asyncio.gather(task1(), task2())
</code></pre>
    <ul>
      <li>First item</li>
      <li>Second item</li>
    </ul>
  </main>
  <footer>Footer copyright</footer>
</body>
</html>
"""


def test_fetch_web_page_tool_metadata():
    tool = FetchWebPageTool()

    assert tool.name == "fetch_web_page"
    assert tool.read_only is True
    assert "url" in tool.input_schema["required"]
    assert tool.to_function_declaration().name == "fetch_web_page"


@pytest.mark.parametrize(
    "blocked_url",
    [
        "",
        "ftp://example.com/file.txt",
        "file:///etc/passwd",
        "http://localhost:8000/runs",
        "http://127.0.0.1:3307/",
        "http://10.0.0.1/admin",
        "http://172.16.0.5/internal",
        "http://192.168.1.1/router",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]:8000/",
        "https://service.local/api",
    ],
)
def test_validate_public_http_url_blocks_ssrf_and_invalid_schemes(blocked_url: str):
    with pytest.raises(ValueError):
        validate_public_http_url(blocked_url)


def test_validate_public_http_url_allows_public_https():
    url = "https://docs.python.org/3/library/asyncio.html"
    assert validate_public_http_url(url) == url


def test_build_accept_header_matches_opencode_priorities():
    assert build_accept_header("markdown").startswith("text/markdown;q=1.0")
    assert build_accept_header("text").startswith("text/plain;q=1.0")
    assert build_accept_header("html").startswith("text/html;q=1.0")


def test_convert_html_to_markdown_preserves_headings_code_and_strips_noise():
    title, md = convert_html_to_markdown(
        SAMPLE_ARTICLE_HTML,
        base_url="https://stackoverflow.com/questions/1",
    )

    assert title == "StackOverflow - Asyncio Gather Example"
    assert "# How to run coroutines concurrently?" in md
    assert "`asyncio.gather`" in md
    assert "[official docs](https://stackoverflow.com/docs/asyncio)" in md
    assert "```\nimport asyncio" in md
    assert "- First item" in md
    assert "tracking" not in md
    assert "Home Navigation" not in md
    assert "Footer copyright" not in md


def test_extract_text_from_html_strips_scripts_and_tags():
    text = extract_text_from_html(SAMPLE_ARTICLE_HTML)

    assert "How to run coroutines concurrently?" in text
    assert "tracking" not in text
    assert "color: black" not in text


@pytest.mark.asyncio
async def test_fetch_web_page_markdown_and_truncation():
    async def fake_fetcher(
        url: str,
        output_format: OutputFormat,
        timeout_seconds: float,
    ) -> FetchedPageResponse:
        assert url == "https://stackoverflow.com/questions/1"
        assert output_format == "markdown"
        assert timeout_seconds == 30.0
        return FetchedPageResponse(
            url=url,
            status_code=200,
            content_type="text/html; charset=utf-8",
            text=SAMPLE_ARTICLE_HTML,
        )

    tool = FetchWebPageTool(fetcher=fake_fetcher)
    result = await tool.run({"url": "https://stackoverflow.com/questions/1"})

    assert not result.is_error
    assert "StackOverflow - Asyncio Gather Example" in result.content
    assert "# How to run coroutines concurrently?" in result.content
    assert "await asyncio.gather(task1(), task2())" in result.content


@pytest.mark.asyncio
async def test_fetch_web_page_truncates_when_exceeding_max_chars():
    long_body = "<p>" + ("A" * 1200) + "</p>"

    async def fake_fetcher(
        url: str,
        output_format: OutputFormat,
        timeout_seconds: float,
    ) -> FetchedPageResponse:
        return FetchedPageResponse(
            url=url,
            status_code=200,
            content_type="text/html",
            text=long_body,
        )

    tool = FetchWebPageTool(fetcher=fake_fetcher)
    result = await tool.run(
        {"url": "https://example.com/long", "max_chars": 600}
    )

    assert not result.is_error
    assert "网页内容已按 max_chars=600 截断" in result.content


@pytest.mark.asyncio
async def test_fetch_web_page_rejects_ssrf_and_invalid_format():
    tool = FetchWebPageTool()

    ssrf_res = await tool.run({"url": "http://127.0.0.1:8000/health"})
    assert ssrf_res.is_error
    assert "安全策略禁止访问" in ssrf_res.content

    fmt_res = await tool.run(
        {"url": "https://example.com", "format": "pdf"}
    )
    assert fmt_res.is_error
    assert "不支持的 format" in fmt_res.content


@pytest.mark.asyncio
async def test_fetch_web_page_handles_http_errors():
    async def error_fetcher(
        url: str,
        output_format: OutputFormat,
        timeout_seconds: float,
    ) -> FetchedPageResponse:
        request = httpx.Request("GET", url)
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("404 Not Found", request=request, response=response)

    tool = FetchWebPageTool(fetcher=error_fetcher)
    result = await tool.run({"url": "https://example.com/missing"})

    assert result.is_error
    assert "HTTP 404" in result.content
