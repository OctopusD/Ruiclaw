# Architecture

This page maps ruiclaw's runtime behavior to source files. Use it when you are debugging internals, reviewing a PR, adding a provider/channel/tool, or trying to understand where a user-visible behavior comes from.

For the product-level mental model, read [`concepts.md`](./concepts.md) first.

## Core Flow

```mermaid
flowchart LR
    Channel["Channel<br/>CLI, WebUI, chat apps"] --> Bus["MessageBus<br/>InboundMessage"]
    Bus --> Loop["AgentLoop<br/>session, workspace, context"]
    Loop --> Runner["AgentRunner<br/>provider/tool loop"]
    Runner --> Provider["Provider<br/>LLM backend"]
    Provider --> Runner
    Runner --> Tools["Tools<br/>files, shell, web, MCP, cron"]
    Tools --> Runner
    Runner --> Loop
    Loop --> Outbound["MessageBus<br/>OutboundMessage"]
    Outbound --> Channel

    Loop -. reads/writes .-> State["Session, memory,<br/>hooks, skills, templates"]
```

Main files:

| Area | Files |
|---|---|
| Message events and queue | `ruiclaw/bus/events.py`, `ruiclaw/bus/queue.py` |
| Turn orchestration | `ruiclaw/agent/loop.py` |
| Provider/tool conversation loop | `ruiclaw/agent/runner.py` |
| Context construction | `ruiclaw/agent/context.py` |
| Session storage and compaction | `ruiclaw/session/manager.py` |
| Long-term memory and Dream | `ruiclaw/agent/memory.py` |

## Agent Loop vs Agent Runner

`AgentLoop` owns the channel-facing turn:

- receives inbound messages;
- determines the effective session and workspace scope;
- builds context;
- wires hooks, progress, and channel metadata;
- publishes outbound messages.

`AgentRunner` owns the model-facing loop:

- sends messages to the selected provider;
- handles streaming deltas and reasoning blocks;
- executes tool calls;
- feeds tool results back into the model;
- stops when a final answer is produced or runtime limits are hit.

MCP connections are application-owned infrastructure. Composition roots create
an `MCPProvider`, share its `ToolRegistry` with `AgentLoop`, await `connect()`
before use, and guarantee `aclose()` during shutdown; the loop does not manage
that lifecycle. `AgentLoop.from_config()` therefore requires a caller-owned
`ToolRegistry`; callers using MCP share it with their application-owned
`MCPProvider`.

Keep this split in mind when debugging. If a problem is about channel routing, session keys, workspace selection, or outbound delivery, start in `agent/loop.py`. If it is about provider calls, tool calls, streaming, or iteration limits, start in `agent/runner.py`.

## Providers

Provider metadata is centralized in `ruiclaw/providers/registry.py`. Configuration fields live in `ruiclaw/config/schema.py`.

Provider selection uses:

- explicit `agents.defaults.provider` or preset provider;
- provider registry keywords;
- API key prefixes and API base URL hints;
- local provider fallback when `apiBase` is configured;
- gateway fallback for providers that can route many model families.

Provider implementations live in `ruiclaw/providers/`. Most hosted providers use the OpenAI-compatible implementation, while Anthropic, Azure OpenAI, AWS Bedrock, OpenAI Codex, and GitHub Copilot have specialized paths.

Useful docs:

- [`providers.md`](./providers.md) for practical setup;
- [`configuration.md#providers`](./configuration.md#providers) for exact provider reference.

## Channels

Channels translate external platforms into `InboundMessage` events and send `OutboundMessage` events back to the platform.

Main files:

| Area | Files |
|---|---|
| Base channel contract | `ruiclaw/channels/base.py` |
| Channel packages | `ruiclaw/channels/<channel>/` |
| Discovery and lifecycle | `ruiclaw/channels/manager.py` |
| WebSocket/WebUI channel | `ruiclaw/channels/websocket/` |

Channels are discovered by scanning self-contained packages under `ruiclaw/channels/`. Add a channel by contributing one package that follows [`channel-package-guide.md`](./channel-package-guide.md).

## WebUI and Gateway

`ruiclaw gateway` starts:

- enabled chat channels;
- the WebSocket channel when configured;
- workspace-scoped cron service;
- system jobs such as Dream and heartbeat;
- the health endpoint on `gateway.port`.

The packaged WebUI is served by the WebSocket channel, not the health endpoint:

| Surface | Default |
|---|---|
| Health endpoint | `http://127.0.0.1:18790/health` |
| WebUI/WebSocket | `http://127.0.0.1:8765` |

WebUI source lives in `webui/`. The production build is written to `ruiclaw/web/dist/` and bundled into the wheel.

Useful docs:

- [`webui.md`](./webui.md) for the WebUI user guide;
- [`../webui/README.md`](../webui/README.md) for frontend source development;
- [`websocket.md`](./websocket.md) for protocol details.

## Tools

Tools are discovered from `ruiclaw/agent/tools/` and plugin entry points.

Important files:

| Tool area | Files |
|---|---|
| Tool base and schema | `ruiclaw/agent/tools/base.py`, `ruiclaw/agent/tools/schema.py` |
| Discovery | `ruiclaw/agent/tools/registry.py` |
| Shell execution | `ruiclaw/agent/tools/shell.py` |
| Filesystem tools | `ruiclaw/agent/tools/filesystem.py` |
| Web search/fetch | `ruiclaw/agent/tools/web.py` |
| MCP tools | `ruiclaw/agent/tools/mcp.py` |
| Cron | `ruiclaw/agent/tools/cron.py`, `ruiclaw/cron/` |
| Image generation | `ruiclaw/agent/tools/image_generation.py` |
| Runtime self-inspection | `ruiclaw/agent/tools/self.py` |

Tool behavior is part of the model contract. Keep user-visible tool names, schemas, and error messages stable unless a change is intentional.

## Config and Paths

The config schema lives in `ruiclaw/config/schema.py`. Loading and saving live in `ruiclaw/config/loader.py`. Runtime path helpers live in `ruiclaw/config/paths.py`.

Defaults:

| Path | Default |
|---|---|
| Config | `~/.ruiclaw/config.json` |
| Workspace | `~/.ruiclaw/workspace/` |
| Sessions | `<config-dir>/sessions/<workspace-id>/*.jsonl` (default: `~/.ruiclaw/sessions/...`) |
| Memory | `<workspace>/memory/` |
| Cron store | `<workspace>/cron/jobs.json` |
| WebUI/media/log runtime data | config directory subdirectories such as `webui/`, `media/`, and `logs/` |

The schema accepts both camelCase and snake_case keys, but saves config with camelCase aliases.

### Agent-Owned State vs Effective Project Context

Runtime code distinguishes the configured agent workspace from the effective
project workspace carried by a session scope. They are often the same path, but
a WebUI chat may select a separate project:

| Concern | Path owner |
|---|---|
| Session namespace, `SOUL.md`, `USER.md`, memory, and custom skills | Configured agent workspace |
| Project `AGENTS.md`, relative tool paths, and shell working directory | Effective project workspace |
| Workspace access mode and project metadata | Session workspace scope |

`ContextBuilder` combines project instructions with agent-owned profile and
memory. Filesystem and search tools use the project as their ordinary boundary
and receive only capability-specific read access to built-in/agent skills and
the exact agent history file. Keep those cross-root capabilities read-only and
explicit; do not treat the entire agent workspace as an allowed root.

## Memory and Sessions

Session history is the near-term conversation replay. Memory is the longer-term workspace state.

| Store | File area |
|---|---|
| Session JSONL files | `<config-dir>/sessions/<workspace-id>/` |
| Long-term memory | `<workspace>/memory/MEMORY.md` |
| Consolidation source history | `<workspace>/memory/history.jsonl` |
| Bootstrap identity files | `<workspace>/SOUL.md`, `<workspace>/USER.md`, templates under `ruiclaw/templates/` |

Dream is implemented in `ruiclaw/agent/memory.py` and scheduled by the runtime when enabled.

## Security Boundaries

Security-sensitive code paths include:

| Boundary | Files |
|---|---|
| Workspace scope | `ruiclaw/security/workspace_access.py`, `ruiclaw/security/workspace_policy.py` |
| Shell sandboxing | `ruiclaw/agent/tools/shell.py` |
| SSRF/network checks | `ruiclaw/security/network.py`, `ruiclaw/agent/tools/web.py` |
| PTH guard and CLI startup security | `ruiclaw/security/` and CLI entrypoints |
| Channel access control | channel config in `ruiclaw/channels/*.py` |

When changing tools, channels, file access, WebUI workspace behavior, or network fetching, treat security as part of the functional behavior and update docs if the user-facing boundary changes.

## Extension Points

| Extension | How |
|---|---|
| Provider | Add `ProviderSpec` in `providers/registry.py`, add schema field in `config/schema.py`, implement provider only if the generic backend is not enough |
| Channel | Export a `ChannelPlugin` descriptor, keep its runtime and optional setup surfaces in one package, and follow [`channel-package-guide.md`](./channel-package-guide.md) |
| Tool | Implement a tool under `agent/tools/` or expose a plugin entry point |
| Agent Plugin | Add a v1 package under `<workspace>/plugins/` and enable it from Apps |
| MCP | Add `tools.mcpServers` config or bundle the server in an Agent Plugin |
| Skill | Add workspace skills under `<workspace>/skills/`, bundle them in an Agent Plugin, or add built-in skills under `ruiclaw/skills/` |
| CLI App | Add it to the CLI Apps catalog; the installer owns its executable lifecycle and writes a skills-only Agent Plugin |

Prefer existing registry/discovery patterns over ad hoc wiring.

## Testing and Verification

Common checks:

```bash
pytest tests/test_openai_api.py::test_function -v
ruff check ruiclaw/
cd webui && bun run test
cd webui && bun run build
```

Choose tests based on the changed surface:

| Change | Minimum useful verification |
|---|---|
| Provider behavior | Provider unit tests or a mocked API path; `ruiclaw agent -m "Hello!"` with safe config when possible |
| Channel behavior | Channel tests plus `ruiclaw gateway` startup path |
| WebUI behavior | WebUI tests/build and, for routing/settings/chat changes, browser-level verification through the gateway |
| Tool behavior | Tool unit tests and an agent-run path when schema or model-facing behavior changes |
| Docs | Link checks, command accuracy against CLI/schema, and `git diff --check` |

For user-facing flows, prefer at least one verification path through the public surface the user actually touches: CLI command, HTTP endpoint, WebSocket/WebUI, chat channel, or packaged import.
