# How AI Agent Memory Works in ruiclaw

This guide explains how to use ruiclaw's long-term AI agent memory: session
history, compressed archives, durable memory files, Dream consolidation, and
Git-backed memory changes.

## What you will build

- a workspace with persistent session history
- compressed history archives for older turns
- durable memory files such as `USER.md` and `MEMORY.md`
- a Dream workflow for curating long-term memory

## When to use this

Use memory when an agent should remember stable preferences, project facts,
decisions, and recurring context across sessions. Do not use memory as a dumping
ground for every raw transcript; ruiclaw separates short-term messages from
curated durable knowledge.

## Install

```bash
python -m pip install ruiclaw-ai
ruiclaw onboard --wizard
ruiclaw agent -m "Hello!"
```

## Minimal working example

Ask the agent to remember a stable fact in a normal session, then run Dream:

```text
/dream
```

Inspect recent memory changes:

```text
/dream-log
```

The exact files live in the active workspace, usually under
`~/.ruiclaw/workspace/`.

## Production notes

- Use one workspace per project or personal context.
- Keep durable facts concise; old session details belong in `history.jsonl`.
- Use `/dream-prompt init` when a workspace needs custom memory guidance.
- Review Git-backed memory changes when memory affects important workflows.

## Security notes

- Memory files may contain sensitive user or project facts.
- Avoid sharing workspaces without reviewing `SOUL.md`, `USER.md`, and
  `memory/MEMORY.md`.
- Use separate workspaces for personal and team contexts.

## Troubleshooting

- If memory feels stale, run `/dream` and inspect `/dream-log`.
- If memory changed incorrectly, use `/dream-restore` to inspect and restore
  previous versions.
- If a new session lacks context, confirm it uses the same workspace.

## Related ruiclaw docs

- [AI Agent Memory in ruiclaw](../memory.md)
- [Concepts](../concepts.md)
- [Configuration](../configuration.md#auto-compact)
- [Chat Commands](../chat-commands.md)
