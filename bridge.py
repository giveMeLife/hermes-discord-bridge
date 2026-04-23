"""Discord Bridge - Bridge file management.

Manages a shared JSONL file that acts as a mailbox between the CLI session
and the Discord gateway. Questions are written by the CLI plugin, responses
are written by the gateway hook.
"""

import json
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

BRIDGE_DIR = Path.home() / ".hermes"
BRIDGE_FILE = BRIDGE_DIR / "discord_bridge.jsonl"
BRIDGE_MODE_FILE = BRIDGE_DIR / "discord_bridge_mode"


# ---------------------------------------------------------------------------
# Bridge mode flag (on/off)
# ---------------------------------------------------------------------------

def is_bridge_active() -> bool:
    """Check if Discord bridge mode is currently active."""
    return BRIDGE_MODE_FILE.exists()


def activate_bridge():
    """Activate Discord bridge mode."""
    BRIDGE_MODE_FILE.write_text(datetime.now(timezone.utc).isoformat())


def deactivate_bridge():
    """Deactivate Discord bridge mode."""
    try:
        BRIDGE_MODE_FILE.unlink()
    except FileNotFoundError:
        pass


def get_bridge_mode_since() -> str | None:
    """Return the timestamp when bridge was activated, or None."""
    try:
        return BRIDGE_MODE_FILE.read_text().strip()
    except FileNotFoundError:
        return None


# ---------------------------------------------------------------------------
# Bridge file operations
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


def get_pending_questions(max_age_seconds: int = 300) -> list[dict]:
    """Get all pending questions younger than max_age_seconds."""
    ensure_bridge_file()
    cutoff = time.time() - max_age_seconds
    pending = []
    for entry in _read_all_entries():
        if entry.get("status") != "pending":
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
