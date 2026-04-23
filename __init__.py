"""Discord Bridge plugin for Hermes Agent.

Allows clarify questions from CLI sessions to be sent to Discord so the
user can respond from their phone. When the user returns to the terminal,
bridge mode can be deactivated.

Requires the upstream PR adding on_clarify / on_clarify_response /
gateway:message_received hooks — or a Hermes build that includes them.

Installation:
    1. Copy this directory to ~/.hermes/plugins/discord-bridge/
    2. Run: hermes plugins enable discord-bridge
    3. Install the gateway hook (see gateway_hook/ below)
    4. Make sure DISCORD_BOT_TOKEN and DISCORD_HOME_CHANNEL are set in ~/.hermes/.env
    5. Make sure `hermes gateway` is running

Usage:
    /bridge on      — Activate Discord bridge
    /bridge off     — Deactivate Discord bridge
    /bridge status  — Check current status
    Or just tell Hermes: "voy al baño, preguntame por discord"
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .bridge import (
    is_bridge_active,
    activate_bridge,
    deactivate_bridge,
    get_bridge_mode_since,
    write_question,
    write_response,
    check_response,
    mark_resolved,
    get_pending_questions,
    cleanup_old_entries,
)
from .discord_sender import (
    send_question_to_discord,
    send_ack_to_discord,
    test_connection,
)

logger = logging.getLogger(__name__)

# Track active polling threads so we can clean up
_active_pollers: Dict[str, threading.Thread] = {}


def register(ctx):
    """Plugin entry point — called by Hermes plugin loader."""
    ctx.register_hook("on_clarify", _on_clarify)
    ctx.register_hook("on_clarify_response", _on_clarify_response)
    ctx.register_command("bridge", _bridge_command,
                         "Toggle Discord bridge for remote approval")


# ---------------------------------------------------------------------------
# Hook handlers
# ---------------------------------------------------------------------------

def _on_clarify(question: str, choices: list, session_id: str,
                response_queue, timeout: int, **kwargs):
    """Handle on_clarify hook — send question to Discord if bridge is active."""
    if not is_bridge_active():
        return

    # Clean up old bridge entries periodically
    try:
        cleanup_old_entries(max_age_hours=1)
    except Exception:
        pass

    # Write question to bridge file and send to Discord
    try:
        q_id = write_question(session_id, question, choices)
        msg_id = send_question_to_discord(question, choices, q_id)
        logger.info("Bridge: sent question %s to Discord (msg %s)", q_id, msg_id)
    except Exception as e:
        logger.warning("Bridge: failed to send question to Discord: %s", e)
        return

    # Start a background thread that polls the bridge file for a response
    # and injects it into the response_queue when found
    poller = threading.Thread(
        target=_poll_bridge_response,
        args=(q_id, question, choices, response_queue, session_id, timeout),
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
            # Find the latest pending question for this session and mark it
            pending = get_pending_questions(max_age_seconds=300)
            for q in reversed(pending):
                if q.get("session_id") == session_id and q.get("status") == "pending":
                    mark_resolved(q["id"])
                    break
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Bridge response polling
# ---------------------------------------------------------------------------

def _poll_bridge_response(q_id: str, question: str, choices: list,
                          response_queue, session_id: str, timeout: int):
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
                    send_ack_to_discord(resp)
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
    """Handle /bridge command — toggle Discord bridge mode."""
    parts = raw_args.strip().split()
    subcmd = parts[0] if parts else "status"

    if subcmd in ("on", "activate", "enable"):
        # Test Discord connection first
        ok, msg = test_connection()
        if ok:
            activate_bridge()
            return f"Discord bridge: ON\n{msg}\nClarify questions will also be sent to Discord.\nSay \"ya volví\" or /bridge off to disable."
        else:
            return f"Cannot connect to Discord: {msg}\nMake sure DISCORD_BOT_TOKEN and DISCORD_HOME_CHANNEL are set in ~/.hermes/.env"

    elif subcmd in ("off", "deactivate", "disable"):
        deactivate_bridge()
        return "Discord bridge: OFF\nClarify questions will only appear in terminal."

    elif subcmd == "status":
        if is_bridge_active():
            since = get_bridge_mode_since() or "?"
            return f"Discord bridge: ON (since {since})"
        else:
            return "Discord bridge: OFF"

    else:
        return "Usage: /bridge [on|off|status]"
