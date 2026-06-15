"""Flyte environment + the one place models are constructed.

Everything provider-specific lives here. The rest of the code calls
`get_model()` and never names an LLM provider — so you can switch models
by changing a single env var, with no code edits.
"""

import os

from dotenv import load_dotenv
import flyte
from langchain.chat_models import init_chat_model

load_dotenv()

# The only knob you need. Format is "provider:model".
#   openai:gpt-4.1-nano   anthropic:claude-sonnet-4-6   google_genai:gemini-2.0-flash
LLM_MODEL = os.getenv("LLM_MODEL", "openai:gpt-4.1-nano")

# Each provider SDK looks for its own env var. We map our single LLM_API_KEY
# onto the right one so attendees only ever manage ONE key, whatever provider
# they picked. (Add a row here to support more providers.)
_PROVIDER_KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
}


def get_model(**kwargs):
    """Return a LangChain chat model for whatever LLM_MODEL points at.

    LangGraph only needs a chat model; `init_chat_model` returns the *native*
    provider class (ChatOpenAI / ChatAnthropic / ...), so `.bind_tools()` and
    tool-calling work exactly as the provider intends.
    """
    provider = LLM_MODEL.split(":", 1)[0]
    api_key = os.getenv("LLM_API_KEY")
    env_var = _PROVIDER_KEY_ENV.get(provider)
    if api_key and env_var and not os.getenv(env_var):
        os.environ[env_var] = api_key
    return init_chat_model(LLM_MODEL, **kwargs)


# Compute environment for every task in this pipeline.
# Images are built and pushed to the devbox's in-cluster registry (localhost:30000).
base_env = flyte.TaskEnvironment(
    name="research-pipeline-env",
    image=flyte.Image.from_debian_base(
        registry="localhost:30000"
    ).with_requirements("requirements.txt"),
    # LLM_MODEL is your choice (not a secret) — bake it into the task so the
    # cluster uses the same provider you picked locally. The keys ARE secrets.
    env_vars={"LLM_MODEL": LLM_MODEL},
    secrets=[
        flyte.Secret(key="LLM_API_KEY", as_env_var="LLM_API_KEY"),
        flyte.Secret(key="TAVILY_API_KEY", as_env_var="TAVILY_API_KEY"),
    ],
    resources=flyte.Resources(cpu=2, memory="2Gi"),
)
