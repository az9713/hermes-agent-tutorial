# Common issues

Top problems and their fixes, ordered by frequency.

---

## "hermes: command not found" after installation

**Cause:** The shell's PATH hasn't been updated to include `~/.local/bin`.

**Fix:**
```bash
source ~/.bashrc    # or: source ~/.zshrc
```

If that doesn't work:
```bash
echo $PATH
# If ~/.local/bin is missing:
export PATH="$HOME/.local/bin:$PATH"
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

---

## 401 Unauthorized when starting a conversation

**Cause:** The API key is missing, expired, or misconfigured.

**Fix:**
```bash
hermes config show    # Check which provider and key is configured
hermes doctor         # Run full diagnostics
```

If using OpenRouter:
```bash
hermes config set OPENROUTER_API_KEY sk-or-your-new-key
```

If using Anthropic direct:
```bash
hermes config set ANTHROPIC_API_KEY sk-ant-your-key
```

**If that doesn't work:** Verify the key works with a direct API call:
```bash
curl https://openrouter.ai/api/v1/models \
  -H "Authorization: Bearer $OPENROUTER_API_KEY"
```

---

## Agent stops mid-task with "context length exceeded"

**Cause:** The conversation history has grown beyond the model's context window, and auto-compression didn't trigger (or the threshold is too high).

**Fix — manually compress now:**
```
/compress
```

**Fix — lower the compression threshold:**
```yaml
# ~/.hermes/config.yaml
compression:
  threshold: 0.75    # Compress earlier (at 75% instead of 85%)
```

**Fix — check context_length is set correctly:**

Some models auto-detect the wrong context length. Override it:
```yaml
model:
  context_length: 131072    # Set the correct limit for your model
```

---

## Gateway connects but Telegram bot doesn't respond

**Cause 1:** The user is not on the allow-list.

**Fix:**
```bash
# Find your Telegram user ID: message @userinfobot on Telegram
# Then add it:
hermes config set TELEGRAM_ALLOWED_USERS 123456789
hermes gateway restart
```

**Cause 2:** The bot token is invalid or revoked.

**Fix:**
```bash
# Get a fresh token from @BotFather on Telegram
hermes config set TELEGRAM_BOT_TOKEN 1234567890:ABCdef...
hermes gateway restart
```

**Cause 3:** Another process is using the same bot token (long-polling conflict).

**Fix:** Check for other running gateway instances:
```bash
ps aux | grep "hermes gateway"
# Kill any duplicate processes, then restart
hermes gateway restart
```

---

## `run_command` hangs or times out

**Cause 1:** The command itself is hanging (waiting for input, network issue, etc.).

**Fix:** Interrupt the current task (`Ctrl+C` in CLI, `/stop` in gateway), then try with a shorter timeout:
```
you: run "ls -la" with a 5-second timeout
```

**Cause 2:** The terminal backend is not responding (Docker container crashed, SSH disconnected, Modal sandbox timed out).

**Fix:**
```bash
# Restart the backend (next command creates a fresh one)
hermes config set terminal.lifetime_seconds 1
# Run any command to trigger backend restart
hermes
# Then: run ls
# Then reset the lifetime back to normal
hermes config set terminal.lifetime_seconds 300
```

---

## "Tool not available" for web_search

**Cause:** No web search API key is configured.

**Fix:** Add at least one:
```bash
# Option 1: Parallel AI
hermes config set PARALLEL_API_KEY your-parallel-key

# Option 2: Exa
hermes config set EXA_API_KEY your-exa-key

# Option 3: Firecrawl
hermes config set FIRECRAWL_API_KEY your-firecrawl-key
```

Then verify:
```bash
hermes tools
# web_search should now show as available
```

---

## Agent keeps writing to wrong files / accessing wrong directory

**Cause:** The working directory (`terminal.cwd`) is not set to the expected path.

**Fix:**
```bash
# Check current setting
hermes config show | grep cwd

# Set the correct path
hermes config set terminal.cwd /path/to/your/project
```

For the CLI, you can also just run `hermes` from the correct directory (the default is `"."`).

---

## Memory not persisting across sessions

**Cause 1:** Memory is disabled in config.

**Fix:**
```yaml
# ~/.hermes/config.yaml
memory:
  enabled: true
```

**Cause 2:** Memory files don't exist yet (first session).

**Fix:** Memory files are created automatically after the first conversation where something is worth remembering. Have a conversation and explicitly ask Hermes to remember something:
```
you: please remember my name is Alice and I prefer Python
```

**Cause 3:** Using Honcho but it's not configured.

**Fix:**
```bash
cat ~/.honcho/config.json
# Should contain: {"enabled": true}
# If missing or disabled:
echo '{"enabled": true}' > ~/.honcho/config.json
```

---

## Skills not loading / skill commands not appearing

**Cause 1:** Skills are in the wrong directory.

**Fix:**
```bash
ls ~/.hermes/skills/
# Each skill should be a directory with a SKILL.md file inside
```

**Cause 2:** Platform filtering is excluding the skill.

**Fix:** Check `SKILL.md` for a `HERMES_SKILL_PLATFORMS` comment and verify it includes your platform.

**Cause 3:** Required tools for the skill are not available.

**Fix:** Run `hermes tools` and check whether the tools the skill requires are shown as available.

---

## hermes doctor output

`hermes doctor` runs a comprehensive diagnostic. Run it when something is wrong:

```bash
hermes doctor
```

It checks:
- Python version and virtual environment
- All configured API keys (validates format, not actual API calls)
- Config file structure
- Memory files
- Skill directory
- Gateway platform configurations
- Terminal backend availability

The output guides you to specific fixes for any failed checks.
