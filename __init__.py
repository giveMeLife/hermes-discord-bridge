"""Discord Bridge plugin for Hermes Agent.

Allows clarify questions from CLI sessions to be sent to Discord so the
user can respond from their phone. Each CLI session gets its own Discord
thread, so multiple sessions can run concurrently without mixing up.

Requires the upstream PR adding on_clarify / on_clarify_response /
gateway:message_received hooks — or a Hermes build that includes them.

Installation:
    1. Copy this directory to ~/.hermes/plugins/discord-bridge/
    2. Run: hermes plugins enable discord-bridge
    3. Install the gateway hook (see gateway_hook/ below)
    4. Make sure DISCORD_BOT_TOKEN and DISCORD_HOME_CHANNEL are set in ~/.hermes/.env
    5. Make sure `hermes gateway` is running

Usage:
    /bridge on      — Activate Discord bridge (applies to next clarify in this CLI)
    /bridge off     — Deactivate Discord bridge for all sessions
    /bridge status  — Check current status (all active sessions)
    Or just tell Hermes: "voy al baño, preguntame por discord"
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .bridge import (
    BRIDGE_DIR,
    is_bridge_active,
    activate_session,
    deactivate_session,
    set_session_thread,
    get_session_thread,
    get_session_by_thread,
    get_active_sessions,
    get_session_since,
    write_question,
    write_response,
    check_response,
    mark_resolved,
    get_pending_questions,
    cleanup_old_entries,
    cleanup_old_sessions,
)
from .discord_sender import (
    create_thread,
    send_question_to_discord,
    send_ack_to_discord,
    send_deactivation_notice,
    send_message_to_thread,
    test_connection,
)

logger = logging.getLogger(__name__)

# Track active polling threads so we can clean up
_active_pollers: Dict[str, threading.Thread] = {}

# Global "bridge requested" flag file — set by /bridge on, consumed by on_clarify
# This solves the problem that slash commands don't have access to session_id
_BRIDGE_REQUESTED_FILE = BRIDGE_DIR / "discord_bridge_requested"


def register(ctx):
    """Plugin entry point — called by Hermes plugin loader."""
    ctx.register_hook("on_clarify", _on_clarify)
    ctx.register_hook("on_clarify_response", _on_clarify_response)
    ctx.register_command("bridge", _bridge_command,
                         "Toggle Discord bridge for remote approval")
    ctx.register_tool(
        name="discord_send",
        toolset="discord_bridge",
        schema=_DISCORD_SEND_SCHEMA,
        handler=lambda args, **kw: discord_send(**args, **kw),
        check_fn=lambda: True,
        description="Send a message to the active Discord thread.",
    )


# ---------------------------------------------------------------------------
# Hook handlers
# ---------------------------------------------------------------------------

def _on_clarify(question: str, choices: list, session_id: str,
                response_queue, timeout: int, **kwargs):
    """Handle on_clarify hook — send question to Discord if bridge is active for this session."""
    # Check if bridge was requested via /bridge on (global flag)
    # If so, auto-activate this session and consume the flag
    if _is_bridge_requested():
        _consume_bridge_request()
        activate_session(session_id)
        logger.info("Bridge: auto-activated session %s from /bridge on request", session_id)

    if not is_bridge_active(session_id):
        return

    # Clean up old bridge entries and stale sessions periodically
    try:
        cleanup_old_entries(max_age_hours=1)
        cleanup_old_sessions(max_age_hours=24)
    except Exception:
        pass

    # Ensure this session has a Discord thread
    thread_id = _ensure_session_thread(session_id)

    # Write question to bridge file and send to Discord
    try:
        q_id = write_question(session_id, question, choices)
        msg_id = send_question_to_discord(question, choices, q_id, thread_id=thread_id)
        logger.info("Bridge: sent question %s to Discord thread %s (msg %s)",
                     q_id, thread_id, msg_id)
    except Exception as e:
        logger.warning("Bridge: failed to send question to Discord: %s", e)
        return

    # Start a background thread that polls the bridge file for a response
    # and injects it into the response_queue when found
    poller = threading.Thread(
        target=_poll_bridge_response,
        args=(q_id, question, choices, response_queue, session_id, timeout, thread_id),
        daemon=True,
        name=f"bridge-poll-{q_id}",
    )
    _active_pollers[q_id] = poller
    poller.start()


def _on_clarify_response(question: str, choices: list, response: Optional[str],
                         source: str, session_id: str, **kwargs):
    """Handle on_clarify_response hook — mark bridge question as resolved."""
    # If the keyboard response came first, mark the bridge question as resolved
    # so the gateway hook doesn't intercept a stale Discord reply.
    if source == "keyboard":
        try:
            pending = get_pending_questions(session_id=session_id, max_age_seconds=300)
            for q in reversed(pending):
                if q.get("session_id") == session_id and q.get("status") == "pending":
                    mark_resolved(q["id"])
                    break
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Bridge request flag (solves /bridge on not knowing session_id)
# ---------------------------------------------------------------------------

def _is_bridge_requested() -> bool:
    """Check if /bridge on was called but hasn't been consumed yet."""
    return _BRIDGE_REQUESTED_FILE.exists()


def _consume_bridge_request():
    """Remove the bridge request flag (consumed by on_clarify)."""
    try:
        _BRIDGE_REQUESTED_FILE.unlink()
    except FileNotFoundError:
        pass


def _set_bridge_request():
    """Set the bridge request flag (called by /bridge on)."""
    from datetime import datetime, timezone
    _BRIDGE_REQUESTED_FILE.write_text(datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Thread management
# ---------------------------------------------------------------------------

def _ensure_session_thread(session_id: str) -> str | None:
    """Make sure this session has a Discord thread. Creates one if needed.

    Returns the thread_id, or None if thread creation failed.
    """
    # Check if we already have a thread for this session
    existing = get_session_thread(session_id)
    if existing:
        return existing

    # Create a new thread
    token, channel_id = _load_discord_env()
    if not token or not channel_id:
        logger.warning("Cannot create thread: missing Discord credentials")
        return None

    thread = create_thread(session_id, channel_id, token)
    if thread and "id" in thread:
        set_session_thread(session_id, thread["id"], thread.get("name", ""))
        return thread["id"]

    logger.warning("Failed to create Discord thread for session %s", session_id)
    return None


def _load_discord_env() -> tuple[str, str]:
    """Load Discord env vars from .env if not in os.environ."""
    import os
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    channel = os.getenv("DISCORD_HOME_CHANNEL", "")
    if token and channel:
        return token, channel

    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key == "DISCORD_BOT_TOKEN" and not token:
                token = value
            elif key == "DISCORD_HOME_CHANNEL" and not channel:
                channel = value

    return token, channel


# ---------------------------------------------------------------------------
# Bridge response polling
# ---------------------------------------------------------------------------

def _poll_bridge_response(q_id: str, question: str, choices: list,
                          response_queue, session_id: str, timeout: int,
                          thread_id: str | None = None):
    """Poll the bridge file for a Discord response and inject it into the queue."""
    deadline = time.monotonic() + min(timeout, 300)  # Cap at 5 minutes

    while time.monotonic() < deadline:
        try:
            resp = check_response(q_id)
            if resp is not None:
                # Got a response from Discord — inject it into the queue
                response_queue.put(resp)
                # Send acknowledgment to Discord
                try:
                    send_ack_to_discord(resp, thread_id=thread_id)
                except Exception:
                    pass
                logger.info("Bridge: received Discord response for %s", q_id)
                _active_pollers.pop(q_id, None)
                return
        except Exception as e:
            logger.debug("Bridge: poll error for %s: %s", q_id, e)

        time.sleep(1)

    # Timed out — clean up
    _active_pollers.pop(q_id, None)
    logger.debug("Bridge: poll timed out for %s", q_id)


# ---------------------------------------------------------------------------
# /bridge slash command
# ---------------------------------------------------------------------------

def _bridge_command(raw_args: str) -> str:
    """Handle /bridge command — toggle Discord bridge mode.

    Since slash commands don't have access to the session_id, /bridge on
    sets a flag file that gets consumed by the next on_clarify call,
    which does have the session_id.
    """
    parts = raw_args.strip().split()
    subcmd = parts[0] if parts else "status"

    if subcmd in ("on", "activate", "enable"):
        # Test Discord connection first
        ok, msg = test_connection()
        if ok:
            _set_bridge_request()
            return (f"Discord bridge: ON (pending)\n{msg}\n"
                    f"Bridge will activate on the next clarify question.\n"
                    f"Say \"ya volví\" or /bridge off to disable.")
        else:
            return (f"Cannot connect to Discord: {msg}\n"
                    f"Make sure DISCORD_BOT_TOKEN and DISCORD_HOME_CHANNEL "
                    f"are set in ~/.hermes/.env")

    elif subcmd in ("off", "deactivate", "disable"):
        # Deactivate ALL sessions (since we can't target a specific one)
        sessions = get_active_sessions()
        for sid, entry in list(sessions.items()):
            thread_id = entry.get("thread_id")
            try:
                send_deactivation_notice(sid, thread_id=thread_id)
            except Exception:
                pass
            deactivate_session(sid)
        # Also remove any pending request
        if _is_bridge_requested():
            _consume_bridge_request()
        return "Discord bridge: OFF\nAll sessions deactivated. Clarify questions will only appear in terminal."

    elif subcmd == "status":
        # Also check for pending request
        if _is_bridge_requested():
            return "Discord bridge: PENDING (requested via /bridge on, waiting for first clarify)"
        sessions = get_active_sessions()
        if not sessions:
            return "Discord bridge: OFF (no active sessions)"
        lines = ["Discord bridge: ACTIVE SESSIONS"]
        for sid, entry in sessions.items():
            since = entry.get("active_since", "?")
            thread = entry.get("thread_name") or entry.get("thread_id") or "no thread"
            short_id = sid.split("_")[-1][:6] if "_" in sid else sid[:6]
            lines.append(f"  — {short_id} (since {since[:19]}, thread: {thread})")
        return "\n".join(lines)

    else:
        return "Usage: /bridge [on|off|status]"


# ---------------------------------------------------------------------------
# Tool: discord_send
# ---------------------------------------------------------------------------

_DISCORD_SEND_SCHEMA = {
    "type": "function",
    "function": {
        "name": "discord_send",
        "description": (
            "Send a message to the active Discord bridge thread. "
            "Useful for notifying the user of progress, results, or status updates "
            "when they are away from the terminal but watching Discord. "
            "If no session_id is provided, the most recently active session is used."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message text to send to Discord. Keep it concise.",
                },
                "session_id": {
                    "type": "string",
                    "description": (
                        "Optional session ID. If omitted, uses the most recently "
                        "active session's Discord thread."
                    ),
                },
            },
            "required": ["message"],
        },
    },
}


def discord_send(message: str, session_id: str | None = None, **kwargs) -> str:
    """Send a message to the Discord thread of an active bridge session.

    Returns a JSON string with success status, message_id, and any error.
    """
    import json

    # Resolve session_id if not provided
    if not session_id:
        sessions = get_active_sessions()
        if not sessions:
            return json.dumps({
                "success": False,
                "error": "No active bridge sessions. Ask the user to run '/bridge on' first.",
            })
        # Use the most recently active session
        session_id = max(sessions.keys(), key=lambda sid: sessions[sid].get("active_since", ""))

    # Get the thread for this session
    thread_id = get_session_thread(session_id)
    if not thread_id:
        return json.dumps({
            "success": False,
            "error": f"Session {session_id[:6]} has no Discord thread.",
        })

    # Send the message
    result = send_message_to_thread(message, thread_id=thread_id)
    return json.dumps(result)
