#!/usr/bin/env python3
"""Keep a steady trickle of traffic flowing through the demo app so Phoenix always has fresh traces.

    python3 loadgen.py                       # http://localhost:8000, 6 requests/min
    APP_URL=http://host:8000 RPM=20 python3 loadgen.py
    python3 loadgen.py --once                # one request of each kind, then exit

With LLM_MODE=claude every request is a real, billed Claude call: keep RPM low.
Uses only the standard library.
"""
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
import uuid

APP_URL = os.getenv("APP_URL", "http://localhost:8000").rstrip("/")
RPM = float(os.getenv("RPM", "6"))
ERROR_PCT = float(os.getenv("ERROR_PCT", "3"))
FEEDBACK_PCT = float(os.getenv("FEEDBACK_PCT", "30"))

CHAT = [
    ["Which sleeping bag is good for spring camping?", "Is down or synthetic better if it rains?"],
    ["I'm planning a 3-day hike in Colorado.", "What should I pack?", "How heavy should my pack be?"],
    ["Do you sell kids' hiking boots?"],
]
RAG = ["How long do I have to return an item?", "How much is express shipping?", "Do you ship to the UK?",
       "Is my backpack under warranty if a zipper broke?", "How do I clean my tent?",
       "When is the Denver store open on Sunday?", "How do loyalty points work?",
       "Can I pay with cryptocurrency?"]
AGENT = ["What's the weather in Boulder and what jacket should I bring?", "Where is order A1234?",
         "Where is order A5550?", "What is 129.99 * 3 - 15?", "Check order A7781 and the weather in Seattle."]


def call(method, path, body=None):
    req = urllib.request.Request(APP_URL + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:  # app restarting, network blip
        return 0, {"error": str(e)}


def maybe_feedback(data):
    if data.get("span_id") and random.uniform(0, 100) < FEEDBACK_PCT:
        call("POST", "/feedback", {"span_id": data["span_id"],
                                   "label": random.choices(["positive", "negative"], [4, 1])[0]})


def one(kind):
    user = f"user-{random.randint(1, 25)}"
    if kind == "chat":
        session = str(uuid.uuid4())
        for msg in random.choice(CHAT):
            status, data = call("POST", "/chat", {"message": msg, "session_id": session, "user_id": user})
            print(f"chat  {status}", flush=True)
            maybe_feedback(data)
    elif kind == "rag":
        status, data = call("POST", "/rag", {"question": random.choice(RAG), "user_id": user})
        print(f"rag   {status}", flush=True)
        maybe_feedback(data)
    elif kind == "agent":
        status, data = call("POST", "/agent", {"question": random.choice(AGENT), "user_id": user})
        print(f"agent {status}", flush=True)
        maybe_feedback(data)
    else:
        status, _ = call("GET", "/error")
        print(f"error {status}", flush=True)


def main():
    if "--once" in sys.argv:
        for kind in ("chat", "rag", "agent", "error"):
            one(kind)
        return
    print(f"loadgen -> {APP_URL}: {RPM} req/min, {ERROR_PCT}% errors", flush=True)
    while True:
        kind = "error" if random.uniform(0, 100) < ERROR_PCT else random.choice(["chat", "rag", "rag", "agent"])
        one(kind)
        time.sleep(60.0 / max(RPM, 0.1))


if __name__ == "__main__":
    main()
