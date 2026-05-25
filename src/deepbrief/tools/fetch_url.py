"""FetchURLTool — fetch a URL and return readable text content.

Uses httpx + readability-lxml to strip nav/ads/boilerplate from HTML pages.
Always returns at most ~6000 characters to bound prompt size.

"""

from __future__ import annotations

import httpx
from readability import Document

from deepbrief.tools.base import BaseTool, ToolResult


MAX_CONTENT_CHARS = 6000


class FetchURLTool(BaseTool):
    name = "fetch_url"
    description = (
        "Fetch a URL and return its main readable text content (article body, "
        "stripped of nav/ads/footers). Use this AFTER web_search when you've "
        "found a URL whose content you want to read in detail. "
        "Do NOT call this for arbitrary URLs the user gives — only URLs you "
        "got from web_search results."
    )
    parameters_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "Absolute https:// URL"},
        },
        "required": ["url"],
        "additionalProperties": False,
    }

    async def execute(self, url: str) -> ToolResult:
        if not url.startswith(("http://", "https://")):
            return ToolResult(
                tool_name=self.name,
                input_args={"url": url},
                success=False,
                error="URL must start with http:// or https://",
            )
        try:
            async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
                resp = await c.get(url, headers={"User-Agent": "DeepBriefBot/0.1"})
                resp.raise_for_status()
        except Exception as e:
            return ToolResult(
                tool_name=self.name,
                input_args={"url": url},
                success=False,
                error=f"Fetch failed: {e}",
            )

        try:
            doc = Document(resp.text)
            title = doc.short_title()
            # Document.summary() returns HTML; we'll do a quick text extraction
            from bs4 import BeautifulSoup

            text = BeautifulSoup(doc.summary(), "lxml").get_text(separator="\n").strip()
        except Exception:
            # Fallback: strip tags from raw HTML
            from bs4 import BeautifulSoup

            text = BeautifulSoup(resp.text, "lxml").get_text(separator="\n").strip()
            title = url

        truncated = len(text) > MAX_CONTENT_CHARS
        if truncated:
            text = text[:MAX_CONTENT_CHARS] + "\n... [truncated]"

        return ToolResult(
            tool_name=self.name,
            input_args={"url": url},
            output={"title": title, "content": text, "truncated": truncated, "source_url": url},
            success=True,
        )
