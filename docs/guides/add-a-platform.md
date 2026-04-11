# Add a platform

Integrate a new messaging platform into the Hermes gateway so users can talk to the agent from that platform.

Also see the existing guide at `gateway/platforms/ADDING_A_PLATFORM.md` in the repo.

## Prerequisites

- Python 3.11+ and the Hermes development install
- A working account and API credentials for the new platform
- Understanding of the [gateway architecture](../concepts/gateway.md)

## 1. Create the platform adapter file

```bash
touch gateway/platforms/myplatform.py
```

## 2. Implement BasePlatform

Every platform adapter inherits from `gateway/platforms/base.py:BasePlatform` and implements its interface:

```python
# gateway/platforms/myplatform.py
from gateway.platforms.base import BasePlatform
from gateway.session import SessionStore
import asyncio
import logging

logger = logging.getLogger(__name__)


class MyPlatform(BasePlatform):
    """Platform adapter for MyPlatform."""

    def __init__(self, config: dict, session_store: SessionStore):
        super().__init__(config, session_store)
        self.token = config.get("token") or os.environ.get("MYPLATFORM_BOT_TOKEN")
        self.allowed_users = self._parse_allowed_users(
            config.get("allowed_users") or os.environ.get("MYPLATFORM_ALLOWED_USERS", "")
        )

    async def start(self) -> None:
        """Start listening for messages. Called once by GatewayRunner."""
        logger.info("Starting MyPlatform adapter")
        # Initialize your platform client here
        # e.g., self.client = MyPlatformSDK(token=self.token)
        await self._poll_messages()

    async def stop(self) -> None:
        """Stop the adapter cleanly."""
        logger.info("Stopping MyPlatform adapter")
        # Disconnect, close connections, etc.

    async def send_message(self, chat_id: str, text: str, **kwargs) -> None:
        """Send a text message to a chat."""
        # Call your platform's send API here
        await self.client.send_message(chat_id=chat_id, text=text)

    async def send_typing(self, chat_id: str) -> None:
        """Send a typing indicator (optional — implement as no-op if unsupported)."""
        await self.client.send_typing(chat_id=chat_id)

    async def _poll_messages(self) -> None:
        """Main polling loop (if the platform uses polling rather than webhooks)."""
        while True:
            messages = await self.client.get_updates()
            for msg in messages:
                await self._handle_incoming(msg)
            await asyncio.sleep(1)

    async def _handle_incoming(self, msg) -> None:
        """Convert a platform message to a Hermes session and process it."""
        user_id = str(msg.sender_id)
        chat_id = str(msg.chat_id)
        text = msg.text

        # Check allow-list
        if self.allowed_users and user_id not in self.allowed_users:
            logger.debug("Ignoring message from unlisted user %s", user_id)
            return

        # Get or create a session for this (platform, chat_id, user_id) tuple
        session = await self.session_store.get_or_create(
            platform="myplatform",
            chat_id=chat_id,
            user_id=user_id,
        )

        # Route the message through the session (calls AIAgent internally)
        await session.handle_message(text, send_fn=self.send_message)
```

## 3. Register the platform in GatewayRunner

Add your platform to `gateway/run.py`'s platform initialization:

```python
# gateway/run.py
from gateway.platforms.myplatform import MyPlatform

PLATFORM_CLASSES = {
    "telegram": TelegramPlatform,
    "discord": DiscordPlatform,
    "slack": SlackPlatform,
    # ... existing platforms ...
    "myplatform": MyPlatform,      # ← Add this
}
```

## 4. Add configuration support

Add env var documentation to `.env.example`:

```bash
# =============================================================================
# MYPLATFORM INTEGRATION
# =============================================================================
# MyPlatform Bot Token
# MYPLATFORM_BOT_TOKEN=
# MYPLATFORM_ALLOWED_USERS=          # Comma-separated user IDs
```

Add to `hermes_cli/config.py` under `OPTIONAL_ENV_VARS`:

```python
"MYPLATFORM_BOT_TOKEN": EnvVarInfo(
    description="MyPlatform bot token",
    required_for=["gateway:myplatform"],
    secret=True,
),
```

## 5. Add to the gateway setup wizard

In `hermes_cli/gateway.py`, add MyPlatform to the interactive setup flow so users can configure it with `hermes gateway setup`.

## 6. Write tests

Create `tests/gateway/test_myplatform.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch
from gateway.platforms.myplatform import MyPlatform
from tests.fakes.session_store import FakeSessionStore


@pytest.fixture
def platform():
    config = {"token": "test-token", "allowed_users": "user123"}
    return MyPlatform(config, FakeSessionStore())


@pytest.mark.asyncio
async def test_ignores_unlisted_user(platform):
    msg = FakeMessage(sender_id="unknown_user", text="hello")
    await platform._handle_incoming(msg)
    # Assert no session was created / no response sent

@pytest.mark.asyncio
async def test_routes_allowed_user(platform):
    msg = FakeMessage(sender_id="user123", text="hello")
    with patch.object(platform.session_store, "get_or_create") as mock_get:
        mock_session = AsyncMock()
        mock_get.return_value = mock_session
        await platform._handle_incoming(msg)
        mock_session.handle_message.assert_called_once_with("hello", send_fn=platform.send_message)
```

## Verification

1. Set the bot token: `export MYPLATFORM_BOT_TOKEN=your-token`
2. Start the gateway: `hermes gateway start`
3. Send a message from the platform
4. Verify the response arrives

## Troubleshooting

**Messages received but no response**
- Check the allow-list configuration
- Look at gateway logs: `hermes logs`

**Platform not starting**
- Verify `MYPLATFORM_BOT_TOKEN` is set
- Check for errors in `hermes gateway start` output
- Verify the platform is listed in `PLATFORM_CLASSES`
