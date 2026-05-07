"""End-to-end pipeline: topic → coordinator → researchers (A2A) → editor (HITL) → brief.md.

Usage:
    python -m deepbrief.run "State of WebGPU adoption in 2026"

Approval mode:
    --auto-approve     skip the HITL gate (CI / smoke tests)
    --cli              prompt on stdin (default if interactive terminal)
    --queue-only       create the approval request and exit; another process
                       (or you, via the notebook) decides later

Assumes researchers are already running on localhost:9001 and 9002.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from deepbrief.agents.coordinator import Coordinator
from deepbrief.agents.editor import ApprovalStore


BRIEFS_DIR = Path("./data/briefs")


async def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser()
    parser.add_argument("topic", nargs="+", help="Research topic (quoted)")
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--cli", action="store_true")
    parser.add_argument("--queue-only", action="store_true")
    parser.add_argument("--researchers", default="http://localhost:9001,http://localhost:9002")
    args = parser.parse_args()

    topic = " ".join(args.topic)
    urls = [u.strip() for u in args.researchers.split(",") if u.strip()]

    print(f"📝 Topic: {topic}\n")

    # 1. Coordinate
    coordinator = Coordinator(researcher_urls=urls)
    print("🧠 Decomposing topic...")
    draft = await coordinator.run(topic)

    print(f"\n✅ Draft ready ({len(draft.markdown)} chars)")
    print("─" * 60)
    print(draft.markdown)
    print("─" * 60)

    # 2. HITL editor gate
    store = ApprovalStore()
    req = store.create(topic=topic, draft_markdown=draft.markdown)
    print(f"\n📬 Approval request: {req.request_id}")

    if args.auto_approve:
        store.decide(req.request_id, "approved", decided_by="auto")
        decision = store.get(req.request_id)
    elif args.queue_only:
        print("⏸  Queued for review. Decide later via the notebook or:")
        print(f"   python -m deepbrief.editor decide {req.request_id} approve")
        return
    else:
        decision = _cli_decide(store, req.request_id)

    if decision.state == "rejected":
        print("❌ Brief rejected. Nothing saved.")
        return

    # 3. Save the final brief
    BRIEFS_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower()).strip("-")[:50]
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_path = BRIEFS_DIR / f"{ts}-{slug}.md"
    out_path.write_text(decision.final_markdown or decision.draft_markdown)
    print(f"\n📄 Saved: {out_path}")


def _cli_decide(store: ApprovalStore, request_id: str):
    """Prompt on stdin for approval. Blocks until decided."""
    print("\n[a]pprove  [r]eject  [e]dit  > ", end="", flush=True)
    choice = sys.stdin.readline().strip().lower()
    if choice.startswith("a"):
        return store.decide(request_id, "approved", decided_by="cli")
    if choice.startswith("r"):
        return store.decide(request_id, "rejected", decided_by="cli")
    if choice.startswith("e"):
        print("Paste the edited Markdown, end with a line containing only 'EOF':")
        lines = []
        for line in sys.stdin:
            if line.strip() == "EOF":
                break
            lines.append(line)
        return store.decide(request_id, "edited", decided_by="cli", final_markdown="".join(lines))
    return store.decide(request_id, "rejected", decided_by="cli", notes="invalid choice")


if __name__ == "__main__":
    asyncio.run(main())
