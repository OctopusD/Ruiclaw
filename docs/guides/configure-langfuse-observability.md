# How to Configure Langfuse Observability for ruiclaw

ruiclaw can trace supported OpenAI-compatible provider calls through Langfuse's
OpenAI SDK wrapper.

## What you will build

- Langfuse installed in the same Python environment as ruiclaw
- Langfuse environment variables set before startup
- one traced ruiclaw model call

## When to use this

Use Langfuse when you need observability for model requests, latency, errors,
cost, or prompt behavior during development or production operation.

## Install

Install ruiclaw and prove the agent works:

```bash
python -m pip install ruiclaw-ai
ruiclaw onboard --wizard
ruiclaw agent -m "Hello!"
```

Install Langfuse:

```bash
ruiclaw plugins enable langfuse
```

## Minimal working example

Set credentials before starting ruiclaw:

```bash
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
ruiclaw agent -m "Hello!"
```

PowerShell:

```powershell
$env:LANGFUSE_SECRET_KEY = "sk-lf-..."
$env:LANGFUSE_PUBLIC_KEY = "pk-lf-..."
$env:LANGFUSE_BASE_URL = "https://cloud.langfuse.com"
ruiclaw agent -m "Hello!"
```

## Production notes

- Langfuse is configured with environment variables, not `config.json`.
- Start services from an environment that exports the same variables.
- Add tracing after the provider works; it should not be the first setup step.
- Native providers that do not use the OpenAI-compatible client path may not
  produce Langfuse OpenAI-wrapper traces.

## Security notes

- Treat Langfuse projects as observability stores for sensitive prompts and
  outputs.
- Use separate projects for personal, staging, and production traffic.
- Keep Langfuse keys out of committed service files.

## Troubleshooting

- If no traces appear, confirm the service process sees the environment
  variables.
- Confirm the provider path is OpenAI-compatible.
- Run one local `ruiclaw agent -m "Hello!"` call before debugging service logs.

## Related ruiclaw docs

- [Configuration: Langfuse Observability](../configuration.md#langfuse-observability)
- [Provider Cookbook: Langfuse Tracing](../provider-cookbook.md#recipe-langfuse-tracing)
- [Deployment](../deployment.md)
