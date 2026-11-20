"""LLM backend: real Claude calls, or a mock that emits the same kind of spans.

LLM_MODE=claude  -> call Claude through the Anthropic SDK (needs ANTHROPIC_API_KEY)
LLM_MODE=mock    -> no network: canned answers, recorded as OpenInference LLM spans
LLM_MODE=auto    -> claude when an API key is set, otherwise mock (default)
"""

from __future__ import annotations

import json
import os
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from openinference.instrumentation import (
    Message,
    TokenCount,
    Tool,
    ToolCall,
    ToolCallFunction,
    get_llm_attributes,
)

from .telemetry import tracer

MODEL = os.getenv("DEMO_CLAUDE_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("DEMO_CLAUDE_EFFORT", "low")  # low keeps demo latency and cost down
MAX_TOKENS = int(os.getenv("DEMO_CLAUDE_MAX_TOKENS", "16000"))

_mode = os.getenv("LLM_MODE", "auto").lower()
if _mode == "auto":
    _mode = "claude" if (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")) else "mock"
MODE = _mode


class LLMError(RuntimeError):
    """Raised when the model call fails or Claude declines the request."""


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]


@dataclass
class LLMResult:
    text: str
    tool_uses: list[ToolUse] = field(default_factory=list)
    # Assistant content to append to the conversation unchanged (keeps thinking blocks intact).
    content: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "end_turn"


# --------------------------------------------------------------------------- Claude

_client = None


def _claude():
    global _client
    if _client is None:
        import anthropic

        # open_telemetry=False: the OpenInference instrumentor already records each call
        # as an LLM span; the SDK's own spans would show up twice in Phoenix.
        _client = anthropic.Anthropic(open_telemetry=False)
    return _client


def _claude_complete(system: str, messages: list[dict], tools: list[dict] | None, model: str) -> LLMResult:
    import anthropic

    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=messages,
        output_config={"effort": EFFORT},
        # If a safety classifier declines, the API retries on a fallback model in the same call.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if tools:
        kwargs["tools"] = tools
    try:
        response = _claude().beta.messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f"Could not reach the Claude API: {e}") from e

    if response.stop_reason == "refusal":
        raise LLMError("Claude declined this request.")

    text = "".join(b.text for b in response.content if b.type == "text")
    tool_uses = [ToolUse(b.id, b.name, dict(b.input)) for b in response.content if b.type == "tool_use"]
    content = [b.model_dump(by_alias=True, exclude_none=True) for b in response.content]
    return LLMResult(text=text, tool_uses=tool_uses, content=content, stop_reason=response.stop_reason or "")


# --------------------------------------------------------------------------- Mock

def _mock_reply(system: str, messages: list[dict], tools: list[dict] | None) -> LLMResult:
    last = messages[-1]
    # After tool results come back, summarise them.
    if isinstance(last["content"], list) and any(c.get("type") == "tool_result" for c in last["content"]):
        results = [c["content"] for c in last["content"] if c.get("type") == "tool_result"]
        text = "Here is what I found: " + "; ".join(str(r) for r in results)
        return LLMResult(text=text, content=[{"type": "text", "text": text}])

    question = last["content"] if isinstance(last["content"], str) else str(last["content"])
    q = question.lower()

    if tools:
        names = {t["name"] for t in tools}
        calls: list[ToolUse] = []
        if "get_weather" in names and "weather" in q:
            m = re.search(r"\bin ([a-z]+)", q)
            calls.append(ToolUse(f"toolu_{uuid.uuid4().hex[:12]}", "get_weather",
                                 {"city": (m.group(1).strip().title() if m else "Denver")}))
        if "calculator" in names and re.search(r"\d+\s*[-+*/]\s*\d+", q):
            expr = re.search(r"[\d\s.+\-*/()]+[\d)]", question).group(0).strip()
            calls.append(ToolUse(f"toolu_{uuid.uuid4().hex[:12]}", "calculator", {"expression": expr}))
        if "lookup_order" in names and (m := re.search(r"\b(A-?\d{3,})\b", question, re.I)):
            calls.append(ToolUse(f"toolu_{uuid.uuid4().hex[:12]}", "lookup_order",
                                 {"order_id": m.group(1).upper()}))
        if calls:
            content = [{"type": "text", "text": "Let me check that."}] + [
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input} for c in calls
            ]
            return LLMResult(text="Let me check that.", tool_uses=calls, content=content, stop_reason="tool_use")

    if "<context>" in question:
        ctx = re.search(r"<context>(.*?)</context>", question, re.S).group(1).strip()
        first = re.search(r"<doc[^>]*>\s*(.*?)\s*</doc>", ctx, re.S)
        text = (f"Based on our help center: {first.group(1)}" if first
                else "I couldn't find that in our help center, so I can't answer reliably.")
    else:
        text = random.choice([
            f"Great question! In short: {question.strip().rstrip('?')} depends on your use case, "
            "but the usual recommendation is to start simple and measure.",
            "Here's a concise answer: Phoenix collects OpenTelemetry traces from LLM apps so you can "
            "inspect prompts, responses, latency and token usage.",
            "Short version: yes. Longer version: it depends on the details, so let's look at the traces.",
        ])
    return LLMResult(text=text, content=[{"type": "text", "text": text}])


def _to_oi_messages(system: str, messages: list[dict]) -> list[Message]:
    out: list[Message] = [Message(role="system", content=system)]
    for m in messages:
        if isinstance(m["content"], str):
            out.append(Message(role=m["role"], content=m["content"]))
            continue
        texts = [c.get("text", "") for c in m["content"] if c.get("type") == "text"]
        calls = [ToolCall(id=c["id"], function=ToolCallFunction(name=c["name"], arguments=json.dumps(c["input"])))
                 for c in m["content"] if c.get("type") == "tool_use"]
        results = [str(c.get("content")) for c in m["content"] if c.get("type") == "tool_result"]
        msg = Message(role="tool" if results else m["role"], content="\n".join(texts + results))
        if calls:
            msg["tool_calls"] = calls
        out.append(msg)
    return out


def _mock_complete(system: str, messages: list[dict], tools: list[dict] | None, model: str) -> LLMResult:
    """Fake a Claude call and record it exactly like the real instrumentor would."""
    input_msgs = _to_oi_messages(system, messages)
    with tracer.start_as_current_span("messages.create (mock)", openinference_span_kind="llm") as span:
        span.set_input({"model": model, "system": system, "messages": messages})
        time.sleep(random.uniform(0.3, 1.2))
        if model.startswith("claude-does-not-exist"):
            raise LLMError(f"Claude API error 404: model: {model} not found (mock)")
        if random.random() < float(os.getenv("MOCK_FAILURE_RATE", "0.02")):
            raise LLMError("Claude API error 529: Overloaded (mock)")

        result = _mock_reply(system, messages, tools)
        prompt_tokens = sum(len(m.get("content") or "") for m in input_msgs) // 4 + 20
        completion_tokens = len(result.text) // 4 + 10 * len(result.tool_uses) + 5
        out_msg = Message(role="assistant", content=result.text)
        if result.tool_uses:
            out_msg["tool_calls"] = [ToolCall(id=t.id, function=ToolCallFunction(
                name=t.name, arguments=json.dumps(t.input))) for t in result.tool_uses]
        span.set_attributes(get_llm_attributes(
            provider="anthropic",
            system="anthropic",
            model_name=model,
            invocation_parameters={"max_tokens": MAX_TOKENS, "output_config": {"effort": EFFORT}},
            input_messages=input_msgs,
            output_messages=[out_msg],
            token_count=TokenCount(prompt=prompt_tokens, completion=completion_tokens,
                                   total=prompt_tokens + completion_tokens),
            tools=[Tool(json_schema=t) for t in tools or []],
        ))
        span.set_output(result.text)
        return result


# --------------------------------------------------------------------------- API

def complete(system: str, messages: list[dict], tools: list[dict] | None = None,
             model: str | None = None) -> LLMResult:
    model = model or MODEL
    if MODE == "claude":
        return _claude_complete(system, messages, tools, model)
    return _mock_complete(system, messages, tools, model)
