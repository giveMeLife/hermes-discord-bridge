"""Gateway hook for Discord Bridge plugin.

When the gateway receives a Discord message and bridge mode is active,
this hook checks if the message is a response to a pending CLI bridge
question. If so, it writes the response to the bridge file so the CLI
poller can pick it up.

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

    If bridge mode is active and there are pending questions, check if
    this message is a response to one of them.
    """
    if not _ensure_bridge_imports():
        return

    # Only process Discord messages
    platform = context.get("platform", "")
    if platform != "discord":
        return

    # Check if bridge mode is active
    if not _bridge_module.is_bridge_active():
        return

    # Get pending questions
    pending = _bridge_module.get_pending_questions(max_age_seconds=600)
    if not pending:
        return

    # Take the most recent pending question
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
    logger.info("Discord bridge: captured response for %s: %s", q_id, response[:50])
