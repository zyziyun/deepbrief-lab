"""Tests for the MCP servers — notes_server (file-backed) and cache_server (in-memory).

These exercise the *tool functions* directly as plain Python. The MCP protocol
plumbing (JSON-RPC over stdio/HTTP) is FastMCP's responsibility and is already
covered by the SDK's own tests; we don't need to re-test it here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deepbrief.mcp_servers import cache_server
from deepbrief.mcp_servers import notes_server


# ─────────────────────────────────────────────────────────────────────────────
# notes_server — file-backed notes
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def notes_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect notes_server's storage to a tmp_path for isolated tests."""
    target = tmp_path / "notes"
    target.mkdir()
    monkeypatch.setattr(notes_server, "NOTES_DIR", target)
    return target


class TestNotesServer:
    def test_save_then_read(self, notes_dir: Path):
        saved = notes_server.save_note(title="Hello", content="# Body\n")
        note_id = saved["note_id"]
        assert saved["title"] == "Hello"

        read = notes_server.read_note(note_id)
        assert read["title"] == "Hello"
        assert read["content"] == "# Body\n"
        assert "created_at" in read

    def test_list_returns_lightweight_metadata(self, notes_dir: Path):
        a = notes_server.save_note(title="A", content="aaa")
        b = notes_server.save_note(title="B", content="bbb")

        listing = notes_server.list_notes()
        ids = {n["id"] for n in listing}
        assert a["note_id"] in ids
        assert b["note_id"] in ids
        # list_notes excludes content
        assert all("content" not in n for n in listing)

    def test_read_unknown_id_returns_error(self, notes_dir: Path):
        result = notes_server.read_note("doesnotexist")
        assert "error" in result

    def test_delete_is_idempotent(self, notes_dir: Path):
        saved = notes_server.save_note(title="X", content="x")
        first = notes_server.delete_note(saved["note_id"])
        second = notes_server.delete_note(saved["note_id"])
        assert first == {"ok": True, "existed": True}
        assert second == {"ok": True, "existed": False}

    def test_titles_are_stripped(self, notes_dir: Path):
        saved = notes_server.save_note(title="  Spaced  ", content="x")
        read = notes_server.read_note(saved["note_id"])
        assert read["title"] == "Spaced"


# ─────────────────────────────────────────────────────────────────────────────
# cache_server — in-memory KV
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_cache(monkeypatch: pytest.MonkeyPatch):
    """cache_server holds module-level state; reset between tests."""
    monkeypatch.setattr(cache_server, "_CACHE", {})
    monkeypatch.setattr(cache_server, "_STATS", {"hits": 0, "misses": 0})


class TestCacheServer:
    def test_get_missing_is_miss(self):
        result = cache_server.cache_get(key="https://example.com")
        assert result == {"hit": False, "value": None}

    def test_set_then_hit(self):
        cache_server.cache_set(key="k", value="v")
        result = cache_server.cache_get(key="k")
        assert result == {"hit": True, "value": "v"}

    def test_stats_counts_correctly(self):
        cache_server.cache_get(key="m")          # miss
        cache_server.cache_set(key="k", value="v")
        cache_server.cache_get(key="k")          # hit
        cache_server.cache_get(key="k")          # hit
        stats = cache_server.cache_stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == pytest.approx(2 / 3, rel=0.01)
        assert stats["size"] == 1

    def test_clear_resets_everything(self):
        cache_server.cache_set(key="a", value="1")
        cache_server.cache_get(key="a")
        result = cache_server.cache_clear()
        assert result["cleared"] == 1
        stats = cache_server.cache_stats()
        assert stats == {"size": 0, "hits": 0, "misses": 0, "hit_rate": 0.0}
