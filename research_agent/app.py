"""Gradio UI for the research pipeline — kicks off the agent as a Flyte task.

Auth passthrough (works on both backends, same code):
  - On a Union cluster *with* auth, the logged-in user's credentials ride in on
    the request, so each run executes **as that user**.
  - On the no-auth devbox, there are no credentials to forward, so it just runs
    as the app's own identity.

Progression:
  1. Local app + local task:   RUN_MODE=local python app.py
  2. Local app + remote task:  python app.py            (needs: flyte deploy workflow.py env)
  3. Deploy app to the cluster: flyte deploy app.py serving_env
"""

import os
from contextlib import nullcontext

from dotenv import load_dotenv
import gradio as gr
import flyte
import flyte.app
import flyte.remote as remote
from flyte.remote import auth_metadata

load_dotenv()

RUN_MODE = os.getenv("RUN_MODE", "remote")
# Set APP_REQUIRES_AUTH=true when deploying to a Union cluster with auth in front.
# Leave it unset/false for the devbox, which serves apps without auth.
REQUIRES_AUTH = os.getenv("APP_REQUIRES_AUTH", "false").lower() == "true"

# The app must look for the task in the SAME project/domain it was deployed to
# (`flyte deploy workflow.py env`). Defaults match the devbox; override for tryv2.
FLYTE_PROJECT = os.getenv("FLYTE_PROJECT", "flytesnacks")
FLYTE_DOMAIN = os.getenv("FLYTE_DOMAIN", "development")
# Devbox is plaintext (set FLYTE_INSECURE=true); a real cluster like tryv2 has TLS.
FLYTE_INSECURE = os.getenv("FLYTE_INSECURE", "false").lower() == "true"

# Flipped on by the deployed server once passthrough auth is initialized.
_PASSTHROUGH = False

serving_env = flyte.app.AppEnvironment(
    name="research-pipeline-ui",
    image=flyte.Image.from_debian_base(python_version=(3, 11)).with_pip_packages(
        "flyte>=2.1.4", "gradio", "python-dotenv",
    ),
    resources=flyte.Resources(cpu=1, memory="1Gi"),
    requires_auth=REQUIRES_AUTH,
    # Carry the deploy-time project/domain into the running container so the
    # task lookup at runtime matches where you deployed the workflow.
    env_vars={
        "FLYTE_PROJECT": FLYTE_PROJECT,
        "FLYTE_DOMAIN": FLYTE_DOMAIN,
        "FLYTE_INSECURE": str(FLYTE_INSECURE).lower(),
    },
    port=7860,
    scaling=flyte.app.Scaling(replicas=(0, 1), scaledown_after=300),
)

# Pre-registered task reference — fetched from the control plane at runtime.
# Deploy tasks first: flyte deploy workflow.py env
research_pipeline_task = remote.Task.get(
    "research-pipeline-env.research_pipeline",
    project=FLYTE_PROJECT,
    domain=FLYTE_DOMAIN,
    auto_version="latest",
)


def _auth_tuples(request):
    """Pull the caller's auth headers off the Gradio request (passthrough mode only)."""
    if not (_PASSTHROUGH and request is not None):
        return []
    tuples = []
    for header in ("authorization", "cookie"):
        value = request.headers.get(header)
        if value:
            tuples.append((header, value))
    return tuples


def _auth_ctx(auth):
    """Context manager that forwards `auth` to Flyte, or a no-op when there's none.

    Each Flyte call is wrapped in its own short-lived context (no `yield` inside),
    so the auth contextvar is set and cleared within a single handler step —
    avoiding contextvar-across-yield pitfalls with Gradio's streaming handlers.
    """
    return auth_metadata(*auth) if auth else nullcontext()


def run_query(query, num_topics, max_searches, max_iterations, request: gr.Request = None):
    """Kick off the research pipeline as a Flyte task, stream URL then result."""
    if RUN_MODE == "local":
        from workflow import research_pipeline
        task = research_pipeline
    else:
        task = research_pipeline_task

    auth = _auth_tuples(request)

    with _auth_ctx(auth):
        result = flyte.run(
            task,
            query=query,
            num_topics=int(num_topics),
            max_searches=int(max_searches),
            max_iterations=int(max_iterations),
        )

    # Show the run link immediately. On the devbox, result.url points at the
    # in-cluster service host — rewrite it to the devbox console on localhost.
    # (No-op on a real cluster like tryv2, where result.url is already external.)
    run_url = getattr(result, "url", None)
    if run_url:
        run_url = str(run_url).replace("flyte-binary-http.flyte:8090", "localhost:30080")
    link_html = f'<a href="{run_url}" target="_blank">View run on Flyte</a>' if run_url else ""
    yield "", (link_html or "Running...")

    # Wait for completion, then show the report
    with _auth_ctx(auth):
        result.wait()
        output = result.outputs()[0]

    # Handle both PipelineResult (local) and dict (remote) outputs
    if hasattr(output, "report"):
        report = output.report
        score = output.score
        iterations = output.iterations
    else:
        report = output["report"]
        score = output.get("score", "N/A")
        iterations = output.get("iterations", "N/A")

    header = f"**Quality:** {score}/10 | **Iterations:** {iterations}\n\n---\n\n"
    yield header + report, link_html


def create_demo():
    """Build the Gradio interface."""
    with gr.Blocks(title="Research Agent") as demo:
        gr.Markdown("# Research Agent\nAsk a question — the agent searches the web via Tavily and synthesizes a report.")

        with gr.Row():
            query = gr.Textbox(label="Research Question", placeholder="Compare quantum computing approaches: superconducting vs trapped ion", scale=3)
            submit = gr.Button("Research", variant="primary", scale=1)

        with gr.Row():
            num_topics = gr.Slider(minimum=1, maximum=10, value=3, step=1, label="Sub-topics")
            max_searches = gr.Slider(minimum=1, maximum=5, value=2, step=1, label="Max searches per topic")
            max_iterations = gr.Slider(minimum=1, maximum=5, value=2, step=1, label="Max quality iterations")

        run_link = gr.HTML()
        report = gr.Markdown(label="Report")

        inputs = [query, num_topics, max_searches, max_iterations]
        submit.click(fn=run_query, inputs=inputs, outputs=[report, run_link])
        query.submit(fn=run_query, inputs=inputs, outputs=[report, run_link])

        gr.Examples(
            examples=[
                ["Compare quantum computing approaches: superconducting vs trapped ion"],
                ["What are the pros and cons of electric vehicles?"],
                ["How is AI being used in drug discovery?"],
            ],
            inputs=query,
        )

    return demo


@serving_env.server
def app_server():
    """Launch the Gradio app (called by Flyte on remote deployment)."""
    global _PASSTHROUGH
    if REQUIRES_AUTH:
        # Auth'd cluster (e.g. tryv2): forward the caller's identity via passthrough.
        flyte.init_passthrough(project=FLYTE_PROJECT, domain=FLYTE_DOMAIN, insecure=FLYTE_INSECURE)
        _PASSTHROUGH = True
    else:
        # No-auth devbox: run as the app's own in-cluster identity. This sets up
        # the cluster transport (incl. plaintext) correctly on its own.
        flyte.init_in_cluster(project=FLYTE_PROJECT, domain=FLYTE_DOMAIN)
    create_demo().launch(server_name="0.0.0.0", server_port=7860, share=False)


if __name__ == "__main__":
    if RUN_MODE != "local":
        flyte.init_from_config()

    create_demo().launch()
