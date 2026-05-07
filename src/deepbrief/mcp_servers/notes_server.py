"""notes_server — a minimal FastMCP server that stores research notes locally.

Demonstrates the **server side** of MCP. This is what an agent (the *client*)
will call when it wants to persist findings during research.

Run it directly:
    python -m deepbrief.mcp_servers.notes_server          # stdio (default)
    python -m deepbrief.mcp_servers.notes_server --http   # Streamable HTTP

Inspect it:
    npx @modelcontextprotocol/inspector python -m deepbrief.mcp_servers.notes_server

Tools exposed:
    - save_note(title, content)      → note_id
    - list_notes()                   → list of {id, title, created_at}
    - read_note(note_id)             → {title, content, created_at}
    - delete_note(note_id)           → {ok: bool}

Lecture reference: S7 §5 (Build an MCP server).
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Where notes get persisted. Override via env var if you want.
NOTES_DIR = Path(os.environ.get("DEEPBRIEF_DATA_DIR", "./data")) / "notes"
NOTES_DIR.mkdir(parents=True, exist_ok=True)


mcp = FastMCP(
    "deepbrief-notes",
    instructions=(
        "Store and retrieve research notes for an in-progress brief. "
        "Each note has a title and freeform Markdown content."
    ),
)


@mcp.tool()
def save_note(title: str, content: str) -> dict:
    """Save a research note to local storage.

    Call this whenever you want to persist a finding from web research so a
    later agent (synthesizer) can read it. Each call creates a new note —
    do not call multiple times to update the same note; pick a fresh title.

    Args:
        title: Short title for the note (under 80 chars).
        content: The note body in Markdown. Include source URLs as
            inline links — the synthesizer relies on these for citations.

    Returns:
        Dict with `note_id` (use this for read_note / delete_note).
    """
    note_id = uuid.uuid4().hex[:12]
    payload = {
        "id": note_id,
        "title": title.strip(),
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (NOTES_DIR / f"{note_id}.json").write_text(json.dumps(payload, indent=2))
    return {"note_id": note_id, "title": payload["title"]}


@mcp.tool()
def list_notes() -> list[dict]:
    """List all saved notes (newest first).

    Returns lightweight metadata — id, title, created_at. Does not include
    note content; call read_note to fetch a specific one.
    """
    notes = []
    for path in sorted(NOTES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        data = json.loads(path.read_text())
        notes.append({"id": data["id"], "title": data["title"], "created_at": data["created_at"]})
    return notes


@mcp.tool()
def read_note(note_id: str) -> dict:
    """Read the full content of a note by its id.

    Args:
        note_id: The 12-char hex id returned by save_note or list_notes.

    Returns:
        Dict with title, content, created_at — or {"error": "..."} if not found.
    """
    path = NOTES_DIR / f"{note_id}.json"
    if not path.exists():
        return {"error": f"Note '{note_id}' not found"}
    return json.loads(path.read_text())


@mcp.tool()
def delete_note(note_id: str) -> dict:
    """Delete a note by id. Idempotent — deleting a non-existent note is a no-op."""
    path = NOTES_DIR / f"{note_id}.json"
    existed = path.exists()
    if existed:
        path.unlink()
    return {"ok": True, "existed": existed}


def main() -> None:
    """Entry point. Default transport is stdio; pass --http for Streamable HTTP."""
    if "--http" in sys.argv:
        port = int(os.environ.get("MCP_PORT", "8765"))
        mcp.settings.host = "0.0.0.0"
        mcp.settings.port = port
        mcp.run(transport="streamable-http")
    else:
        mcp.run()  # stdio (default)


if __name__ == "__main__":
    main()
