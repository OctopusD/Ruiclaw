# Build an Email AI Agent with ruiclaw

This guide turns ruiclaw into an email AI agent that polls IMAP for accepted
messages and replies through SMTP.

## What this guide builds

- a dedicated mailbox for ruiclaw
- IMAP and SMTP credentials in `config.json`
- an allowed sender list
- a gateway process that polls and replies

## Prerequisites

- A working local ruiclaw reply:

```bash
ruiclaw agent -m "Hello!"
```

- A mailbox for the bot.
- IMAP and SMTP access. For Gmail, use an app password rather than your account
  password.

## Install ruiclaw

```bash
python -m pip install ruiclaw-ai
ruiclaw onboard --wizard
```

## Enable the Email channel

Merge this snippet into `~/.ruiclaw/config.json` and replace the addresses and
passwords:

```json
{
  "channels": {
    "email": {
      "enabled": true,
      "consentGranted": true,
      "imapHost": "imap.gmail.com",
      "imapPort": 993,
      "imapUsername": "my-ruiclaw@gmail.com",
      "imapPassword": "your-app-password",
      "smtpHost": "smtp.gmail.com",
      "smtpPort": 587,
      "smtpUsername": "my-ruiclaw@gmail.com",
      "smtpPassword": "your-app-password",
      "fromAddress": "my-ruiclaw@gmail.com",
      "allowFrom": ["your-real-email@gmail.com"],
      "autoReplyEnabled": true
    }
  }
}
```

## Run ruiclaw gateway

```bash
ruiclaw channels status
ruiclaw gateway
```

## Test a message

Send an email from an address in `allowFrom` to the bot mailbox. Keep the
gateway running long enough for the polling interval to receive it.

## Security notes

- Use a dedicated mailbox, not your primary personal inbox.
- Set `consentGranted` to `false` to fully disable mailbox access.
- Email does not use DM pairing. Keep `allowFrom` narrow; `["*"]` accepts mail
  from anyone.
- Use environment variables for mailbox passwords.
- Enable attachment types only when the agent needs them.

## Troubleshooting

- If login fails, confirm IMAP/SMTP access and app-password setup.
- If the bot reads but does not reply, check `autoReplyEnabled`, SMTP settings,
  and allowed sender addresses.
- If attachments are missing, review `allowedAttachmentTypes`, size limits, and
  gateway logs.

## Next: memory, automations, MCP tools

- [Chat Apps reference](../chat-apps.md)
- [Secure local AI agent](./secure-local-ai-agent.md)
- [AI Agent Memory](./ai-agent-memory.md)
- [OpenAI-compatible agent API](./openai-compatible-agent-api.md)
