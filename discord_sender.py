"""Discord Bridge - Send clarify questions to Discord via REST API.

Sends messages to Discord using the bot token from .env, without needing
a persistent WebSocket connection. Creates a thread per CLI session so
multiple sessions can run concurrently — each session's questions and
responses stay in their own thread.
"""

from __future__ import annotations

import json
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _load_env():
    """Load DISCORD_BOT_TOKEN and DISCORD_HOME_CHANNEL from .env file."""
    env_path = Path.home() / ".hermes" / ".env"
    token = os.getenv("DISCORD_BOT_TOKEN", "")
    channel = os.getenv("DISCORD_HOME_CHANNEL", "")

    if not token or not channel:
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


def _discord_api_request(method: str, url: str, token: str, data: dict | None = None):
    """Make a Discord API request using urllib (no external deps)."""
    import urllib.request
    import urllib.error

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
        "User-Agent": "Hermes-DiscordBridge/1.1",
    }
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        logger.error("Discord API error %d: %s", e.code, error_body[:200])
        return None
    except Exception as e:
        logger.error("Discord API request failed: %s", e)
        return None


def create_thread(session_id: str, channel_id: str, token: str) -> dict | None:
    """Create a Discord thread in the home channel for a session.

    Returns the thread object from Discord API, or None on failure.
    Thread name is like "Hermes — abc123" (last 6 chars of session_id).
    """
    short_id = session_id.split("_")[-1][:6] if "_" in session_id else session_id[:6]
    thread_name = f"Hermes — {short_id}"

    url = f"https://discord.com/api/v10/channels/{channel_id}/threads"
    data = {
        "name": thread_name,
        "type": 11,  # PUBLIC_THREAD
        "auto_archive_duration": 60,  # 1 hour
    }

    result = _discord_api_request("POST", url, token, data)
    if result and "id" in result:
        logger.info("Created Discord thread %s for session %s", result["id"], session_id)
        return result

    logger.warning("Failed to create thread for session %s: %s", session_id, result)
    return None


def send_question_to_discord(
    question: str,
    choices: list | None = None,
    q_id: str = "",
    thread_id: str | None = None,
) -> str | None:
    """Send a clarify question to Discord. Returns the message ID or None.

    If thread_id is provided, sends to that thread. Otherwise sends to the
    home channel directly (fallback for sessions without a thread yet).
    """
    token, channel_id = _load_env()
    if not token or not channel_id:
        logger.warning("Discord bridge: missing DISCORD_BOT_TOKEN or DISCORD_HOME_CHANNEL")
        return None

    # Build message content
    lines = ["**Hermes needs your approval:**", ""]
    lines.append(question)
    if choices:
        lines.append("")
        for i, choice in enumerate(choices, 1):
            lines.append(f"  {i}. {choice}")
        lines.append("")
        lines.append("Reply with the number or the text of your choice.")
    else:
        lines.append("")
        lines.append("Reply with your answer.")

    if q_id:
        lines.append(f"`[bridge:{q_id}]`")

    content = "\n".join(lines)

    # Discord message limit is 2000 chars
    if len(content) > 1950:
        content = content[:1950] + "\n..."

    # Send to thread if available, otherwise to home channel
    target_id = thread_id or channel_id
    url = f"https://discord.com/api/v10/channels/{target_id}/messages"
    result = _discord_api_request("POST", url, token, {"content": content})

    if result and "id" in result:
        logger.info("Discord bridge: sent question %s as message %s (thread=%s)",
                     q_id, result["id"], thread_id)
        return result["id"]
    return None


def send_ack_to_discord(response_text: str, thread_id: str | None = None):
    """Send a brief acknowledgment to Discord after receiving a bridge response."""
    token, channel_id = _load_env()
    if not token or not channel_id:
        return

    content = f"**Got it!** Continuing in CLI session... ({response_text[:50]})"
    target_id = thread_id or channel_id
    url = f"https://discord.com/api/v10/channels/{target_id}/messages"
    _discord_api_request("POST", url, token, {"content": content})


def send_deactivation_notice(session_id: str, thread_id: str | None = None):
    """Send a message to Discord when bridge mode is deactivated for a session."""
    token, channel_id = _load_env()
    if not token or not channel_id:
        return

    short_id = session_id.split("_")[-1][:6] if "_" in session_id else session_id[:6]
    content = f"**Bridge deactivated** for session `{short_id}`. No more questions will be sent here."
    target_id = thread_id or channel_id
    url = f"https://discord.com/api/v10/channels/{target_id}/messages"
    _discord_api_request("POST", url, token, {"content": content})


def send_message_to_thread(
    message: str,
    thread_id: str | None = None,
    channel_id: str | None = None,
) -> dict:
    """Send an arbitrary message to a Discord thread or channel.

    If thread_id is provided, sends to that thread. Otherwise sends to
    channel_id (or the home channel from env as fallback).

    Returns {"success": bool, "message_id": str | None, "error": str | None}.
    """
    token, home_channel = _load_env()
    if not token:
        return {"success": False, "message_id": None, "error": "DISCORD_BOT_TOKEN not found"}

    target_id = thread_id or channel_id or home_channel
    if not target_id:
        return {"success": False, "message_id": None, "error": "No channel or thread ID provided"}

    url = f"https://discord.com/api/v10/channels/{target_id}/messages"
    result = _discord_api_request("POST", url, token, {"content": message})

    if result and "id" in result:
        logger.info("Discord bridge: sent message %s to thread %s", result["id"], thread_id)
        return {"success": True, "message_id": result["id"], "error": None}

    error_msg = result.get("message", str(result)) if result else "Unknown error"
    logger.warning("Discord bridge: failed to send message to %s: %s", target_id, error_msg)
    return {"success": False, "message_id": None, "error": error_msg}


def test_connection() -> tuple[bool, str]:
    """Test Discord bot connection. Returns (success, message)."""
    token, channel_id = _load_env()
    if not token:
        return False, "DISCORD_BOT_TOKEN not found in .env"
    if not channel_id:
        return False, "DISCORD_HOME_CHANNEL not found in .env"

    # Test by getting channel info
    url = f"https://discord.com/api/v10/channels/{channel_id}"
    result = _discord_api_request("GET", url, token)

    if result and "id" in result:
        channel_name = result.get("name", channel_id)
        return True, f"Connected to channel: #{channel_name}"
    elif result and "code" in result:
        return False, f"Discord API error: {result.get('message', result['code'])}"
    else:
        return False, "Failed to connect to Discord API"
