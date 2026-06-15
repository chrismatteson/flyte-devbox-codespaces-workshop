"""
Research pipeline workflow — LangGraph controls the pipeline, Flyte provides the compute.

Each pipeline step is a separate Flyte task visible in the UI:
- plan_topics: break query into sub-topics
- research_topic: ReAct agent researches one sub-topic (parallel via Send)
- synthesize: combine sub-topic reports into a unified synthesis
- quality_check: evaluate quality and identify gaps

LangGraph manages the flow: plan → research → synthesize → quality check → loop

Usage:
    # Local (no cluster)
    flyte run --local --tui workflow.py research_pipeline --query "Compare quantum computing approaches"

    # On the devbox cluster
    flyte run workflow.py research_pipeline --query "Compare quantum computing approaches"
"""

import json
import os
import base64
import logging

import markdown

import flyte
import flyte.report
from langchain_core.messages import HumanMessage

from config import base_env, get_model
from models import TopicReport, QualityResult, PipelineResult
from graph import build_pipeline_graph, build_research_subgraph

logging.basicConfig(level=logging.WARNING, format="%(message)s", force=True)
log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
logging.getLogger("graph").setLevel(logging.INFO)
logging.getLogger("tools.search").setLevel(logging.INFO)

env = base_env


def md_to_html(text: str) -> str:
    """Convert markdown to HTML for Flyte reports."""
    return markdown.markdown(text, extensions=["tables", "fenced_code"])


# ------------------------------------------------------------------
# Flyte tasks — each step is visible in the UI while running
# ------------------------------------------------------------------

@env.task(report=True)
async def plan_topics(query: str, num_topics: int = 3) -> list[str]:
    """Break a research query into focused sub-topics."""
    log.info(f"Planning {num_topics} sub-topics for: {query}")

    await flyte.report.replace.aio(
        f"<h2>Planning</h2><p>Breaking query into {num_topics} sub-topics...</p>"
    )
    await flyte.report.flush.aio()

    llm = get_model()
    response = llm.invoke(
        f"Break this research question into exactly {num_topics} focused sub-topics. "
        f"Return ONLY a JSON array of strings, nothing else.\n\nQuestion: {query}"
    )
    
    # 1. Safely extract the raw text string from the response content wrapper
    content = response.content
    raw_text = ""

    if isinstance(content, dict) and "text" in content:
        raw_text = content["text"]
    elif isinstance(content, list) and len(content) > 0:
        if isinstance(content[0], dict) and "text" in content[0]:
            raw_text = content[0]["text"]
        else:
            raw_text = str(content[0])
    elif isinstance(content, str):
        raw_text = content
    else:
        raw_text = str(content)

    # 2. Parse the extracted text string into a native Python list
    try:
        topics = json.loads(raw_text)
        if not isinstance(topics, list):
            topics = [query]
    except (json.JSONDecodeError, TypeError):
        log.warning(f"Failed to parse LLM response as JSON. Raw text was: {raw_text}")
        topics = [query]

    # Trim to requested number of topics
    topics = topics[:num_topics]
    log.info(f"Sub-topics: {topics}")

    topic_html = "".join(f"<li>{t}</li>" for t in topics)
    await flyte.report.replace.aio(
        f"<h2>Planning</h2><p>Sub-topics:</p><ul>{topic_html}</ul>"
    )
    await flyte.report.flush.aio()

    return topics


@env.task(report=True)
async def research_topic(topic: str, max_searches: int = 2) -> TopicReport:
    """Run the ReAct research agent on a single sub-topic."""
    log.info(f"[Research Task] Starting: {topic}")

    tavily_api_key = os.getenv("TAVILY_API_KEY")

    await flyte.report.replace.aio(f"<h2>Researching: {topic}</h2><p>Running searches...</p>")
    await flyte.report.flush.aio()

    graph = build_research_subgraph(tavily_api_key=tavily_api_key, max_searches=max_searches)
    result = await graph.ainvoke({"messages": [HumanMessage(content=f"Research this topic: {topic}")]})
    
    # 1. Safely extract the content block
    content = result["messages"][-1].content
    
    # 2. Flatten Gemini's list/dict response format into a single raw text string
    if isinstance(content, list):
        report = "".join(
            block["text"] if isinstance(block, dict) and "text" in block else str(block)
            for block in content
        )
    elif isinstance(content, dict) and "text" in content:
        report = content["text"]
    elif isinstance(content, str):
        report = content
    else:
        report = str(content)

    log.info(f"[Research Task] Done: {topic}")

    # 3. Now 'report' is a guaranteed string, so md_to_html and Flyte type checkers work perfectly
    await flyte.report.replace.aio(f"<h2>{topic}</h2>{md_to_html(report)}")
    await flyte.report.flush.aio()

    return TopicReport(topic=topic, report=report)


@env.task(report=True)
async def synthesize(query: str, results: list[TopicReport]) -> str:
    """Combine sub-topic research reports into a unified synthesis."""
    log.info(f"Synthesizing {len(results)} report(s)...")

    await flyte.report.replace.aio(
        f"<h2>Synthesis</h2><p>Combining {len(results)} reports...</p>"
    )
    await flyte.report.flush.aio()

    llm = get_model()
    sections = "\n\n---\n\n".join(
        f"## {r.topic}\n\n{r.report}" for r in results
    )

    response = llm.invoke(
        f"You have research reports on sub-topics of this question:\n\n{query}\n\n"
        f"Sub-topic reports:\n\n{sections}\n\n"
        f"Write a comprehensive report that synthesizes all findings. "
        f"Organize by theme, highlight connections between sub-topics, "
        f"and end with key takeaways."
    )
    
    # 1. Safely extract and flatten Gemini 3's block-list response content
    content = response.content
    synthesis_text = ""

    if isinstance(content, list):
        synthesis_text = "".join(
            block["text"] if isinstance(block, dict) and "text" in block else str(block)
            for block in content
        )
    elif isinstance(content, dict) and "text" in content:
        synthesis_text = content["text"]
    elif isinstance(content, str):
        synthesis_text = content
    else:
        synthesis_text = str(content)

    # 2. Clean it up and assign to your variable as a single valid string
    synthesis = synthesis_text.strip()
    
    # This now counts text characters correctly instead of counting items in a list!
    log.info(f"Synthesis complete: {len(synthesis)} chars")

    # 3. Pass the clean string safely to your utilities and Flyte runner
    await flyte.report.replace.aio(f"<h2>Synthesis</h2>{md_to_html(synthesis)}")
    await flyte.report.flush.aio()

    return synthesis


@env.task(report=True)
async def quality_check(query: str, synthesis: str) -> QualityResult:
    """Evaluate report quality and identify gaps."""
    log.info("Evaluating quality...")

    await flyte.report.replace.aio(
        "<h2>Quality Check</h2><p>Evaluating report quality...</p>"
    )
    await flyte.report.flush.aio()

    llm = get_model()
    response = llm.invoke(
        f'Evaluate this research report for the question: {query}\n\n'
        f'Report:\n{synthesis}\n\n'
        f'Rate the report quality from 1-10 and identify any gaps or missing perspectives. '
        f'Return JSON: {{"score": <int>, "gaps": [<string>, ...]}}\n'
        f'If the report is comprehensive (score >= 8) or there are no significant gaps, '
        f'return an empty gaps list.'
    )

    # 1. Extract and flatten Gemini 3.1 block-list structure into a single raw text string
    content = response.content
    raw_text = ""

    if isinstance(content, list):
        raw_text = "".join(
            block["text"] if isinstance(block, dict) and "text" in block else str(block)
            for block in content
        )
    elif isinstance(content, dict) and "text" in content:
        raw_text = content["text"]
    elif isinstance(content, str):
        raw_text = content
    else:
        raw_text = str(content)

    # 2. Clean markdown code block markers if the model wrapped the JSON string
    raw_text = raw_text.strip()
    if raw_text.startswith("```json"):
        raw_text = raw_text[7:]
    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]
    raw_text = raw_text.strip()

    # 3. Parse the guaranteed string object
    try:
        evaluation = json.loads(raw_text)
        score = evaluation.get("score", 8)
        gaps = evaluation.get("gaps", [])
    except (json.JSONDecodeError, TypeError):
        log.warning(f"Failed to parse quality evaluation JSON. Raw text was: {raw_text}")
        score = 8
        gaps = []

    result = QualityResult(score=score, gaps=gaps)
    log.info(f"Score: {result.score}/10, Gaps: {len(result.gaps)}")

    gap_html = "".join(f"<li>{g}</li>" for g in result.gaps) if result.gaps else "<li>None</li>"
    await flyte.report.replace.aio(
        f"<h2>Quality Check</h2>"
        f"<p><b>Score:</b> {result.score}/10</p>"
        f"<p><b>Gaps:</b></p><ul>{gap_html}</ul>"
    )
    await flyte.report.flush.aio()

    return result


# ------------------------------------------------------------------
# Orchestrator: runs the LangGraph pipeline, backed by Flyte tasks
# ------------------------------------------------------------------

@env.task(report=True)
async def research_pipeline(
    query: str,
    num_topics: int = 3,
    max_searches: int = 2,
    max_iterations: int = 2,
) -> PipelineResult:
    """
    Research pipeline workflow:
    1. LangGraph plans sub-topics via plan_topics Flyte task
    2. LangGraph fans out research via Send → each dispatches to research_topic Flyte task
    3. LangGraph synthesizes results via synthesize Flyte task
    4. LangGraph evaluates quality via quality_check Flyte task
    5. If gaps found, loops back to step 2
    """
    log.info(f"Starting research pipeline: {query}")

    tavily_api_key = os.getenv("TAVILY_API_KEY")

    # Build the pipeline graph, passing all Flyte tasks as compute backends
    pipeline = build_pipeline_graph(
        plan_task=plan_topics,
        research_task=research_topic,
        synthesize_task=synthesize,
        quality_check_task=quality_check,
    )

    # Visualize the graphs in report tabs
    graph_tab = flyte.report.get_tab("Agent Graphs")

    png_bytes = pipeline.get_graph().draw_mermaid_png()
    img_b64 = base64.b64encode(png_bytes).decode()
    graph_tab.log(f"""\
<h2>Research Pipeline</h2>\
<img src="data:image/png;base64,{img_b64}" alt="Research pipeline" />""")

    subgraph = build_research_subgraph(tavily_api_key=tavily_api_key, max_searches=max_searches)
    sub_png = subgraph.get_graph().draw_mermaid_png()
    sub_b64 = base64.b64encode(sub_png).decode()
    graph_tab.log(f"""\
<h2>Research Agent (ReAct)</h2>\
<img src="data:image/png;base64,{sub_b64}" alt="ReAct research agent" />""")
    await flyte.report.flush.aio()

    # Run the pipeline — LangGraph controls the flow, Flyte tasks run the compute
    result = await pipeline.ainvoke({
        "query": query,
        "num_topics": num_topics,
        "max_searches": max_searches,
        "max_iterations": max_iterations,
        "iteration": 0,
        "topics": [],
        "research_results": [],
        "synthesis": "",
        "score": 0,
        "gaps": [],
        "final_report": "",
    })

    # Build the final report
    final_report = result["final_report"]
    sub_reports = [TopicReport(**r) for r in result["research_results"]]
    score = result.get("score", 0)
    iteration = result.get("iteration", 1) - 1

    await flyte.report.replace.aio(f"""\
<h2>Research Report</h2>\
<p><b>Query:</b> {query}</p>\
<p><b>Quality:</b> {score}/10 after {iteration} iteration(s)</p>\
<hr/>{md_to_html(final_report)}""")
    await flyte.report.flush.aio()

    log.info(f"Research pipeline complete. Score: {score}/10, Iterations: {iteration}")
    return PipelineResult(
        query=query,
        report=final_report,
        sub_reports=sub_reports,
        score=score,
        iterations=iteration,
    )
