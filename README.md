# Flyte v2 Devbox Workshop — LangGraph Research Agent

Build a research agent where **LangGraph controls the logic** (planning, fan-out, quality gates, looping) and **Flyte provides the compute** (every step is its own task with its own container, live report, and logs). You'll run it locally first, then on a real Kubernetes cluster — the **Flyte devbox** — running right inside Codespaces or your laptop.

> **New here? Start with [PREREQUISITES.md](PREREQUISITES.md).** It covers the two ways to participate (Codespaces or local) and the API keys you need. Come back here once your devbox is up.

## What you'll build

```
research_pipeline  (LangGraph pipeline, running inside a Flyte task)
  ├── plan_topics      (Flyte task)        → split the query into sub-topics
  ├── research         (Send fan-out → parallel Flyte tasks)
  │     ├── research_topic("topic A")  ┐
  │     ├── research_topic("topic B")  ├── each runs a ReAct agent + web search
  │     └── research_topic("topic C")  ┘
  ├── synthesize       (Flyte task)        → combine into one report
  ├── quality_check    (Flyte task)        → score + find gaps
  │     ├── gaps found → research again (loop)
  │     └── good enough → finalize
  └── finalize                             → final report
```

Each `research_topic` runs a LangGraph **ReAct** agent that searches the web via [Tavily](https://tavily.com/) and loops until it has enough to write a summary.

## Pick any LLM — one knob, one key

You never edit code to change models. Set two values in `research_agent/.env`:

| `LLM_MODEL` | `LLM_API_KEY` is your… |
|---|---|
| `openai:gpt-4.1-nano` | OpenAI key |
| `anthropic:claude-sonnet-4-6` | Anthropic key |
| `google_genai:gemini-2.0-flash` | Google key *(also `pip install langchain-google-genai`)* |

All model construction lives in one place — `get_model()` in [research_agent/config.py](research_agent/config.py) — so the rest of the code is provider-agnostic.

---

## Walkthrough

All commands run from the `research_agent/` directory:

```bash
cd research_agent
```

Make sure `.env` is filled in (see [PREREQUISITES.md](PREREQUISITES.md)).

### 1. Run locally — no cluster needed

Everything runs in-process. Great for a first look at the logic:

```bash
flyte run --local --tui workflow.py research_pipeline \
  --query "Compare quantum computing approaches: superconducting vs trapped ion"
```

Smaller/faster run:

```bash
flyte run --local workflow.py research_pipeline \
  --query "What are the pros and cons of electric vehicles?" \
  --num_topics 2 --max_searches 1 --max_iterations 1
```

### 2. Point Flyte at the devbox

The devbox should already be running (Codespaces starts it for you; locally you ran `flyte start devbox`). Verify and configure:

```bash
curl -fsS http://localhost:30080/readyz && echo "  <-- devbox UP"

flyte create config \
  --endpoint localhost:30080 \
  --project flytesnacks \
  --domain development \
  --builder local \
  --insecure
```

That writes `.flyte/config.yaml` in this directory.

<details>
<summary><b>Optional: use the hosted <code>tryv2</code> cluster instead of the devbox</b></summary>

If you'd rather run against Union's hosted cluster (no local devbox needed), point the config there instead. Replace `<your-project>` with your project name on `tryv2`:

```bash
flyte create config \
  --endpoint tryv2.hosted.unionai.cloud \
  --project <your-project> \
  --domain development
```

Notes:
- No `--insecure` and no `--builder local` — it's a real, TLS-secured cluster that builds images for you.
- Everything else in this walkthrough is identical; just skip the devbox steps and the `localhost:30080` references (use the cluster's own UI).
- Create your secrets against the same project/domain (step 3), e.g. `--project <your-project>`.

</details>

### 3. Create the secrets

The tasks run on the cluster, so they need the keys as **Flyte secrets** (not just your local `.env`). You provide one LLM key plus Tavily:

```bash
flyte create secret LLM_API_KEY --project flytesnacks --domain development
flyte create secret TAVILY_API_KEY --project flytesnacks --domain development
```

> Use the key that matches your `LLM_MODEL` provider. The task maps `LLM_API_KEY` onto the right provider variable automatically.

### 4. Run on the devbox

```bash
flyte run workflow.py research_pipeline \
  --query "Compare quantum computing approaches" \
  --num_topics 2 --max_searches 2 --max_iterations 1
```

The command prints a run URL. Open the **Flyte UI on port 30080 at `/v2`** to watch each task execute with its own live report. (In Codespaces: **Ports** tab → 30080 → add `/v2` to the path.)

### 5. (Optional) Serve it as an app

Register the tasks/build images, then run the Gradio UI:

```bash
flyte deploy workflow.py env     # register tasks + build images on the devbox

python app.py                    # local UI driving the remote tasks
# or deploy the UI to the cluster:
flyte deploy app.py serving_env
```

| Flag | Default | Description |
|------|---------|-------------|
| `--query` | required | Research question |
| `--num_topics` | 3 | Sub-topics researched in parallel |
| `--max_searches` | 2 | Max web searches per sub-topic |
| `--max_iterations` | 2 | Max quality-gate loops |

---

## Extra Credit (optional)
Point your favorite AI tool (Claude Code, Cursor, …) at the workshop MCP and paste a prompt. Pick one (or both):
🧪 Be an ML engineer

Invent a few feature recipes for predicting [taco ratings / asteroid danger — you pick], train a model on each recipe in parallel, then write me a short report: which one you'd ship and why, with a recipe scoreboard and loss curves.

🦾 MLE agent that writes its own training run

Turn loose an ML-engineer agent on predicting [taco ratings / asteroid danger — your pick]: let it invent features, write and run its own training, read the score, and iterate to beat its last attempt. Then write me a short report: the score-per-iteration climb and the model it shipped.

---

## How it works

- **`config.py`** — `get_model()` reads `LLM_MODEL`, maps your single `LLM_API_KEY` onto the provider's expected env var, and returns the native LangChain chat model via `init_chat_model`. Switch providers without touching pipeline code.
- **`graph.py`** — two LangGraph graphs: `build_research_subgraph()` is the ReAct loop (agent ↔ tools) for one topic; `build_pipeline_graph()` wires the pipeline where each node just dispatches to a Flyte task.
- **`workflow.py`** — five Flyte tasks (`plan_topics`, `research_topic`, `synthesize`, `quality_check`, and the `research_pipeline` orchestrator). Every step is visible in the UI with its own compute and report.

The graph nodes are thin wrappers — state in, task call, state out — while all LLM calls happen inside the Flyte tasks.

See the [blog post](https://www.union.ai/blog-post/langgraph-on-flyte-orchestrate-the-logic-scale-the-compute) for the full walkthrough.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Devbox hangs partway through "waiting for cluster" | Not enough resources. Use a 4-core / 16 GB Codespace (or give Docker Desktop more CPU/RAM). |
| Browser shows `404 page not found` on port 30080 | The console lives at **`/v2`**, not `/`. Use `…-30080.app.github.dev/v2`. |
| Browser shows S3 XML `<Error>AccessDenied</Error>` | You opened the wrong forwarded port (the object store). Use **30080**, not 30000–30003. |
| `flyte: command not found` | Activate your venv, or in Codespaces wait for `postCreate` to finish installing. |
| `flyte start devbox` errors that Docker isn't running | Start Docker Desktop (local), or wait for docker-in-docker to finish (Codespaces). |
