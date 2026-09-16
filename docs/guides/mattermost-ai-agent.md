# Build a Mattermost AI Agent with ruiclaw

This guide connects ruiclaw to Mattermost through the built-in Mattermost
channel, using WebSocket events and the Mattermost REST API.

## What this guide builds

- a Mattermost bot account or token
- the `mattermost` channel enabled in ruiclaw
- mention-only group behavior for first deployment
- one pairing-approved DM or mention test

## Prerequisites

- A working local ruiclaw reply:

```bash
ruiclaw agent -m "Hello!"
```

- A Mattermost server URL.
- A bot token or personal access token for the bot account.

## Install ruiclaw

```bash
python -m pip install ruiclaw-ai
ruiclaw onboard --wizard
```

## Enable the Mattermost channel

Merge this snippet into `~/.ruiclaw/config.json`:

```json
{
  "channels": {
    "mattermost": {
      "enabled": true,
      "serverUrl": "https://mattermost.example.com",
      "token": "YOUR_MATTERMOST_TOKEN",
      "teamId": "YOUR_TEAM_ID",
      "groupPolicy": "mention",
      "groupPolicyInThread": "open",
      "replyInThread": true,
      "dm": {
        "policy": "allowlist"
      }
    }
  }
}
```

`teamId` scopes the channel to a Mattermost team. Keep `groupPolicy` as
`mention` for the first test. `groupPolicyInThread` can be `"mention"`,
`"open"`, or `"allowlist"` and controls messages that reply inside a
thread. If it is omitted, it inherits `groupPolicy`, preserving the behavior
of existing configurations. Set it to `"open"` explicitly when follow-up
messages in threads should not require another @mention.

When `groupPolicy` is `"allowlist"`, `groupAllowFrom` remains the outer
channel boundary for root posts and thread replies. A thread policy cannot open
a channel that is not on that allowlist.

Mattermost DMs are open by default. Setting `dm.policy` to `"allowlist"` with no
`dm.allowFrom` entries makes new DM senders receive a pairing code. Approve the
code before using the bot normally.

## Run ruiclaw gateway

```bash
ruiclaw channels status
ruiclaw gateway
```

## Test a message

DM the bot account. It should return a pairing code. Approve it from a trusted
local surface:

```bash
ruiclaw agent -m "/pairing approve ABCD-EFGH"
```

Then DM the bot again, or mention it in a channel where the bot has access:

```text
@ruiclaw Hello from Mattermost
```

## Security notes

- Store the Mattermost token in an environment variable for deployed services.
- Keep `dm.policy` as `"allowlist"` when you want pairing-based approval.
- Use mention-only group behavior before opening the bot to busy channels.
- Review file and shell tools before inviting broad channel access.

## Troubleshooting

- If startup logs say `serverUrl and token must be configured`, check the
  camelCase config keys.
- If DMs are ignored, review the `dm` policy and pairing approval state.
- If channel messages are ignored, confirm the bot is mentioned and belongs to
  the team/channel.
- If thread replies are surprising, review `groupPolicyInThread`,
  `replyInThread`, and `includeThreadContext`.

## Next: memory, automations, MCP tools

- [Chat Apps reference](../chat-apps.md)
- [Pairing](../configuration.md#pairing)
- [Long-running AI Agent](./long-running-ai-agent.md)
- [Deployment](../deployment.md)
