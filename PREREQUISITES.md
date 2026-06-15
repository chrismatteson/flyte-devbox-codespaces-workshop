# Prerequisites

Get these done **before** the workshop. There are two ways to participate — pick one.

---

## What you need either way

1. **An LLM API key** for one provider:
   - OpenAI (`sk-...`), or
   - Anthropic (`sk-ant-...`), or
   - Google Gemini.
   You only need **one**. You'll tell the app which provider you're using via `LLM_MODEL` (see below).
2. **A Tavily key** for web search — free tier at <https://tavily.com>.

---

## Option A — Local (clone + run on your machine)

Best if you have Docker and want everything local.

**You need:**
- **Docker Desktop** (or Docker Engine), running. The devbox runs a privileged container.
- **Python 3.11+**.
- Either **uv** (recommended) or **pip**.

**Set up:**

```bash
git clone https://github.com/unionai/flyte-devbox-codespaces-workshop.git
cd flyte-devbox-codespaces-workshop/research_agent
```

Install dependencies — **with uv** (fast):

```bash
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -r requirements.txt
```

…**or with pip**:

```bash
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Start the devbox (first run pulls a container image and boots Kubernetes — give it a few minutes):

```bash
flyte start devbox
```

Then jump to the [main README](README.md) walkthrough.

---

## Option B — GitHub Codespaces (nothing to install)

Best if you can't (or don't want to) install Docker locally.

1. On the repo: **Code → Codespaces → Create codespace on main**.
2. Wait for it to build. On creation the Codespace automatically:
   - enables Docker-in-Docker and installs `kubectl`,
   - installs the Python requirements,
   - **starts the Flyte devbox in the background** (a local Kubernetes cluster in a container).
3. Confirm the devbox is up (it takes a few minutes the first time):
   ```bash
   tail -f /tmp/devbox.log        # watch it come up; Ctrl-C to stop watching
   curl -fsS http://localhost:30080/readyz && echo "  <-- devbox UP"
   ```
4. The Flyte UI is on forwarded port **30080** at path **`/v2`**. Open the **Ports** tab, find 30080, and browse to `…/v2`.

> **Tip:** Open the Codespace in **VS Code Desktop** (command palette → "Open in VS Code Desktop"). Forwarded ports then tunnel to real `localhost`, so terminal links keep their full path — and the ports stay private, so your LLM key is never exposed.

> The devbox needs real resources. Create the Codespace on a **4-core / 16 GB** machine — the repo requests this automatically, but confirm it in the create dialog. A 2-core machine will hang while the cluster pods start.

Then jump to the [main README](README.md) walkthrough.

---

## API keys (both options)

Create your `.env` from the template and fill it in:

```bash
cd research_agent
cp .env.example .env
```

```ini
# .env
LLM_MODEL=openai:gpt-4.1-nano   # or anthropic:claude-sonnet-4-6, google_genai:gemini-2.0-flash
LLM_API_KEY=your-provider-key
TAVILY_API_KEY=your-tavily-key
```

`LLM_API_KEY` is **one** key — whichever provider you named in `LLM_MODEL`. You never edit code to switch providers; you change these two lines.

---

## Optional — Flyte MCP for your AI assistant

This repo ships config so your editor's AI assistant can query Flyte directly. It's a hosted, no-auth HTTP server, so there's nothing to install — your client just reads the file:

| Assistant | File (already in repo) |
|---|---|
| GitHub Copilot (VS Code / Codespaces) | `.vscode/mcp.json` |
| Claude Code | `.mcp.json` |
| Cursor | `.cursor/mcp.json` |

Open your assistant in agent/tools mode; if prompted, allow the `flyte` server to start. Sanity check from a terminal:

```bash
curl -fsS -i https://flyte-mcp.apps.demo.hosted.unionai.cloud/flyte-mcp/mcp | head -5
```
