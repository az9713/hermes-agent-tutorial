# The tool system

Tools are the mechanisms that let Hermes take actions in the real world: run commands, search the web, read and write files, control a browser, generate images, delegate to subagents.

## What it is

The tool system is a self-registering registry of callable functions exposed to the LLM as JSON schemas. When the model wants to take an action, it emits a tool call. The agent loop dispatches it through the registry. The registry finds the handler, calls it with the parsed arguments, and returns the result to the model.

The central registry lives in `tools/registry.py`.

## How tools self-register

Each tool file calls `registry.register()` at import time:

```python
# tools/web_tools.py (simplified)
from tools.registry import registry

registry.register(
    name="web_search",
    toolset="web",
    schema={
        "name": "web_search",
        "description": "Search the web for current information",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    handler=_web_search_handler,
    check_fn=lambda: bool(os.environ.get("PARALLEL_API_KEY") or os.environ.get("EXA_API_KEY")),
)
```

`model_tools.py` imports all tool modules when the agent starts — that's what triggers the registrations. No central list needs updating when you add a tool.

See [ADR 001](../architecture/adr/001-tool-self-registration.md) for why this pattern was chosen.

## Toolsets

Tools are grouped into toolsets. A toolset is a named category of related tools:

| Toolset | Tools in it |
|---------|-------------|
| `web` | `web_search`, `web_extract` |
| `terminal` | `run_command`, `run_interactive` |
| `file` | `read_file`, `write_file`, `search_files`, `patch_file` |
| `browser` | `browser_navigate`, `browser_screenshot`, `browser_click`, etc. |
| `code` | `execute_code` |
| `delegate` | `delegate` |
| `mcp` | Dynamically registered MCP server tools |
| `cron` | `create_cron_job`, `list_cron_jobs`, `delete_cron_job` |
| `image` | `generate_image` |
| `memory` | `search_sessions`, `update_memory` |
| `system` | `clarify` |

Toolsets are enabled or disabled per platform in `toolsets.py` and `tools/toolset_distributions.py`. The CLI enables all toolsets by default. The Telegram gateway might disable `terminal` for security.

## Availability checks

Each tool has an optional `check_fn` — a function that returns whether the tool is currently usable. A tool that requires `PARALLEL_API_KEY` reports as unavailable if the key isn't set, and its schema is excluded from the system prompt.

You can see which tools are available and why some are disabled with:

```bash
hermes tools
```

## All tools at a glance

### Web tools (`tools/web_tools.py`)

| Tool | What it does | Requires |
|------|-------------|---------|
| `web_search` | Search the web for current information | `PARALLEL_API_KEY` or `EXA_API_KEY` or `FIRECRAWL_API_KEY` |
| `web_extract` | Extract full text from a URL | Same as web_search |

### Terminal tool (`tools/terminal_tool.py`)

| Tool | What it does | Requires |
|------|-------------|---------|
| `run_command` | Execute a shell command | Configured terminal backend |
| `run_interactive` | Run an interactive process (e.g., Python REPL) | Configured terminal backend |

The terminal tool dispatches to the active backend. See [terminal execution](terminal-execution.md) for backend details.

### File tools (`tools/file_tools.py`, `file_operations.py`)

| Tool | What it does |
|------|-------------|
| `read_file` | Read a file's contents |
| `write_file` | Write or overwrite a file |
| `patch_file` | Apply a diff-style patch to a file |
| `search_files` | Fuzzy-search for files by name or content |
| `list_directory` | List directory contents |

File tools enforce a write deny-list: they refuse to write to sensitive paths (SSH keys, shadow passwords, Hermes config files, etc.).

### Browser tools (`tools/browser_tool.py`, `browser_camofox.py`)

| Tool | What it does | Requires |
|------|-------------|---------|
| `browser_navigate` | Open a URL in a browser | `BROWSERBASE_API_KEY` or local Node.js |
| `browser_screenshot` | Capture the current page | Same |
| `browser_click` | Click an element | Same |
| `browser_fill` | Fill a form field | Same |
| `browser_extract` | Extract text from the current page | Same |

Two backends: Browserbase (cloud, with stealth and CAPTCHA solving) or local Camoufox (stealth Chromium via Node.js).

### Code execution (`tools/code_execution_tool.py`)

| Tool | What it does |
|------|-------------|
| `execute_code` | Run Python code in a sandboxed subprocess with RPC tool access |

The code execution sandbox strips API keys from the environment before running user code. Tools are accessible via an RPC bridge, so executed code can call `run_command`, `web_search`, etc.

### Delegation (`tools/delegate_tool.py`)

| Tool | What it does |
|------|-------------|
| `delegate` | Spawn a child AIAgent to handle a subtask in parallel |

The child agent is isolated (separate conversation history) but has RPC access to the parent's tool registry. Results are returned to the parent when the child finishes.

### MCP tools (`tools/mcp_tool.py`)

When MCP servers are configured in `~/.hermes/mcp_config.json`, each server's tools are registered dynamically in the `mcp` toolset. The agent can call any MCP tool as if it were a native Hermes tool.

### Session search (`tools/session_search_tool.py`)

| Tool | What it does |
|------|-------------|
| `search_sessions` | Search past conversations using FTS5 full-text search |

### Cron tools (`tools/cronjob_tools.py`)

| Tool | What it does |
|------|-------------|
| `create_cron_job` | Schedule a recurring task |
| `list_cron_jobs` | List all scheduled jobs |
| `delete_cron_job` | Delete a scheduled job |

### Image generation (`tools/image_generation_tool.py`)

| Tool | What it does | Requires |
|------|-------------|---------|
| `generate_image` | Generate an image via FAL.ai | `FAL_KEY` |

### Clarification (`tools/clarify_tool.py`)

| Tool | What it does |
|------|-------------|
| `clarify` | Pause and ask the user a question before proceeding |

## The approval flow

Some commands are dangerous. Before executing a command that matches the danger patterns in `tools/approval.py` (e.g., `sudo rm -rf`, `DROP TABLE`, `git push --force`), Hermes pauses and asks for explicit user approval.

The approval flow differs by interface:
- **CLI**: Prompts inline in the terminal, waits for y/n
- **Gateway**: Sends a message to the user requesting confirmation

You can configure a command allowlist (pre-approved patterns) in your config to skip approval for trusted commands.

## Configuration and tuning

Enable or disable individual toolsets:

```bash
hermes tools
```

Or edit `~/.hermes/config.yaml`:

```yaml
tools:
  disabled_toolsets:
    - browser
    - image
```

Set a tool's required env var:

```bash
hermes config set EXA_API_KEY sk-xxx
```

Or add it to `~/.hermes/.env`.
