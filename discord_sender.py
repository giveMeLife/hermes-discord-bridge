"""Discord Bridge - Send clarify questions to Discord via REST API.

Sends messages to Discord using the bot token from .env, without needing
a persistent WebSocket connection. This allows the CLI plugin to send
questions even while the gateway is handling the Discord side.
"""

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


def send_question_to_discord(
    question: str,
    choices: list | None = None,
    q_id: str = "",
) -> str | None:
    """Send a clarify question to Discord. Returns the message ID or None."""
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

    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    result = _discord_api_request("POST", url, token, {"content": content})

    if result and "id" in result:
        logger.info("Discord bridge: sent question %s as message %s", q_id, result["id"])
        return result["id"]
    return None


def send_ack_to_discord(response_text: str):
    """Send a brief acknowledgment to Discord after receiving a bridge response."""
    token, channel_id = _load_env()
    if not token or not channel_id:
        return

    content = f"**Got it!** Continuing in CLI session... ({response_text[:50]})"
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
    _discord_api_request("POST", url, token, {"content": content})


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
