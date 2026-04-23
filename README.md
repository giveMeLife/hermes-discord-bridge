# Hermes Discord Bridge Plugin

Send `clarify` questions from your Hermes CLI session to Discord, so you can approve from your phone while away from the terminal.

## How it works

1. You're working in the terminal with Hermes
2. You run `/bridge on` (or tell Hermes "preguntame por discord")
3. When the agent needs approval via `clarify`:
   - The question appears in the terminal (as always)
   - It's ALSO sent to your Discord channel
   - The plugin polls for a Discord response in the background
4. You reply from Discord (phone) or the terminal
5. The first response that arrives wins
6. When you're back: `/bridge off` or "ya volví"

## Requirements

- **Hermes Agent** with the clarify hooks applied (see below)
- `DISCORD_BOT_TOKEN` and `DISCORD_HOME_CHANNEL` set in `~/.hermes/.env`
- `hermes gateway` running for the Discord side

### Clarify Hooks

This plugin requires three new hook points that are not yet merged into Hermes upstream:

| Hook | Type | Purpose |
|------|------|---------|
| `on_clarify` | Plugin hook | Fired when clarify prompt is shown; plugins can inject responses |
| `on_clarify_response` | Plugin hook | Fired when user responds or clarify times out |
| `gateway:message_received` | Gateway hook | Fired for authorized messages before processing |

**Upstream PR:** [NousResearch/hermes-agent#14602](https://github.com/NousResearch/hermes-agent/pull/14602)

Until the PR is merged, you need a Hermes build that includes these hooks. Options:

1. **Use the fork** — Clone [giveMeLife/hermes-agent](https://github.com/giveMeLife/hermes-agent) branch `feat/clarity-bridge-hooks`
2. **Cherry-pick** — Apply the commit from the PR on top of your Hermes checkout
3. **Wait for merge** — Once the PR is merged into main, any standard Hermes update will include the hooks

## Installation

### 1. Install the plugin

```bash
# Clone this repo
git clone https://github.com/giveMeLife/hermes-discord-bridge.git

# Copy to Hermes plugins directory
cp -r hermes-discord-bridge/ ~/.hermes/plugins/discord-bridge/

# Enable the plugin
hermes plugins enable discord-bridge
```

Or manually add `discord-bridge` to the `plugins.enabled` list in `~/.hermes/config.yaml`:

```yaml
plugins:
  enabled:
    - discord-bridge
```

### 2. Install the gateway hook

The gateway hook intercepts Discord messages that are responses to pending bridge questions:

```bash
mkdir -p ~/.hermes/hooks
cp -r hermes-discord-bridge/gateway_hook/ ~/.hermes/hooks/discord-bridge/
```

### 3. Configure Discord credentials

Add to `~/.hermes/.env`:

```
DISCORD_BOT_TOKEN=your-bot-token-here
DISCORD_HOME_CHANNEL=your-channel-id-here
```

### 4. Restart Hermes

```bash
# Restart the gateway to load the gateway hook
hermes gateway

# Start a new CLI session — the plugin will auto-load
hermes
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

## Troubleshooting

**`/bridge on` says "Cannot connect to Discord"**
- Check that `DISCORD_BOT_TOKEN` and `DISCORD_HOME_CHANNEL` are set in `~/.hermes/.env`
- Make sure `hermes gateway` is running (the bot needs to be online for the REST API to work)

**Questions don't appear in Discord**
- Verify the gateway hook is installed: `ls ~/.hermes/hooks/discord-bridge/`
- Check the plugin is enabled: `hermes plugins list`

**Discord responses don't reach the CLI**
- Make sure `hermes gateway` is running
- Check that the gateway hook loaded: look for `[hooks] Loaded hook 'discord-bridge'` in gateway logs
- The bridge file at `~/.hermes/discord_bridge.jsonl` should have entries with `"status": "responded"`

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
