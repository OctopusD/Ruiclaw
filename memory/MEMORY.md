# Long-term Memory

This file stores important information that should persist across sessions.

## Project Context

- **RuiClaw** — lightweight open-source AI agent framework (Python + React/TypeScript WebUI); workspace `/Users/darian/project/ruiclaw`; forked from nanobot, MIT.
- Requires Python 3.11+; Node/npm or Bun needed only for WebUI changes.
- Console scripts (`pyproject.toml`): `ruiclaw` → `ruiclaw.cli.entry:main`; `ruiclaw-desktop-tui` → `ruiclaw.cli.desktop_tui:main`.
- Startup commands: `ruiclaw webui` (set Provider/API Key/model in Settings → Models), `ruiclaw agent --workspace <path>` (or `-m "..."` for a one-shot request), `ruiclaw gateway`; run state via `ruiclaw runs list|show`.
- Runtime state: per-workspace `.ruiclaw/runs/<run_id>/` (manifest.json, events.jsonl, report.json) and `.ruiclaw/memory/`; config/sessions under `~/.ruiclaw/`; all gitignored.

## Important Notes

- Workspace `.venv` already exists (Python 3.11.13) and ships `.venv/bin/ruiclaw`; `uv` and `ruiclaw` are not on the shell PATH — use `source .venv/bin/activate` or call `.venv/bin/ruiclaw` directly.

---

*This file is automatically updated by ruiclaw when important information should be remembered.*
