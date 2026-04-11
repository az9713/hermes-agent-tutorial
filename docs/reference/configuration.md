# Configuration reference

All configuration options for `~/.hermes/config.yaml`. Environment variables in `~/.hermes/.env` take precedence over config file values.

Copy `cli-config.yaml.example` in the repo root to get started.

---

## model

LLM provider and model settings.

### model.default

Type: `string`
Default: `"anthropic/claude-opus-4.6"`
Description: The model used for all conversations. Accepts any model identifier supported by the active provider.

```yaml
model:
  default: "anthropic/claude-opus-4.6"
```

### model.provider

Type: `string`
Default: `"auto"`
Description: Which LLM provider to use. `"auto"` detects the provider from credentials automatically.

| Value | Requires |
|-------|---------|
| `"auto"` | Any configured credential |
| `"openrouter"` | `OPENROUTER_API_KEY` |
| `"nous"` | `hermes login` (OAuth) |
| `"nous-api"` | `NOUS_API_KEY` |
| `"anthropic"` | `ANTHROPIC_API_KEY` |
| `"openai-codex"` | `hermes login --provider openai-codex` |
| `"copilot"` | `GITHUB_TOKEN` |
| `"gemini"` | `GOOGLE_API_KEY` or `GEMINI_API_KEY` |
| `"zai"` | `GLM_API_KEY` |
| `"kimi-coding"` | `KIMI_API_KEY` |
| `"minimax"` | `MINIMAX_API_KEY` |
| `"minimax-cn"` | `MINIMAX_CN_API_KEY` |
| `"huggingface"` | `HF_TOKEN` |
| `"kilocode"` | `KILOCODE_API_KEY` |
| `"ai-gateway"` | `AI_GATEWAY_API_KEY` |
| `"custom"` | `model.base_url` + optional `model.api_key` |
| `"lmstudio"` | `model.base_url` (alias for `"custom"`) |
| `"ollama"` | `model.base_url` (alias for `"custom"`) |
| `"vllm"` | `model.base_url` (alias for `"custom"`) |
| `"llamacpp"` | `model.base_url` (alias for `"custom"`) |

### model.base_url

Type: `string`
Default: `"https://openrouter.ai/api/v1"` when using OpenRouter
Description: Base URL for the API endpoint. Required when `provider` is `"custom"`.

```yaml
model:
  provider: "lmstudio"
  base_url: "http://localhost:1234/v1"
```

### model.context_length

Type: `integer`
Default: auto-detected from provider
Description: Total context window in tokens (input + output combined). Leave unset unless auto-detection is wrong (e.g., local servers with custom `num_ctx`).

### model.max_tokens

Type: `integer`
Default: model's native ceiling
Description: Maximum tokens the model may generate per response. Leave unset to use the model's full output capability.

---

## provider_routing

Controls request routing across providers when using OpenRouter.

### provider_routing.sort

Type: `string`
Default: `"price"`
Values: `"price"`, `"throughput"`, `"latency"`
Description: Strategy for selecting which OpenRouter provider to route requests to.

### provider_routing.only

Type: `list[string]`
Default: unset (all providers)
Description: Restrict to specific provider slugs. Example: `["anthropic", "google"]`.

### provider_routing.ignore

Type: `list[string]`
Default: unset
Description: Exclude specific provider slugs.

### provider_routing.order

Type: `list[string]`
Default: unset
Description: Try providers in this explicit order.

### provider_routing.require_parameters

Type: `boolean`
Default: `false`
Description: Only use providers that support all parameters in your request.

### provider_routing.data_collection

Type: `string`
Default: `"allow"`
Values: `"allow"`, `"deny"`
Description: `"deny"` excludes providers that may store conversation data.

---

## smart_model_routing

Route short/simple turns to a cheaper model.

### smart_model_routing.enabled

Type: `boolean`
Default: `false`

### smart_model_routing.max_simple_chars

Type: `integer`
Default: `160`
Description: Turns shorter than this character count are candidates for the cheap model.

### smart_model_routing.max_simple_words

Type: `integer`
Default: `28`

### smart_model_routing.cheap_model.provider

Type: `string`
Description: Provider for the cheap model.

### smart_model_routing.cheap_model.model

Type: `string`
Description: Model ID for simple turns. Example: `"google/gemini-2.5-flash"`.

---

## terminal

Terminal execution backend configuration.

### terminal.backend

Type: `string`
Default: `"local"`
Values: `"local"`, `"docker"`, `"ssh"`, `"modal"`, `"daytona"`, `"singularity"`

### terminal.cwd

Type: `string`
Default: `"."` (CLI), home directory (gateway)
Description: Working directory. For remote backends, this is a path inside the remote environment.

### terminal.timeout

Type: `integer`
Default: `180`
Description: Command timeout in seconds. Commands exceeding this are killed.

### terminal.lifetime_seconds

Type: `integer`
Default: `300`
Description: Seconds of inactivity before the backend environment is destroyed.

### terminal.sudo_password

Type: `string`
Default: unset
Description: Password for `sudo -S`. Stored in plaintext — only for trusted machines.

### terminal.docker_image

Type: `string`
Default: `"nikolaik/python-nodejs:python3.11-nodejs20"`
Description: Docker image used when `backend: "docker"`.

### terminal.docker_mount_cwd_to_workspace

Type: `boolean`
Default: `false`
Description: Mount the launch directory into `/workspace` in the container.

### terminal.docker_forward_env

Type: `list[string]`
Default: `[]`
Description: Environment variables to forward from host into the Docker container.

### terminal.ssh_host

Type: `string`
Description: SSH server hostname. Required when `backend: "ssh"`.

### terminal.ssh_user

Type: `string`
Description: SSH username.

### terminal.ssh_port

Type: `integer`
Default: `22`

### terminal.ssh_key

Type: `string`
Description: Path to SSH private key. Uses ssh-agent if unset.

### terminal.modal_image

Type: `string`
Default: `"nikolaik/python-nodejs:python3.11-nodejs20"`
Description: Docker image for Modal sandboxes.

### terminal.singularity_image

Type: `string`
Description: Singularity/Apptainer image URI.

---

## compression

Context compression settings.

### compression.enabled

Type: `boolean`
Default: `true`
Description: Automatically compress conversation history when approaching the context limit.

### compression.threshold

Type: `float`
Default: `0.85`
Description: Compress when context is at this fraction of the limit.

### compression.summary_model

Type: `string`
Default: `"google/gemini-3-flash-preview"`
Description: Model used for summarizing compressed turns.

---

## worktree

Type: `boolean`
Default: `false`
Description: Always create an isolated git worktree when in a git repo. Equivalent to always passing `-w`.

---

## memory

Memory system configuration.

### memory.enabled

Type: `boolean`
Default: `true`

### memory.backend

Type: `string`
Default: `"local"`
Values: `"local"`, `"honcho"`, `"mem0"`, `"openviking"`, `"retaindb"`, `"supermemory"`, `"byterover"`, `"hindsight"`, `"holographic"`

### memory.nudge_frequency

Type: `string`
Default: `"after_complex_tasks"`
Values: `"always"`, `"after_complex_tasks"`, `"never"`
Description: How often Hermes checks whether the current conversation contains something worth adding to memory.

---

## tools

Tool enable/disable configuration.

### tools.disabled_toolsets

Type: `list[string]`
Default: `[]`
Description: Toolsets to disable globally.

```yaml
tools:
  disabled_toolsets:
    - browser
    - image
```
