"""Phoenix demo app: a small "Acme Outfitters" support assistant traced into Arize Phoenix.

Every request becomes one trace:
  /chat   CHAIN -> LLM                       multi-turn chat, grouped into Phoenix Sessions
  /rag    CHAIN -> RETRIEVER -> LLM          help-center Q&A with retrieved documents
  /agent  AGENT -> LLM -> TOOL(s) -> LLM ... tool-calling loop (weather, calculator, orders)
  /error  CHAIN -> LLM (fails)               a call to a model that does not exist
  /feedback                                  thumbs up/down written to Phoenix as a span annotation
"""

from __future__ import annotations

import ast
import json
import logging
import operator
import os
import random
import uuid
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from openinference.instrumentation import Document, get_retriever_attributes, using_session, using_user
from opentelemetry.trace import Status, StatusCode
from pydantic import BaseModel, Field

from . import knowledge
from .llm import EFFORT, MODE, MODEL, LLMError, complete
from .telemetry import PROJECT_NAME, tracer

log = logging.getLogger("phoenix-demo")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

# tracing=False: FastAPI's built-in HTTP spans would become each trace's root and hide the
# chat / rag / agent spans that Phoenix shows as the trace input and output.
app = FastAPI(title="Phoenix Demo App", version="1.0.0", telemetry={"tracing": False})

SYSTEM_PROMPT = (
    "You are the support assistant for Acme Outfitters, an outdoor gear shop. "
    "Answer in two or three short sentences."
)
MAX_HISTORY_TURNS = 10
_histories: dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY_TURNS * 2))


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    user_id: str | None = None


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    user_id: str | None = None


class FeedbackRequest(BaseModel):
    span_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    label: str = Field(pattern=r"^(positive|negative)$")
    explanation: str | None = Field(default=None, max_length=2000)


def _span_id(span) -> str:
    return format(span.get_span_context().span_id, "016x")


def _trace_id(span) -> str:
    return format(span.get_span_context().trace_id, "032x")


def _fail(span, err: Exception) -> HTTPException:
    span.record_exception(err)
    span.set_status(Status(StatusCode.ERROR, str(err)))
    log.error("request failed: %s", err)
    return HTTPException(status_code=502, detail={"error": str(err), "trace_id": _trace_id(span)})


# --------------------------------------------------------------------------- pages

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (Path(__file__).parent / "static" / "index.html").read_text()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mode": MODE, "model": MODEL, "effort": EFFORT, "project": PROJECT_NAME}


# --------------------------------------------------------------------------- chat

@app.post("/chat")
def chat(req: ChatRequest) -> dict:
    session_id = req.session_id or str(uuid.uuid4())
    history = _histories[session_id]
    with using_session(session_id), using_user(req.user_id or "anonymous"):
        with tracer.start_as_current_span("chat", openinference_span_kind="chain") as span:
            span.set_input(req.message)
            messages = list(history) + [{"role": "user", "content": req.message}]
            try:
                result = complete(SYSTEM_PROMPT, messages)
            except LLMError as e:
                raise _fail(span, e)
            span.set_output(result.text)
            history.append({"role": "user", "content": req.message})
            history.append({"role": "assistant", "content": result.content})
            return {"answer": result.text, "session_id": session_id,
                    "span_id": _span_id(span), "trace_id": _trace_id(span)}


# --------------------------------------------------------------------------- RAG

RAG_PROMPT = (
    "You answer customer questions for Acme Outfitters using only the help-center documents in "
    "<context>. If the documents don't contain the answer, say you don't know. Keep it brief."
)


@app.post("/rag")
def rag(req: AskRequest) -> dict:
    with using_session(req.session_id or str(uuid.uuid4())), using_user(req.user_id or "anonymous"):
        with tracer.start_as_current_span("rag", openinference_span_kind="chain") as span:
            span.set_input(req.question)

            with tracer.start_as_current_span("retrieve", openinference_span_kind="retriever") as r:
                r.set_input(req.question)
                hits = knowledge.search(req.question)
                r.set_attributes(get_retriever_attributes(documents=[
                    Document(id=d.id, content=d.text, score=s, metadata={"title": d.title})
                    for d, s in hits
                ]))

            context = "\n".join(f'<doc id="{d.id}" title="{d.title}">\n{d.text}\n</doc>' for d, _ in hits)
            prompt = f"<context>\n{context}\n</context>\n\nQuestion: {req.question}"
            try:
                result = complete(RAG_PROMPT, [{"role": "user", "content": prompt}])
            except LLMError as e:
                raise _fail(span, e)
            span.set_output(result.text)
            return {"answer": result.text, "sources": [d.id for d, _ in hits],
                    "span_id": _span_id(span), "trace_id": _trace_id(span)}


# --------------------------------------------------------------------------- agent

TOOLS = [
    {
        "name": "get_weather",
        "description": "Get the current weather for a city. Use it for trip-planning or gear questions "
                       "that depend on conditions.",
        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}},
                         "required": ["city"], "additionalProperties": False},
    },
    {
        "name": "calculator",
        "description": "Evaluate an arithmetic expression with + - * / and parentheses.",
        "input_schema": {"type": "object", "properties": {"expression": {"type": "string"}},
                         "required": ["expression"], "additionalProperties": False},
    },
    {
        "name": "lookup_order",
        "description": "Look up an Acme Outfitters order by its ID (for example A1234) and return its status.",
        "input_schema": {"type": "object", "properties": {"order_id": {"type": "string"}},
                         "required": ["order_id"], "additionalProperties": False},
    },
]
AGENT_PROMPT = (
    "You are the Acme Outfitters support agent. Use the tools when they help answer the question, "
    "then give a short final answer."
)
MAX_AGENT_STEPS = 6

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.USub: operator.neg, ast.UAdd: operator.pos}


def _safe_eval(expr: str) -> float:
    def ev(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError(f"unsupported expression: {expr}")
    return ev(ast.parse(expr, mode="eval").body)


def _run_tool(name: str, args: dict) -> str:
    if name == "get_weather":
        city = str(args.get("city", "")).strip() or "Denver"
        rng = random.Random(city.lower())  # stable per city
        return json.dumps({"city": city, "temp_c": rng.randint(-5, 30),
                           "conditions": rng.choice(["sunny", "cloudy", "rain", "snow", "windy"])})
    if name == "calculator":
        return str(_safe_eval(str(args.get("expression", ""))))
    if name == "lookup_order":
        order_id = str(args.get("order_id", "")).upper()
        if order_id.endswith("0"):
            raise KeyError(f"order {order_id} not found")
        rng = random.Random(order_id)
        return json.dumps({"order_id": order_id, "status": rng.choice(["processing", "shipped", "delivered"]),
                           "items": rng.randint(1, 4)})
    raise KeyError(f"unknown tool {name}")


@app.post("/agent")
def agent(req: AskRequest) -> dict:
    with using_session(req.session_id or str(uuid.uuid4())), using_user(req.user_id or "anonymous"):
        with tracer.start_as_current_span("support-agent", openinference_span_kind="agent") as span:
            span.set_input(req.question)
            messages: list[dict] = [{"role": "user", "content": req.question}]
            tools_used: list[str] = []
            try:
                for _ in range(MAX_AGENT_STEPS):
                    result = complete(AGENT_PROMPT, messages, tools=TOOLS)
                    messages.append({"role": "assistant", "content": result.content})
                    if not result.tool_uses:
                        break
                    tool_results = []
                    for tu in result.tool_uses:
                        tools_used.append(tu.name)
                        with tracer.start_as_current_span(tu.name, openinference_span_kind="tool") as t:
                            schema = next((x for x in TOOLS if x["name"] == tu.name), {})
                            t.set_tool(name=tu.name, description=schema.get("description"),
                                       parameters=schema.get("input_schema", {}))
                            t.set_input(tu.input)
                            try:
                                output = _run_tool(tu.name, tu.input)
                                t.set_output(output)
                                tool_results.append({"type": "tool_result", "tool_use_id": tu.id,
                                                     "content": output})
                            except Exception as e:  # report the failure back to the model
                                t.record_exception(e)
                                t.set_status(Status(StatusCode.ERROR, str(e)))
                                tool_results.append({"type": "tool_result", "tool_use_id": tu.id,
                                                     "content": f"Error: {e}", "is_error": True})
                    messages.append({"role": "user", "content": tool_results})
                else:
                    result.text = result.text or "Sorry, I couldn't finish that within the step limit."
            except LLMError as e:
                raise _fail(span, e)
            span.set_output(result.text)
            return {"answer": result.text, "tools_used": tools_used,
                    "span_id": _span_id(span), "trace_id": _trace_id(span)}


# --------------------------------------------------------------------------- error

@app.get("/error")
def error() -> dict:
    """Call a model that does not exist, so Phoenix shows a failed LLM span."""
    with tracer.start_as_current_span("broken-chat", openinference_span_kind="chain") as span:
        span.set_input("Hello?")
        try:
            complete(SYSTEM_PROMPT, [{"role": "user", "content": "Hello?"}], model="claude-does-not-exist")
        except LLMError as e:
            raise _fail(span, e)
        return {"answer": "unexpectedly succeeded"}


# --------------------------------------------------------------------------- feedback

@app.post("/feedback")
def feedback(req: FeedbackRequest) -> dict:
    """Attach a user's thumbs up/down to the trace's root span as a Phoenix annotation."""
    from phoenix.client import Client

    base_url = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")
    try:
        Client(base_url=base_url, api_key=os.getenv("PHOENIX_API_KEY")).spans.add_span_annotation(
            span_id=req.span_id,
            annotation_name="user_feedback",
            annotator_kind="HUMAN",
            label=req.label,
            score=1.0 if req.label == "positive" else 0.0,
            explanation=req.explanation,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"could not write annotation to Phoenix: {e}")
    return {"ok": True}
