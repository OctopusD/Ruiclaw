# How to Build a Personal AI Agent with ruiclaw

This guide builds a personal AI agent you can run locally, talk to from the
terminal or browser, and later connect to chat apps, memory, tools, and
automations.

## What you will build

- a configured ruiclaw install
- one working model provider
- one local agent reply
- a browser WebUI session for ongoing work

## When to use this

Use this when you want a personal AI agent that you control rather than a hosted
chat-only interface. ruiclaw is useful when the agent needs local workspace
access, tool calls, session history, memory, scheduled work, or chat app
delivery.

## Install

Follow [Quick Start](../quick-start.md) to install ruiclaw using the recommended
method for your operating system and configure one model. Return here after
you receive the first reply. If terminals and config files are new to you, use
[Start Without Technical Background](../start-without-technical-background.md)
instead.

## Minimal working example

First prove the runtime can answer:

```bash
ruiclaw agent -m "Hello!"
```

Then open the browser workbench:

```bash
ruiclaw webui
```

The WebUI starts the local gateway, opens a browser, and keeps persistent chat
sessions for longer work.

## Production notes

- Keep one workspace per project or personal context.
- Use `modelPresets` when you want stable names for fast, deep, local, or
  fallback models.
- Keep `ruiclaw gateway` running for WebUI, chat apps, automations, and the
  WebSocket channel.
- Use the Python SDK or OpenAI-compatible API when another program should call
  the agent.

## Security notes

- Do not store API keys directly in shared files; use environment variables.
- Prefer chat app pairing for first setup. Use `allowFrom` only for static
  allowlists, and keep those lists narrow.
- Enable workspace restriction before exposing file or shell tools to other
  users.
- Use a separate workspace for experiments that can modify files.

## Troubleshooting

- `ruiclaw status` shows the config path, workspace path, and active model.
- If `ruiclaw agent -m "Hello!"` fails, fix provider setup before opening the
  WebUI or chat apps.
- If the WebUI opens but does not answer, check gateway logs and provider
  credentials.

## Related ruiclaw docs

- [Quick Start](../quick-start.md)
- [Concepts](../concepts.md)
- [WebUI](../webui.md)
- [Configuration](../configuration.md)
- [Troubleshooting](../troubleshooting.md)
