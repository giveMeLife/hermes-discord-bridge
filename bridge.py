"""Discord Bridge - Bridge file and session management.

Manages a shared JSONL file that acts as a mailbox between CLI sessions
and the Discord gateway, plus a sessions registry that tracks which
sessions have bridge mode active and their associated Discord thread IDs.

Supports multiple concurrent sessions — each gets its own Discord thread.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

BRIDGE_DIR = Path.home() / ".hermes"
BRIDGE_FILE = BRIDGE_DIR / "discord_bridge.jsonl"
SESSIONS_FILE = BRIDGE_DIR / "discord_bridge_sessions.json"

# Legacy single-session mode file (kept for migration)
LEGACY_MODE_FILE = BRIDGE_DIR / "discord_bridge_mode"


# ---------------------------------------------------------------------------
# Session registry (multi-session bridge mode)
# ---------------------------------------------------------------------------

def _read_sessions() -> dict:
    """Read the sessions registry. Returns {session_id: {active_since, thread_id, thread_name}}."""
    if not SESSIONS_FILE.exists():
        return {}
    try:
        return json.loads(SESSIONS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _write_sessions(sessions: dict):
    """Write the sessions registry."""
    SESSIONS_FILE.write_text(json.dumps(sessions, indent=2, ensure_ascii=False))


def is_bridge_active(session_id: str | None = None) -> bool:
    """Check if bridge mode is active for a given session (or any session if None)."""
    sessions = _read_sessions()
    if session_id:
        return session_id in sessions
    return len(sessions) > 0


def activate_session(session_id: str) -> dict:
    """Activate bridge mode for a session. Returns the session entry."""
    sessions = _read_sessions()
    entry = sessions.get(session_id, {})
    entry["active_since"] = datetime.now(timezone.utc).isoformat()
    entry.setdefault("thread_id", None)
    entry.setdefault("thread_name", None)
    sessions[session_id] = entry
    _write_sessions(sessions)
    return entry


def deactivate_session(session_id: str) -> dict | None:
    """Deactivate bridge mode for a session. Returns the removed entry or None."""
    sessions = _read_sessions()
    entry = sessions.pop(session_id, None)
    _write_sessions(sessions)
    return entry


def set_session_thread(session_id: str, thread_id: str, thread_name: str):
    """Store the Discord thread ID associated with a session."""
    sessions = _read_sessions()
    if session_id in sessions:
        sessions[session_id]["thread_id"] = thread_id
        sessions[session_id]["thread_name"] = thread_name
        _write_sessions(sessions)


def get_session_thread(session_id: str) -> str | None:
    """Get the Discord thread ID for a session, or None."""
    sessions = _read_sessions()
    return sessions.get(session_id, {}).get("thread_id")


def clear_session_thread(session_id: str):
    """Remove the Discord thread ID for a session (e.g. when thread was deleted)."""
    sessions = _read_sessions()
    if session_id in sessions:
        sessions[session_id]["thread_id"] = None
        sessions[session_id]["thread_name"] = None
        _write_sessions(sessions)


def get_session_by_thread(thread_id: str) -> str | None:
    """Get the session_id associated with a Discord thread, or None."""
    sessions = _read_sessions()
    for sid, entry in sessions.items():
        if str(entry.get("thread_id")) == str(thread_id):
            return sid
    return None


def get_active_sessions() -> dict:
    """Return all active sessions (session_id -> entry)."""
    return _read_sessions()


def get_session_since(session_id: str) -> str | None:
    """Return the timestamp when bridge was activated for a session, or None."""
    sessions = _read_sessions()
    return sessions.get(session_id, {}).get("active_since")


def cleanup_old_sessions(max_age_hours: int = 24):
    """Remove sessions older than max_age_hours from the registry."""
    sessions = _read_sessions()
    cutoff = time.time() - (max_age_hours * 3600)
    kept = {}
    for sid, entry in sessions.items():
        since = entry.get("active_since", "")
        if not since:
            continue
        try:
            dt = datetime.fromisoformat(since)
            if dt.timestamp() > cutoff:
                kept[sid] = entry
        except (ValueError, OSError):
            continue
    _write_sessions(kept)


# ---------------------------------------------------------------------------
# Legacy single-session mode (migrated on first call)
# ---------------------------------------------------------------------------

def _migrate_legacy_mode():
    """One-time migration: if the old discord_bridge_mode file exists,
    convert it to a session entry so existing users aren't surprised."""
    if not LEGACY_MODE_FILE.exists():
        return
    try:
        since = LEGACY_MODE_FILE.read_text().strip()
        sessions = _read_sessions()
        # Only migrate if there's no active sessions yet
        if not sessions:
            sessions["legacy"] = {
                "active_since": since,
                "thread_id": None,
                "thread_name": None,
            }
            _write_sessions(sessions)
        LEGACY_MODE_FILE.unlink()
    except Exception:
        pass


# Run migration on import
_migrate_legacy_mode()


# ---------------------------------------------------------------------------
# Bridge file operations (JSONL mailbox)
# ---------------------------------------------------------------------------

def ensure_bridge_file():
    """Create bridge file if it doesn't exist."""
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    if not BRIDGE_FILE.exists():
        BRIDGE_FILE.touch()


def write_question(session_id: str, question: str, choices: list | None = None) -> str:
    """Write a pending question to the bridge file. Returns the question ID."""
    ensure_bridge_file()
    q_id = f"q_{uuid.uuid4().hex[:8]}"
    entry = {
        "id": q_id,
        "type": "question",
        "session_id": session_id,
        "question": question,
        "choices": choices or [],
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "responded_at": None,
        "response": None,
        "source": None,
    }
    with open(BRIDGE_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return q_id


def write_response(q_id: str, response: str, source: str = "discord"):
    """Write a response to a pending question in the bridge file."""
    ensure_bridge_file()
    entries = _read_all_entries()
    for entry in entries:
        if entry.get("id") == q_id and entry.get("type") == "question":
            entry["status"] = "responded"
            entry["response"] = response
            entry["source"] = source
            entry["responded_at"] = datetime.now(timezone.utc).isoformat()
            break
    _write_all_entries(entries)


def check_response(q_id: str) -> str | None:
    """Check if a response has been received for a question. Returns response or None."""
    ensure_bridge_file()
    for entry in _read_all_entries():
        if entry.get("id") == q_id and entry.get("status") == "responded":
            return entry.get("response")
    return None


def get_pending_questions(session_id: str | None = None, max_age_seconds: int = 300) -> list[dict]:
    """Get all pending questions, optionally filtered by session_id, younger than max_age_seconds."""
    ensure_bridge_file()
    cutoff = time.time() - max_age_seconds
    pending = []
    for entry in _read_all_entries():
        if entry.get("status") != "pending":
            continue
        if session_id and entry.get("session_id") != session_id:
            continue
        created = entry.get("created_at", "")
        if not created:
            continue
        try:
            dt = datetime.fromisoformat(created)
            if dt.timestamp() > cutoff:
                pending.append(entry)
        except (ValueError, OSError):
            continue
    return pending


def mark_resolved(q_id: str):
    """Mark a question as resolved (used when CLI gets keyboard response first)."""
    ensure_bridge_file()
    entries = _read_all_entries()
    for entry in entries:
        if entry.get("id") == q_id and entry.get("status") == "pending":
            entry["status"] = "resolved"
            entry["responded_at"] = datetime.now(timezone.utc).isoformat()
            entry["source"] = "cli_keyboard"
            break
    _write_all_entries(entries)


def cleanup_old_entries(max_age_hours: int = 24):
    """Remove entries older than max_age_hours."""
    ensure_bridge_file()
    cutoff = time.time() - (max_age_hours * 3600)
    kept = []
    for entry in _read_all_entries():
        created = entry.get("created_at", "")
        if not created:
            continue
        try:
            dt = datetime.fromisoformat(created)
            if dt.timestamp() > cutoff:
                kept.append(entry)
        except (ValueError, OSError):
            continue
    _write_all_entries(kept)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_all_entries() -> list[dict]:
    """Read all entries from the bridge file."""
    entries = []
    if not BRIDGE_FILE.exists():
        return entries
    with open(BRIDGE_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _write_all_entries(entries: list[dict]):
    """Write all entries back to the bridge file."""
    with open(BRIDGE_FILE, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
