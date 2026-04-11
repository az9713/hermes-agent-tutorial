# Tools reference

Complete reference for all tools available in Hermes. Tool availability depends on configured credentials and enabled toolsets.

Check which tools are currently available:

```bash
hermes tools
```

---

## web_search

**Toolset:** `web`
**Requires:** `PARALLEL_API_KEY`, `EXA_API_KEY`, or `FIRECRAWL_API_KEY`

Search the web for current information.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | yes | The search query |
| `num_results` | integer | no | Number of results to return (default: 5) |

Returns: Formatted search results with titles, URLs, and snippets.

---

## web_extract

**Toolset:** `web`
**Requires:** `PARALLEL_API_KEY`, `EXA_API_KEY`, or `FIRECRAWL_API_KEY`

Extract full text content from a URL.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | yes | URL to extract content from |
| `format` | string | no | `"markdown"` (default) or `"text"` |

Returns: Extracted page content.

---

## run_command

**Toolset:** `terminal`
**Requires:** Configured terminal backend

Execute a shell command in the active terminal backend.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | string | yes | Shell command to execute |
| `timeout` | integer | no | Override timeout in seconds |
| `background` | boolean | no | Run in background (default: false) |

Returns: Combined stdout and stderr from the command.

Dangerous commands (matching patterns in `tools/approval.py`) require user approval before execution.

---

## run_interactive

**Toolset:** `terminal`
**Requires:** Configured terminal backend

Start an interactive process session (Python REPL, Node.js, etc.).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `command` | string | yes | Command to start the interactive process |
| `input` | string | no | Input to send to the process |

---

## read_file

**Toolset:** `file`

Read the contents of a file.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | yes | File path (absolute or relative to cwd) |
| `offset` | integer | no | Start reading at this line number |
| `limit` | integer | no | Maximum number of lines to read |

Returns: File contents. Binary files are rejected with an error message.

---

## write_file

**Toolset:** `file`

Write content to a file (creates or overwrites).

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | yes | File path |
| `content` | string | yes | Content to write |

A write deny-list prevents writing to: SSH keys, shadow passwords, Hermes config files, and other sensitive paths.

---

## patch_file

**Toolset:** `file`

Apply a targeted replacement to a file without rewriting the whole thing.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | yes | File path |
| `old_string` | string | yes | Exact text to find and replace |
| `new_string` | string | yes | Replacement text |

Returns: Success message or error if `old_string` was not found.

---

## search_files

**Toolset:** `file`

Search for files by name pattern or content.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `pattern` | string | yes | Search pattern (file name glob or content regex) |
| `path` | string | no | Directory to search in (default: cwd) |
| `mode` | string | no | `"name"` or `"content"` (default: `"name"`) |

---

## list_directory

**Toolset:** `file`

List the contents of a directory.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `path` | string | yes | Directory path |
| `recursive` | boolean | no | List recursively (default: false) |

---

## browser_navigate

**Toolset:** `browser`
**Requires:** `BROWSERBASE_API_KEY` + `BROWSERBASE_PROJECT_ID`, or local Node.js

Open a URL in a browser session.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `url` | string | yes | URL to navigate to |
| `session_id` | string | no | Reuse an existing browser session |

---

## browser_screenshot

**Toolset:** `browser`

Capture a screenshot of the current browser page.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session_id` | string | yes | Browser session to screenshot |
| `full_page` | boolean | no | Capture full page (default: false = viewport only) |

---

## browser_click

**Toolset:** `browser`

Click an element on the current page.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session_id` | string | yes | Browser session |
| `selector` | string | yes | CSS selector or text to click |

---

## browser_fill

**Toolset:** `browser`

Fill a form field.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `session_id` | string | yes | Browser session |
| `selector` | string | yes | CSS selector for the input field |
| `value` | string | yes | Value to fill in |

---

## execute_code

**Toolset:** `code`

Execute Python code in a sandboxed subprocess.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `code` | string | yes | Python code to execute |
| `timeout` | integer | no | Execution timeout in seconds (default: 30) |

The sandbox:
- Strips API keys from the environment
- Provides RPC access to Hermes tools (the code can call `run_command`, `web_search`, etc.)
- Captures stdout, stderr, and return values

Returns: Combined output from the execution.

---

## delegate

**Toolset:** `delegate`

Spawn an isolated child agent to handle a subtask in parallel.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `task` | string | yes | Task description for the child agent |
| `model` | string | no | Model to use for the child agent |
| `tools` | list[string] | no | Tool names available to the child |

Returns: The child agent's final response when it completes.

---

## search_sessions

**Toolset:** `memory`

Search past conversations using full-text search.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `query` | string | yes | Search query |
| `limit` | integer | no | Maximum results (default: 5) |

---

## generate_image

**Toolset:** `image`
**Requires:** `FAL_KEY`

Generate an image using FAL.ai.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `prompt` | string | yes | Image generation prompt |
| `model` | string | no | FAL model to use |
| `size` | string | no | Image size (e.g., `"1024x1024"`) |

---

## create_cron_job

**Toolset:** `cron`

Schedule a recurring task.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `schedule` | string | yes | Cron expression (e.g., `"0 9 * * *"`) |
| `description` | string | yes | Task to run (used as the agent prompt) |
| `channel` | string | no | Delivery channel override |

---

## clarify

**Toolset:** `system`

Pause and ask the user a clarifying question before proceeding.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `question` | string | yes | Question to ask the user |
| `options` | list[string] | no | Multiple choice options |
