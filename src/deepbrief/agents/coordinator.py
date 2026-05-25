"""Coordinator agent — decomposes a topic and delegates to researchers via A2A.

Pipeline:
  1. LLM call: turn the topic into 3-5 sub-questions
  2. Fan out: call each researcher's A2A endpoint in parallel via `tasks/send`
  3. LLM call: synthesize the responses into a draft brief

The coordinator does NOT use ReAct internally — its decisions are deterministic
(decompose → fan out → synthesize). This is the *bounded autonomy* pattern from
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass

from openai import AsyncOpenAI

from deepbrief.a2a.client import send_task


# NOTE: JSON braces are doubled ({{ and }}) because this string goes through
# str.format(). Single { / } would be interpreted as format placeholders and
# raise KeyError on the JSON keywords. This is a real footgun caught in
# notebook 07 production runs.
DECOMPOSE_PROMPT = """You decompose research topics into focused sub-questions.

Given a TOPIC, output JSON:
{{
  "subquestions": [
    "<focused question 1>",
    "<focused question 2>",
    ...
  ]
}}

Rules:
- 3 to 5 sub-questions
- Each is independently researchable (a researcher gets only that one question, no other context)
- Cover different facets (definition, current state, examples, criticism, future)
- Do NOT include the topic verbatim — break it down

TOPIC: {topic}
"""


SYNTHESIZE_PROMPT = """You merge research notes into a single Markdown brief.

You will receive a TOPIC and N research notes. Each note has a SUMMARY and SOURCES.

Output Markdown with:
- # <Title>
- 2-4 short sections with ## headings
- Inline citations like [1], [2] referring to a SOURCES section at the bottom
- Be concise — under 400 words total

TOPIC: {topic}

NOTES:
{notes}
"""


@dataclass
class BriefDraft:
    topic: str
    subquestions: list[str]
    notes: list[str]
    markdown: str


class Coordinator:
    def __init__(
        self,
        researcher_urls: list[str],
        model: str | None = None,
        client: AsyncOpenAI | None = None,
    ) -> None:
        self.researcher_urls = researcher_urls
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.client = client or AsyncOpenAI()

    async def decompose(self, topic: str) -> list[str]:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": DECOMPOSE_PROMPT.format(topic=topic)}],
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        return data["subquestions"][: len(self.researcher_urls)]  # cap to available researchers

    async def dispatch(self, subquestions: list[str]) -> list[str]:
        """Round-robin sub-questions across researchers; run all in parallel."""
        async def _one(url: str, question: str) -> str:
            try:
                return await send_task(url, question)
            except Exception as e:
                return f"SUMMARY: [researcher failed: {e}]\n\nSOURCES: -"

        tasks = [
            _one(self.researcher_urls[i % len(self.researcher_urls)], q)
            for i, q in enumerate(subquestions)
        ]
        return await asyncio.gather(*tasks)

    async def synthesize(self, topic: str, notes: list[str]) -> str:
        joined = "\n\n---\n\n".join(f"## Note {i+1}\n{n}" for i, n in enumerate(notes))
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": SYNTHESIZE_PROMPT.format(topic=topic, notes=joined)}],
        )
        return resp.choices[0].message.content or ""

    async def run(self, topic: str) -> BriefDraft:
        subqs = await self.decompose(topic)
        notes = await self.dispatch(subqs)
        markdown = await self.synthesize(topic, notes)
        return BriefDraft(topic=topic, subquestions=subqs, notes=notes, markdown=markdown)
