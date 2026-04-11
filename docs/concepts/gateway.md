# The messaging gateway

The gateway lets you talk to Hermes from any messaging platform: Telegram, Discord, Slack, WhatsApp, Signal, Email, Matrix, DingTalk, Feishu, WeChat, Mattermost, SMS, iMessage (via BlueBubbles), and a REST API.

## What it is

The gateway is a long-lived process that listens for messages on one or more platforms, routes them to an `AIAgent` instance, and delivers responses back to the originating platform.

The gateway runs as a separate process from the CLI:

```bash
hermes gateway start
```

## Architecture

```
Platform A (Telegram)    Platform B (Discord)    Platform C (Slack)
       │                        │                       │
       ▼                        ▼                       ▼
PlatformAdapter           PlatformAdapter         PlatformAdapter
  (telegram.py)             (discord.py)            (slack.py)
       │                        │                       │
       └────────────────────────┴───────────────────────┘
                                │
                                ▼
                       GatewayRunner (gateway/run.py)
                       ├── Message routing
                       ├── Session management
                       ├── Slash command dispatch
                       ├── Hook system
                       └── Cron scheduler integration
                                │
                                ▼
                    AIAgent (one per active session)
                                │
                                ▼
                    LLM + Tool dispatch
                                │
                                ▼
                    Platform delivery (gateway/delivery.py)
```

## Supported platforms

| Platform | Adapter | Notes |
|----------|---------|-------|
| Telegram | `gateway/platforms/telegram.py` | Bot API, long polling or webhook |
| Discord | `gateway/platforms/discord.py` | discord.py |
| Slack | `gateway/platforms/slack.py` | Slack Bolt, Socket Mode |
| WhatsApp | `gateway/platforms/whatsapp.py` | Baileys bridge (Node.js) |
| Signal | `gateway/platforms/signal.py` | signal-cli |
| Email | `gateway/platforms/email.py` | IMAP/SMTP |
| Matrix | `gateway/platforms/matrix.py` | matrix-nio |
| DingTalk | `gateway/platforms/dingtalk.py` | Alibaba DingTalk |
| Feishu/Lark | `gateway/platforms/feishu.py` | ByteDance Feishu |
| WeChat for Work | `gateway/platforms/wecom.py` | Enterprise WeChat |
| WeChat | `gateway/platforms/weixin.py` | Consumer WeChat |
| Mattermost | `gateway/platforms/mattermost.py` | Open source Slack alternative |
| SMS | `gateway/platforms/sms.py` | Via Twilio or compatible |
| iMessage | `gateway/platforms/bluebubbles.py` | Via BlueBubbles server |
| Home Assistant | `gateway/platforms/homeassistant.py` | Smart home integration |
| REST API | `gateway/platforms/api_server.py` | HTTP API for custom integrations |
| Webhook | `gateway/platforms/webhook.py` | Receive webhook events |

## Session management

Each (user, platform, chat) tuple gets its own conversation session. If you message Hermes on Telegram and then switch to the CLI, those are separate sessions by default.

The `SessionStore` in `gateway/session.py` tracks active sessions. A session is created on first message and reused for subsequent messages in the same chat.

Session isolation means:
- Different users on the same platform have separate conversations
- The same user on different platforms has separate conversations
- Each session has its own AIAgent instance and message history

## User allow-lists

By default, the gateway only responds to users on the configured allow-list. This prevents strangers from talking to your agent.

Configure per-platform:

```bash
# In ~/.hermes/.env:
TELEGRAM_ALLOWED_USERS=123456789,987654321
SLACK_ALLOWED_USERS=U01234567,U09876543
```

To allow all users (dangerous — only for trusted closed networks):

```bash
GATEWAY_ALLOW_ALL_USERS=true
```

## Slash commands in the gateway

All slash commands that work in the CLI also work in the gateway. The full command list is in `hermes_cli/commands.py` — the single source of truth for all platforms.

Additional gateway-specific commands:

| Command | What it does |
|---------|-------------|
| `/status` | Show gateway and platform connection status |
| `/sethome` | Set this chat as the home channel for cron deliveries |
| `/stop` | Interrupt the current task |
| `/platforms` | Show all connected platforms and active sessions |

## Home channel

The home channel is where cron jobs deliver their output when no specific channel is specified. Set it with `/sethome` in any conversation.

Per-platform home channel override via env var:

```bash
TELEGRAM_HOME_CHANNEL=123456789
TELEGRAM_HOME_CHANNEL_NAME=My Home Chat
```

## Message delivery

The delivery module (`gateway/delivery.py`) handles:

- **Response queuing** — Responses are queued to avoid flooding rate-limited platforms
- **Streaming** — Long responses are streamed as they arrive rather than sent as one block
- **Human-like delays** — Optional pacing (`HERMES_HUMAN_DELAY_MODE`) makes the bot feel less robotic
- **Message splitting** — Responses exceeding platform message limits are split automatically

## Mirroring

Cross-platform mirroring (`gateway/mirror.py`) lets you receive messages on one platform while the response is delivered to another. Useful for monitoring a bot conversation from a different device.

## Webhook mode (Telegram)

By default, Telegram uses long polling. For production deployments (Fly.io, Railway, etc.), switch to webhook mode:

```bash
TELEGRAM_WEBHOOK_URL=https://my-app.fly.dev/telegram
TELEGRAM_WEBHOOK_PORT=8443
TELEGRAM_WEBHOOK_SECRET=a-random-secret
```

## Setting up the gateway

```bash
hermes gateway setup    # Interactive setup wizard for all platforms
hermes gateway start    # Start the gateway process
hermes gateway status   # Check connection status
hermes gateway stop     # Stop the running gateway
hermes gateway restart  # Restart gracefully
```

For platform-specific setup instructions, see the [prerequisites](../getting-started/prerequisites.md) and the env vars for each platform in the [environment variables reference](../reference/env-vars.md).

## Adding a new platform

See [add a platform](../guides/add-a-platform.md) and the existing guide at `gateway/platforms/ADDING_A_PLATFORM.md`.

## Common gotchas

**Message arrives but no response** — Check that the sending user is on the allow-list (`TELEGRAM_ALLOWED_USERS`, etc.).

**Gateway stops after a few hours** — Check for network timeouts on the platform connection. Consider running the gateway under `systemd` or `screen` for persistence.

**Two users getting each other's conversations** — Check that credential token locks are correctly configured. Each profile should have a unique bot token. The lock mechanism in `gateway/status.py` prevents two gateway instances from using the same token simultaneously.
