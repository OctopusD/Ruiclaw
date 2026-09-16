# Build a WeChat AI Agent with ruiclaw

This guide connects ruiclaw to WeChat through the `weixin` channel. The channel
uses HTTP long polling with QR-code login through the supported upstream API.

## What this guide builds

- the `weixin` channel enabled in ruiclaw
- a QR-code login session
- one pairing-approved WeChat sender
- a running gateway for message delivery

## Prerequisites

- A working local ruiclaw reply:

```bash
ruiclaw agent -m "Hello!"
```

- A WeChat account that can complete QR-code login.

## Install ruiclaw

```bash
python -m pip install ruiclaw-ai
ruiclaw onboard --wizard
```

## Enable the WeChat channel

Install the optional channel dependency:

```bash
ruiclaw plugins enable weixin
```

Merge this snippet into `~/.ruiclaw/config.json`:

```json
{
  "channels": {
    "weixin": {
      "enabled": true
    }
  }
}
```

Omitting `allowFrom` enables pairing-only mode. The first private WeChat message
from a new sender gets a pairing code instead of agent access.

Log in:

```bash
ruiclaw channels login weixin
```

Use `--force` if you need to discard saved login state and authenticate again.

## Run ruiclaw gateway

```bash
ruiclaw channels status
ruiclaw gateway
```

## Test a message

Send a private WeChat message to the bot. It should reply with a pairing code.
Approve it from a trusted local surface:

```bash
ruiclaw agent -m "/pairing approve ABCD-EFGH"
```

Send the message again after approval and watch gateway logs for the sender ID
and reply.

## Security notes

- Prefer pairing-only mode for first setup. Add `allowFrom` only when you want a
  static allowlist.
- Treat saved login state as sensitive account access.
- Avoid connecting personal accounts to untrusted workspaces or broad tool
  permissions.

## Troubleshooting

- If login fails, rerun `ruiclaw channels login weixin --force`.
- If a first private message returns a pairing code, that is expected. Approve
  the code before testing normal agent replies.
- If messages are denied without a pairing code, check gateway logs for whether
  WeChat provided the context token required for ruiclaw to reply.
- If polling disconnects, restart the gateway and check network reachability to
  the upstream service.

## Next: memory, automations, MCP tools

- [Chat Apps reference](../chat-apps.md)
- [AI Agent Memory](./ai-agent-memory.md)
- [Secure local AI agent](./secure-local-ai-agent.md)
- [Deployment](../deployment.md)
