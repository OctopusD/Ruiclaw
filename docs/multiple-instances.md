# Multiple Instances

Run multiple ruiclaw instances simultaneously with separate configs and runtime data. Use `--config` as the main entrypoint. Optionally pass `--workspace` during `onboard` when you want to initialize or update the saved workspace for a specific instance.

## Quick Start

If you want each instance to have its own dedicated workspace from the start, pass both `--config` and `--workspace` during onboarding.

**Initialize instances:**

```bash
# Create separate instance configs and workspaces
ruiclaw onboard --config ~/.ruiclaw-telegram/config.json --workspace ~/.ruiclaw-telegram/workspace
ruiclaw onboard --config ~/.ruiclaw-discord/config.json --workspace ~/.ruiclaw-discord/workspace
ruiclaw onboard --config ~/.ruiclaw-feishu/config.json --workspace ~/.ruiclaw-feishu/workspace
```

**Configure each instance:**

Edit `~/.ruiclaw-telegram/config.json`, `~/.ruiclaw-discord/config.json`, etc. with different channel settings. The workspace you passed during `onboard` is saved into each config as that instance's default workspace.

**Run instances:**

```bash
# Check one instance before starting it
ruiclaw status --config ~/.ruiclaw-telegram/config.json

# Instance A - Telegram bot
ruiclaw gateway --config ~/.ruiclaw-telegram/config.json

# Instance B - Discord bot
ruiclaw gateway --config ~/.ruiclaw-discord/config.json

# Instance C - Feishu bot with custom port
ruiclaw gateway --config ~/.ruiclaw-feishu/config.json --port 18792
```

## Path Resolution

When using `--config`, ruiclaw derives its runtime data directory from the config file location. The workspace still comes from `agents.defaults.workspace` unless you override it with `--workspace`.

To open a CLI session against one of these instances locally:

```bash
ruiclaw agent -c ~/.ruiclaw-telegram/config.json -m "Hello from Telegram instance"
ruiclaw agent -c ~/.ruiclaw-discord/config.json -m "Hello from Discord instance"

# Open the browser workbench for a specific instance
ruiclaw webui -c ~/.ruiclaw-telegram/config.json

# Optional one-off workspace override
ruiclaw agent -c ~/.ruiclaw-telegram/config.json -w /tmp/ruiclaw-telegram-test
```

> Interactive `ruiclaw agent` and `ruiclaw webui` commands with the same `--config` and explicit `--workspace` selectors share one gateway instance. Different selectors produce isolated runtime state and processes. The one-shot and `--classic` agent paths remain direct local executions.

| Component | Resolved From | Example |
|-----------|---------------|---------|
| **Config** | `--config` path | `~/.ruiclaw-A/config.json` |
| **Workspace** | `--workspace` or config | `~/.ruiclaw-A/workspace/` |
| **Sessions** | config directory + workspace ID | `~/.ruiclaw-A/sessions/<workspace-id>/` |
| **Cron Jobs** | workspace directory | `~/.ruiclaw-A/workspace/cron/` |
| **Media / runtime state** | config directory | `~/.ruiclaw-A/media/` |

## How It Works

- `--config` selects which config file to load
- By default, the workspace comes from `agents.defaults.workspace` in that config
- If you pass `--workspace`, it overrides the workspace from the config file

## Minimal Setup

1. Copy your base config into a new instance directory.
2. Set a different `agents.defaults.workspace` for that instance.
3. Start the instance with `--config`.

Example config fragment:

```json
{
  "agents": {
    "defaults": {
      "workspace": "~/.ruiclaw-telegram/workspace"
    }
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_TELEGRAM_BOT_TOKEN"
    }
  },
  "gateway": {
    "host": "127.0.0.1",
    "port": 18790
  }
}
```

The copied base config can keep using the same `modelPresets` and `agents.defaults.modelPreset`. If this instance needs a different model, add another preset and set `agents.defaults.modelPreset` to that preset name.

Start separate instances:

```bash
ruiclaw status --config ~/.ruiclaw-telegram/config.json
ruiclaw gateway --config ~/.ruiclaw-telegram/config.json
ruiclaw gateway --config ~/.ruiclaw-discord/config.json
```

Each gateway instance also exposes a lightweight HTTP health endpoint on `gateway.host:gateway.port`. By default, the gateway binds to `127.0.0.1`, so the endpoint stays local unless you explicitly set `gateway.host` to a public or LAN-facing address.

`GET /health` reports process liveness and WebSocket channel readiness:

- Returns `200 OK` when the WebSocket channel is disabled or running.
- Returns `503 Service Unavailable` when the WebSocket channel is enabled but not running.

The JSON response includes `status`, `process`, `ready`, and `websocket`.
For example, with the WebSocket channel disabled:

```json
{"status":"ok","process":"alive","ready":true,"websocket":"disabled"}
```

Readiness currently reflects only the WebSocket channel. A successful response does not verify other chat channels, MCP servers, or model provider connectivity.

Other paths return `404`.

Override workspace for one-off runs when needed:

```bash
ruiclaw gateway --config ~/.ruiclaw-telegram/config.json --workspace /tmp/ruiclaw-telegram-test
```

## Common Use Cases

- Run separate bots for Telegram, Discord, Feishu, and other platforms
- Keep testing and production instances isolated
- Use different models or providers for different teams
- Serve multiple tenants with separate configs and runtime data

## Notes

- Each instance must use a different port if they run at the same time
- Session data follows the active config directory; use a different workspace per instance to isolate memory, skills, and the stable session namespace ID
- `--workspace` overrides the workspace defined in the config file
- Cron jobs are stored in the active workspace; runtime media/state is derived from the config directory
