# Hermes Discord Bridge Plugin

Send `clarify` questions from your Hermes CLI session to Discord, so you can approve from your phone while away from the terminal.

## How it works

1. You're working in the terminal with Hermes
2. You say "voy al baño, if you need approval ask me on Discord"
3. You run `/bridge on` (or Hermes activates it from your message)
4. When the agent needs approval via `clarify`:
   - The question appears in the terminal (as always)
   - It's ALSO sent to your Discord channel
   - The plugin polls for a Discord response in the background
5. You reply from Discord (phone) or the terminal
6. The first response that arrives wins
7. When you're back: `/bridge off` or "ya volví"

## Requirements

- **Hermes Agent** with `on_clarify`, `on_clarify_response`, and `gateway:message_received` hooks (PR #14602 or a build that includes them)
- `DISCORD_BOT_TOKEN` and `DISCORD_HOME_CHANNEL` set in `~/.hermes/.env`
- `hermes gateway` running for the Discord side

## Installation

### 1. Install the plugin

```bash
# Copy plugin to Hermes plugins directory
cp -r discord-bridge/ ~/.hermes/plugins/discord-bridge/

# Enable the plugin
hermes plugins enable discord-bridge
```

### 2. Install the gateway hook

```bash
# Copy the gateway hook
cp -r gateway_hook/ ~/.hermes/hooks/discord-bridge/
```

### 3. Configure Discord credentials

Make sure your `~/.hermes/.env` has:
```
DISCORD_BOT_TOKEN=your-bot-token
DISCORD_HOME_CHANNEL=your-channel-id
```

### 4. Restart Hermes

```bash
hermes gateway  # restart the gateway
# then start a new CLI session
```

## Usage

In a CLI session:

| Command | Action |
|---------|--------|
| `/bridge on` | Activate Discord bridge |
| `/bridge off` | Deactivate Discord bridge |
| `/bridge status` | Check current status |

Or just tell Hermes in natural language:
- "voy al baño, preguntame por discord" → activates bridge
- "ya volví" → deactivates bridge

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
| `__init__.py` | Plugin entry point (`register()`), hook handlers, `/bridge` command |
| `bridge.py` | Bridge file management (JSONL mailbox between CLI and gateway) |
| `discord_sender.py` | Send questions to Discord via REST API (no external deps) |
| `gateway_hook/` | Gateway hook that intercepts Discord messages as bridge responses |

### Why no source patching?

Previous versions of this plugin patched `cli.py` and `gateway/run.py` at install time. That approach broke on every Hermes update. This version uses the native plugin system and gateway hooks — zero source modifications, survives updates.

## Uninstallation

```bash
hermes plugins disable discord-bridge
rm -rf ~/.hermes/plugins/discord-bridge/
rm -rf ~/.hermes/hooks/discord-bridge/
rm -f ~/.hermes/discord_bridge.jsonl
rm -f ~/.hermes/discord_bridge_mode
```

## License

MIT
