# Add a tool

Implement and register a new tool that the agent can call during conversations.

This guide adds a hypothetical `send_slack_message` tool as the working example.

## Prerequisites

- Python 3.11+
- Hermes development install (the repo, not pip-installed)
- The virtual environment activated: `source venv/bin/activate`

## 1. Create the tool file

Create a new file in `tools/`:

```bash
touch tools/slack_tool.py
```

## 2. Implement the handler

```python
# tools/slack_tool.py
import os
from tools.registry import registry


def _send_slack_message(channel: str, text: str) -> str:
    """Send a message to a Slack channel via the Slack Web API."""
    import httpx

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        return "Error: SLACK_BOT_TOKEN not set"

    response = httpx.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {token}"},
        json={"channel": channel, "text": text},
        timeout=10,
    )
    data = response.json()
    if data.get("ok"):
        return f"Message sent to {channel} (ts={data['ts']})"
    return f"Error: {data.get('error', 'unknown error')}"


# Self-register at import time
registry.register(
    name="send_slack_message",
    toolset="slack",
    schema={
        "name": "send_slack_message",
        "description": "Send a message to a Slack channel.",
        "parameters": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Channel name or ID (e.g., #general or C01234567)",
                },
                "text": {
                    "type": "string",
                    "description": "Message text to send",
                },
            },
            "required": ["channel", "text"],
        },
    },
    handler=_send_slack_message,
    check_fn=lambda: bool(os.environ.get("SLACK_BOT_TOKEN")),
)
```

Key points:
- The handler is a plain Python function. It receives parsed argument values as keyword args.
- Return a string. The agent loop adds it to the conversation as a tool result.
- `check_fn` returns `True` when the tool is available. If it returns `False`, the tool's schema is excluded from the system prompt and the model never sees it.
- `toolset` groups the tool for bulk enable/disable. Use an existing toolset name or create a new one.

## 3. Register the import in model_tools.py

Add the import to `model_tools.py` so your tool file gets imported at startup (which triggers the self-registration):

```python
# model_tools.py — add this line with the other tool imports
import tools.slack_tool  # noqa: F401
```

## 4. Add the env var to toolset_distributions.py (optional)

If your tool requires an env var, add it to the toolset metadata in `tools/toolset_distributions.py`:

```python
"slack": ToolsetInfo(
    description="Send Slack messages",
    required_env_vars=["SLACK_BOT_TOKEN"],
),
```

This makes `hermes tools` display the correct setup instructions.

## 5. Test the tool

Write a unit test in `tests/tools/test_slack_tool.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from tools.slack_tool import _send_slack_message


def test_send_slack_message_success():
    mock_response = MagicMock()
    mock_response.json.return_value = {"ok": True, "ts": "1234567890.123"}

    with patch("httpx.post", return_value=mock_response):
        with patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb-test"}):
            result = _send_slack_message("#general", "Hello world")

    assert "Message sent to #general" in result


def test_send_slack_message_no_token():
    with patch.dict("os.environ", {}, clear=True):
        result = _send_slack_message("#general", "Hello")

    assert "SLACK_BOT_TOKEN not set" in result
```

Run:

```bash
pytest tests/tools/test_slack_tool.py -v
```

## 6. Verify the tool appears

Set the required env var and start Hermes:

```bash
export SLACK_BOT_TOKEN=xoxb-your-token
hermes tools
```

The `send_slack_message` tool should appear in the list as available.

## Verification

In a conversation:

```
you: send a message to #general saying "hello from hermes"
hermes: [calls send_slack_message(channel="#general", text="hello from hermes")]
        Message sent to #general (ts=1234567890.123)
```

## Troubleshooting

**Tool not appearing in `hermes tools`**
- Check that the import was added to `model_tools.py`
- Verify `check_fn` returns `True` when the env var is set
- Run `python -c "import tools.slack_tool"` and check for import errors

**Handler not called**
- Verify the `name` in `registry.register()` exactly matches the function name in the schema
- Check `model_tools.py` logs for dispatch errors
