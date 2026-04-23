"""Gateway hook for Discord Bridge plugin.

When the gateway receives a Discord message and bridge mode is active,
this hook checks if the message is a response to a pending CLI bridge
question. It matches by Discord thread ID to support multiple concurrent
sessions — each session has its own thread.

Install by copying this directory to ~/.hermes/hooks/discord-bridge/
"""

import importlib.util
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_BRIDGE_IMPORTED = False
_bridge_module = None


def _ensure_bridge_imports():
    """Lazy import bridge module from the plugin directory."""
    global _BRIDGE_IMPORTED, _bridge_module
    if _BRIDGE_IMPORTED:
        return _bridge_module is not None
    _BRIDGE_IMPORTED = True
    try:
        bridge_path = str(Path.home() / ".hermes" / "plugins" / "discord-bridge" / "bridge.py")
        spec = importlib.util.spec_from_file_location("discord_bridge_bridge", bridge_path)
        if spec is None or spec.loader is None:
            logger.debug("Bridge plugin not found at %s", bridge_path)
            return False
        _bridge_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_bridge_module)
        return True
    except Exception as e:
        logger.debug("Discord bridge gateway import failed: %s", e)
        return False


async def handle(event_type, context):
    """Handle gateway:message_received event.

    If bridge mode is active and the message comes from a Discord thread
    associated with an active bridge session, check if it's a response to
    a pending question and write it to the bridge file.
    """
    if not _ensure_bridge_imports():
        return

    # Only process Discord messages
    platform = context.get("platform", "")
    if platform != "discord":
        return

    # Get the thread_id from the context.
    # Discord adapter now passes thread_id explicitly for Thread messages.
    # Fallback to chat_id (which IS the thread ID for messages in threads).
    thread_id = context.get("thread_id") or context.get("channel_id") or context.get("chat_id")

    # Try to find the session associated with this thread
    session_id = _bridge_module.get_session_by_thread(thread_id) if thread_id else None

    if not session_id:
        # Not a bridge thread — could be a regular channel message
        # or a thread not created by the bridge. Skip it.
        return

    # Check if bridge mode is active for this session
    if not _bridge_module.is_bridge_active(session_id):
        return

    # Get pending questions for THIS session only
    pending = _bridge_module.get_pending_questions(session_id=session_id, max_age_seconds=600)
    if not pending:
        return

    # Take the most recent pending question for this session
    latest = pending[-1]
    q_id = latest.get("id", "")
    choices = latest.get("choices", [])

    # The message text is the user's response
    user_text = (context.get("text") or "").strip()
    if not user_text:
        return

    # Try to match numbered choice responses
    response = user_text
    if choices and user_text.isdigit():
        idx = int(user_text) - 1
        if 0 <= idx < len(choices):
            response = choices[idx]

    # Write the response to the bridge file
    _bridge_module.write_response(q_id, response, source="discord")
    logger.info("Discord bridge: captured response for %s (session %s, thread %s): %s",
                q_id, session_id, thread_id, response[:50])
