# Build a Discord AI Agent with ruiclaw

This guide connects ruiclaw to Discord so a Discord user or server channel can
talk to your self-hosted AI agent through the ruiclaw gateway.

## What this guide builds

- a Discord bot application
- Message Content intent enabled
- the `discord` channel enabled in ruiclaw
- one direct message or mention test

## Prerequisites

- A working local ruiclaw reply:

```bash
ruiclaw agent -m "Hello!"
```

- Access to the Discord Developer Portal.
- A Discord server where you can invite a bot.

## Install ruiclaw

```bash
python -m pip install ruiclaw-ai
ruiclaw onboard --wizard
```

## Enable the Discord channel

Install the optional channel dependency:

```bash
ruiclaw plugins enable discord
```

Create a Discord application, add a bot, copy the token, and enable
`MESSAGE CONTENT INTENT` in the bot settings.

Merge this snippet into `~/.ruiclaw/config.json`:

```json
{
  "channels": {
    "discord": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowChannels": [],
      "groupPolicy": "mention",
      "streaming": true
    }
  }
}
```

Omitting `allowFrom` enables pairing-only mode. A new user should DM the bot
first, get a pairing code, and be approved before using the bot in servers.

Invite the bot with permissions to read history and send messages.

## Run ruiclaw gateway

```bash
ruiclaw channels status
ruiclaw gateway
```

## Test a message

Send the bot a DM first. It should return a pairing code. Approve it from a
trusted local surface:

```bash
ruiclaw agent -m "/pairing approve ABCD-EFGH"
```

After approval, mention it in an allowed server channel:

```text
@your-bot Hello from Discord
```

## Security notes

- Keep `groupPolicy` as `mention` for first deployment.
- Use `allowChannels` for server channels where the bot should operate.
- Prefer pairing-only mode for user access; add `allowFrom` only when you want a
  static allowlist.
- Avoid open group behavior in busy channels until session routing is clear.
- Review tool access before inviting the bot into shared servers.

## Troubleshooting

- If no messages arrive, confirm Message Content intent is enabled.
- If a DM returns a pairing code, approve it before testing normal replies.
- If server messages are ignored, check pairing approval, `allowChannels`, and
  whether the bot was mentioned.
- If the bot cannot reply, confirm the invite permissions and channel overrides.

## Next: memory, automations, MCP tools

- [Chat Apps reference](../chat-apps.md)
- [Pairing](../configuration.md#pairing)
- [AI Agent Memory](./ai-agent-memory.md)
- [Configure MCP tools](./configure-mcp-tools.md)
