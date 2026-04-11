# Prerequisites

Everything you need before installing Hermes.

---

### Operating system

Hermes runs on Linux, macOS, WSL2, and Android (via Termux).

**Windows native is not supported.** Install [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) first, then follow the Linux instructions inside WSL2.

### Python 3.11+

```bash
python3 --version
# Should print: Python 3.11.x or higher
```

Install: [python.org/downloads](https://www.python.org/downloads/) or via your system package manager.

> **Tip:** Hermes uses `uv` for fast dependency installation. If you use the one-line installer, `uv` is installed automatically. If installing manually, install `uv` first: `curl -LsSf https://astral.sh/uv/install.sh | sh`

### An LLM API key

Hermes needs at least one API key to call a language model. The simplest option:

**OpenRouter** (access to 200+ models from one key):
- Sign up at [openrouter.ai/keys](https://openrouter.ai/keys)
- Copy your key — you'll paste it during `hermes setup`

Alternatively, use any of: Nous Portal, Anthropic direct, Google AI Studio (Gemini), OpenAI, z.ai/GLM, Kimi, MiniMax, Hugging Face Inference, or any OpenAI-compatible endpoint.

### Git

```bash
git --version
# Should print: git version 2.x.x
```

Install: [git-scm.com/downloads](https://git-scm.com/downloads) or via your system package manager.

### curl

```bash
curl --version
# Should print: curl 7.x.x or higher
```

Install: included on macOS; `sudo apt install curl` on Debian/Ubuntu.

---

## Optional prerequisites

These are only needed for specific features:

| Feature | Requirement |
|---------|-------------|
| Docker terminal backend | Docker Engine installed and running |
| SSH terminal backend | SSH access to a remote server |
| Modal terminal backend | `modal` Python package + `modal setup` |
| Browser automation (local) | Node.js 18+ (`node --version`) |
| Browser automation (cloud) | Browserbase API key |
| Voice transcription (local) | `pip install faster-whisper` |
| Voice transcription (cloud) | Groq or OpenAI API key |
| WhatsApp gateway | Node.js 18+ |
| Image generation | FAL.ai API key |
| Web search | Parallel, Exa, or Firecrawl API key |
| Honcho memory | Honcho API key + `~/.honcho/config.json` |
| RL training | Tinker API key + W&B API key |
