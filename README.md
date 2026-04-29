# Hermes Discord Bridge Plugin

Send `clarify` questions from your Hermes CLI session to Discord, so you can
approve from your phone while away from the terminal. Also supports sending
arbitrary messages (progress updates, results) to your session's Discord thread.

## How it works

1. You're working in the terminal with Hermes
2. You run `/bridge on` (or tell Hermes "preguntame por discord")
3. When the agent needs approval via `clarify`:
   - The question appears in the terminal (as always)
   - It's ALSO sent to a dedicated Discord thread for your session
   - The plugin polls for a Discord response in the background
4. You reply from Discord (phone) or the terminal
5. The first response that arrives wins
6. When you're back: `/bridge off` or "ya volví"

The agent can also send messages to your thread via `discord_send` for progress
updates while you're away.

## Requirements

- **Hermes Agent** with the clarify hooks (fork or PR #14602)
- `DISCORD_BOT_TOKEN` and `DISCORD_HOME_CHANNEL` set in `~/.hermes/.env`
- `hermes gateway` running

## Installation

### Step 1 — Clone and copy the plugin

```bash
git clone https://github.com/giveMeLife/hermes-discord-bridge.git
cp -r hermes-discord-bridge/ ~/.hermes/plugins/discord-bridge/
```

### Step 2 — Install the gateway hook

```bash
mkdir -p ~/.hermes/hooks
cp -r hermes-discord-bridge/gateway_hook/ ~/.hermes/hooks/discord-bridge/
```

### Step 3 — Configure `~/.hermes/config.yaml`

**Both** entries are required. The plugin won't load if either is missing.

```yaml
# 1. Enable the plugin
plugins:
  enabled:
    - discord-bridge

# 2. Add the toolset to your platform (cli, telegram, etc.)
platform_toolsets:
  cli:
    - hermes-cli
    - discord_bridge    # <-- add this line
```

> **Common mistake:** only adding `plugins.enabled` but not `platform_toolsets`.
> The plugin loads but the `discord_send` tool won't be available to the agent.

### Step 4 — Set Discord credentials

Add to `~/.hermes/.env`:

```
DISCORD_BOT_TOKEN=your-bot-token-here
DISCORD_HOME_CHANNEL=your-channel-id-here
```

### Step 5 — Restart

```bash
# Restart the gateway (loads the hook)
hermes gateway

# Start a new CLI session (loads the plugin + toolset)
hermes
```

### Verify it works

```bash
# In the CLI:
/bridge status        # Should say "OFF (no active sessions)"
/bridge on            # Should say "ON (pending)"
# Now when a clarify fires, it goes to Discord
```

## Usage

| Command | Action |
|---------|--------|
| `/bridge on` | Activate Discord bridge |
| `/bridge off` | Deactivate Discord bridge |
| `/bridge status` | Check current status |

Or in natural language:
- "voy al baño, preguntame por discord" → activates bridge
- "ya volví" → deactivates bridge

### Sending progress updates

When bridge mode is active, the agent can send messages to your Discord thread:

```
discord_send(message="Step 2 done: 47 files processed")
```

The tool automatically routes to the current session's thread. If no thread
exists yet (bridge was activated but no clarify fired), it creates one
automatically.

## Architecture

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│  CLI Session │────▶│  Bridge File     │◀────│   Gateway   │
│  (plugin)    │     │  (JSONL mailbox) │     │  (hook)     │
│              │     │                  │     │             │
│  on_clarify  │     │  ~/.hermes/      │     │  gateway:   │
│  hook fires  │     │  discord_bridge  │     │  message_   │
│  → writes Q  │     │  .jsonl          │     │  received   │
│  → sends to  │     │                  │     │  → writes R │
│    Discord   │     │                  │     │             │
│              │     │                  │     │             │
│  poller reads│◀────│  response entry  │─────│  Discord    │
│  response    │     │                  │     │  message    │
│  → puts in   │     │                  │     │  captured   │
│  resp queue  │     │                  │     │             │
└─────────────┘     └──────────────────┘     └─────────────┘
```

### Components

| File | Role |
|------|------|
| `__init__.py` | Plugin entry point (`register()`), hook handlers, `/bridge` command, `discord_send` tool |
| `bridge.py` | Bridge file management (JSONL mailbox between CLI and gateway) |
| `discord_sender.py` | Send messages to Discord via REST API (no external deps) |
| `gateway_hook/` | Gateway hook that intercepts Discord messages as bridge responses |

## Troubleshooting

**Plugin loads but `discord_send` tool is not available**
- Check that `discord_bridge` is in `platform_toolsets.cli` in `~/.hermes/config.yaml`
- Restart the CLI after changing config

**`/bridge on` says "Cannot connect to Discord"**
- Check `DISCORD_BOT_TOKEN` and `DISCORD_HOME_CHANNEL` in `~/.hermes/.env`
- Make sure `hermes gateway` is running

**Questions don't appear in Discord**
- Verify the gateway hook is installed: `ls ~/.hermes/hooks/discord-bridge/`
- Check the plugin is enabled: `hermes plugins list`

**Discord responses don't reach the CLI**
- Make sure `hermes gateway` is running
- Check gateway logs for `[hooks] Loaded hook 'discord-bridge'`

## Uninstallation

```bash
hermes plugins disable discord-bridge
rm -rf ~/.hermes/plugins/discord-bridge/
rm -rf ~/.hermes/hooks/discord-bridge/
rm -f ~/.hermes/discord_bridge.jsonl
rm -f ~/.hermes/discord_bridge_sessions.json
```

Also remove `discord_bridge` from `platform_toolsets.cli` in `~/.hermes/config.yaml`.

## License

MIT
