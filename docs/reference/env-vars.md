# Environment variables

All environment variables Hermes reads. Set them in `~/.hermes/.env` or as shell environment variables. Shell variables take precedence.

---

## LLM providers

| Variable | Description | Required for |
|----------|-------------|-------------|
| `OPENROUTER_API_KEY` | OpenRouter API key | OpenRouter provider |
| `ANTHROPIC_API_KEY` | Anthropic direct API key | Anthropic provider |
| `GOOGLE_API_KEY` | Google AI Studio key | Gemini provider |
| `GEMINI_API_KEY` | Alias for `GOOGLE_API_KEY` | Gemini provider |
| `GEMINI_BASE_URL` | Override Gemini base URL | Optional |
| `GLM_API_KEY` | z.ai / ZhipuAI GLM key | zai provider |
| `GLM_BASE_URL` | Override z.ai base URL | Optional |
| `KIMI_API_KEY` | Kimi / Moonshot AI key | kimi-coding provider |
| `KIMI_BASE_URL` | Override Kimi base URL | Optional |
| `MINIMAX_API_KEY` | MiniMax (global) key | minimax provider |
| `MINIMAX_BASE_URL` | Override MiniMax base URL | Optional |
| `MINIMAX_CN_API_KEY` | MiniMax (China) key | minimax-cn provider |
| `MINIMAX_CN_BASE_URL` | Override MiniMax China base URL | Optional |
| `OPENCODE_ZEN_API_KEY` | OpenCode Zen key | OpenCode Zen provider |
| `OPENCODE_ZEN_BASE_URL` | Override OpenCode Zen base URL | Optional |
| `OPENCODE_GO_API_KEY` | OpenCode Go key | OpenCode Go provider |
| `OPENCODE_GO_BASE_URL` | Override OpenCode Go base URL | Optional |
| `HF_TOKEN` | Hugging Face token | huggingface provider |
| `NOUS_API_KEY` | Nous Portal API key | nous-api provider |
| `GITHUB_TOKEN` | GitHub token | copilot provider; Skills Hub |
| `HERMES_INFERENCE_PROVIDER` | Override provider without editing config | Optional |
| `HERMES_QWEN_BASE_URL` | Override Qwen base URL | Optional |
| `KILOCODE_API_KEY` | KiloCode gateway key | kilocode provider |
| `AI_GATEWAY_API_KEY` | Vercel AI Gateway key | ai-gateway provider |

---

## Tool API keys

| Variable | Description | Required for |
|----------|-------------|-------------|
| `PARALLEL_API_KEY` | Parallel AI web search/extract | `web_search`, `web_extract` |
| `EXA_API_KEY` | Exa AI web search | `web_search` (alternative) |
| `FIRECRAWL_API_KEY` | Firecrawl web search/crawl | `web_search` (alternative) |
| `FAL_KEY` | FAL.ai image generation | `generate_image` |
| `BROWSERBASE_API_KEY` | Cloud browser automation | `browser_*` tools (cloud) |
| `BROWSERBASE_PROJECT_ID` | Browserbase project | `browser_*` tools (cloud) |
| `BROWSERBASE_PROXIES` | Enable residential proxies | Optional, default: `true` |
| `BROWSERBASE_ADVANCED_STEALTH` | Advanced stealth mode (Scale plan) | Optional, default: `false` |
| `BROWSER_SESSION_TIMEOUT` | Browser session timeout (seconds) | Optional, default: `300` |
| `BROWSER_INACTIVITY_TIMEOUT` | Browser inactivity cleanup (seconds) | Optional, default: `120` |
| `HONCHO_API_KEY` | Honcho dialectic user modeling | Honcho memory backend |
| `GROQ_API_KEY` | Groq API (for Whisper STT) | Voice transcription (cloud) |
| `VOICE_TOOLS_OPENAI_KEY` | OpenAI key for Whisper + TTS | Voice (alternative to Groq) |
| `TINKER_API_KEY` | Tinker RL training service | RL training |
| `WANDB_API_KEY` | Weights & Biases | RL experiment tracking |
| `RL_API_URL` | RL API server URL | Optional, default: `http://localhost:8080` |

---

## Terminal execution

| Variable | Description | Default |
|----------|-------------|---------|
| `TERMINAL_ENV` | Override terminal backend | From `config.yaml` |
| `TERMINAL_DOCKER_IMAGE` | Docker image for docker backend | `nikolaik/python-nodejs:python3.11-nodejs20` |
| `TERMINAL_SINGULARITY_IMAGE` | Singularity image URI | — |
| `TERMINAL_MODAL_IMAGE` | Modal sandbox image | `nikolaik/python-nodejs:python3.11-nodejs20` |
| `TERMINAL_CWD` | Override working directory | From `config.yaml` |
| `TERMINAL_TIMEOUT` | Command timeout (seconds) | `60` |
| `TERMINAL_LIFETIME_SECONDS` | Backend lifetime (seconds) | `300` |
| `TERMINAL_SSH_HOST` | SSH server hostname | — |
| `TERMINAL_SSH_USER` | SSH username | — |
| `TERMINAL_SSH_PORT` | SSH port | `22` |
| `TERMINAL_SSH_KEY` | Path to SSH private key | ssh-agent |
| `SUDO_PASSWORD` | Password for `sudo -S` | — |

---

## Messaging gateway

### Telegram

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token (from @BotFather) |
| `TELEGRAM_ALLOWED_USERS` | Comma-separated Telegram user IDs |
| `TELEGRAM_HOME_CHANNEL` | Default chat ID for cron deliveries |
| `TELEGRAM_HOME_CHANNEL_NAME` | Display name for home channel |
| `TELEGRAM_WEBHOOK_URL` | Webhook URL (enables webhook mode) |
| `TELEGRAM_WEBHOOK_PORT` | Webhook port | 
| `TELEGRAM_WEBHOOK_SECRET` | Webhook secret for validation |

### Slack

| Variable | Description |
|----------|-------------|
| `SLACK_BOT_TOKEN` | Slack bot token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | Slack app token for Socket Mode (`xapp-...`) |
| `SLACK_ALLOWED_USERS` | Comma-separated Slack user IDs |

### WhatsApp

| Variable | Description |
|----------|-------------|
| `WHATSAPP_ENABLED` | Enable WhatsApp bridge (`false`) |
| `WHATSAPP_ALLOWED_USERS` | Comma-separated phone numbers |

### Email

| Variable | Description |
|----------|-------------|
| `EMAIL_ADDRESS` | Email address for Hermes |
| `EMAIL_PASSWORD` | Email password (or App Password for Gmail) |
| `EMAIL_IMAP_HOST` | IMAP server hostname |
| `EMAIL_IMAP_PORT` | IMAP port (default: `993`) |
| `EMAIL_SMTP_HOST` | SMTP server hostname |
| `EMAIL_SMTP_PORT` | SMTP port (default: `587`) |
| `EMAIL_POLL_INTERVAL` | Seconds between inbox checks (default: `15`) |
| `EMAIL_ALLOWED_USERS` | Comma-separated allowed email addresses |
| `EMAIL_HOME_ADDRESS` | Default address for cron deliveries |

### Gateway-wide

| Variable | Description | Default |
|----------|-------------|---------|
| `GATEWAY_ALLOW_ALL_USERS` | Skip allow-lists (dangerous) | `false` |
| `HERMES_HUMAN_DELAY_MODE` | Message pacing mode | `"off"` |
| `HERMES_HUMAN_DELAY_MIN_MS` | Min delay in ms (custom mode) | `800` |
| `HERMES_HUMAN_DELAY_MAX_MS` | Max delay in ms (custom mode) | `2500` |

---

## Context compression

| Variable | Description | Default |
|----------|-------------|---------|
| `CONTEXT_COMPRESSION_ENABLED` | Enable auto-compression | `true` |
| `CONTEXT_COMPRESSION_THRESHOLD` | Compress at this fraction of context limit | `0.85` |

---

## Voice / STT

| Variable | Description | Default |
|----------|-------------|---------|
| `STT_GROQ_MODEL` | Groq Whisper model | `whisper-large-v3-turbo` |
| `STT_OPENAI_MODEL` | OpenAI Whisper model | `whisper-1` |
| `GROQ_BASE_URL` | Override Groq base URL | — |
| `STT_OPENAI_BASE_URL` | Override OpenAI STT base URL | — |

---

## Debug

| Variable | Description | Default |
|----------|-------------|---------|
| `WEB_TOOLS_DEBUG` | Debug logging for web tools | `false` |
| `VISION_TOOLS_DEBUG` | Debug logging for vision tools | `false` |
| `MOA_TOOLS_DEBUG` | Debug logging for MoA | `false` |
| `IMAGE_TOOLS_DEBUG` | Debug logging for image tools | `false` |

---

## System

| Variable | Description | Default |
|----------|-------------|---------|
| `HERMES_HOME` | Override data directory | `~/.hermes` |
