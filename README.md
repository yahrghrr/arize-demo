# Arize Phoenix Demo

> Intended for demos and testing, not for production use.

A self-hosted **[Arize Phoenix](https://arize.com/docs/phoenix)** server (LLM tracing and evaluation) plus a small
**Claude-powered support assistant** that sends OpenTelemetry traces to it. You get chat, RAG, an agent with tool calls,
deliberate failures and user feedback to look at in Phoenix.

| Component             | Port  | What it is                                                                 |
|-----------------------|-------|----------------------------------------------------------------------------|
| Phoenix               | 6006  | UI, REST API and OTLP/HTTP collector (`/v1/traces`)                        |
| Phoenix               | 4317  | OTLP/gRPC collector                                                        |
| Demo app              | 8000  | "Acme Outfitters" support assistant (FastAPI) with a small chat page       |
| Load generator        | –     | Optional: keeps a steady stream of requests flowing                        |

### What shows up in Phoenix

| Endpoint          | Trace shape                                 | What to show in Phoenix                                                      |
|-------------------|---------------------------------------------|------------------------------------------------------------------------------|
| `POST /chat`      | `chat` CHAIN → LLM                          | Multi-turn history; turns grouped in the **Sessions** tab by `session_id`    |
| `POST /rag`       | `rag` CHAIN → RETRIEVER → LLM               | Retrieved documents with scores; "I don't know" when nothing matches          |
| `POST /agent`     | `support-agent` AGENT → LLM → TOOL… → LLM   | Tool calls and arguments; order IDs ending in `0` make a tool span fail       |
| `GET /error`      | `broken-chat` CHAIN → LLM (error)           | A failed LLM call (model that does not exist) with the exception recorded     |
| `POST /feedback`  | –                                           | 👍/👎 written to the root span as a `user_feedback` annotation                 |

LLM spans carry the prompt messages, response, model name, invocation parameters and token counts. Every span also has
`session.id` and `user.id`.

### Mock mode vs. real Claude

| `LLM_MODE`        | Behaviour                                                                                   |
|-------------------|---------------------------------------------------------------------------------------------|
| `auto` (default)  | Real Claude calls when `ANTHROPIC_API_KEY` is set, otherwise mock                           |
| `claude`          | Calls Claude (`claude-opus-5-5`, effort `low`) through the Anthropic SDK                    |
| `mock`            | No network or key needed. Canned answers recorded as the same kind of OpenInference spans  |

In Claude mode, requests are sent with server-side refusal fallbacks turned on (`fallbacks: "default"`): if a safety
classifier declines, the API retries on a fallback model within the same call. Each request is a billed API call, and
agent requests make two or more, so keep the load generator's `RPM` low.

```
.
├── docker-compose.yml           # Phoenix + app (+ loadgen profile) in containers
├── setup.sh / uninstall.sh      # VM install as systemd services
├── server/requirements.txt      # pinned arize-phoenix for the VM install
├── deploy/                      # systemd units and /etc/default env files
└── app/
    ├── Dockerfile, requirements.txt
    ├── loadgen.py               # traffic generator (stdlib only)
    └── demo_app/
        ├── telemetry.py         # phoenix.otel.register + Anthropic instrumentor
        ├── llm.py               # Claude client, or mock that emits LLM spans
        ├── knowledge.py         # help-center docs + keyword retriever
        ├── main.py              # FastAPI endpoints and trace structure
        └── static/index.html    # chat page
```

---

## Option A: Docker Compose (quickest)

Run these from the repository root.

```bash
docker compose up -d --build
```

Open the demo app at <http://localhost:8000> and Phoenix at <http://localhost:6006> (project **phoenix-demo**).

To use real Claude calls, export your key before starting (or put it in a `.env` file next to `docker-compose.yml`):

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

```bash
docker compose up -d --build
```

To add continuous background traffic:

```bash
docker compose --profile loadgen up -d
```

Trace data is kept in the `phoenix-data` volume. `docker compose down -v` removes it.

## Option B: Install on a VM (systemd)

Requires Ubuntu 22.04+/Debian 12+ or RHEL/Rocky/Alma 9+/Amazon Linux 2023. The script installs Python 3.11+ if
needed, creates a `phoenix` service user, and installs three services that start on boot.

```bash
sudo bash setup.sh
```

With real Claude calls (the key is saved to `/etc/default/phoenix-demo-app`, readable only by root and the service user):

```bash
sudo ANTHROPIC_API_KEY=sk-ant-... bash setup.sh
```

Other options: `APP=0` installs Phoenix only, `LOADGEN=0` skips the load generator. Re-running the script upgrades and
redeploys while keeping the existing `/etc/default/*` files.

| Service                 | Settings file                        |
|-------------------------|--------------------------------------|
| `phoenix`               | `/etc/default/phoenix`               |
| `phoenix-demo-app`      | `/etc/default/phoenix-demo-app`      |
| `phoenix-demo-loadgen`  | `/etc/default/phoenix-demo-loadgen`  |

After editing a settings file, restart that service, for example:

```bash
sudo systemctl restart phoenix-demo-app
```

Follow the logs:

```bash
journalctl -u phoenix -u phoenix-demo-app -u phoenix-demo-loadgen -f
```

Open ports 6006 and 8000 in the VM's firewall or security group to reach the UIs from your browser. Phoenix runs
without login by default. Before exposing it beyond a private network, set `PHOENIX_ENABLE_AUTH=True` and
`PHOENIX_SECRET` in `/etc/default/phoenix`, create an API key in Phoenix (Settings → API Keys) and put it in
`PHOENIX_API_KEY` in `/etc/default/phoenix-demo-app`.

To remove everything (`PURGE=1` also deletes trace data and config):

```bash
sudo bash uninstall.sh
```

## Option C: Run locally without containers

Needs Python 3.11–3.13. In one terminal:

```bash
python3 -m venv .venv-phoenix && .venv-phoenix/bin/pip install -r server/requirements.txt
```

```bash
.venv-phoenix/bin/phoenix serve
```

In a second terminal:

```bash
python3 -m venv .venv-app && .venv-app/bin/pip install -r app/requirements.txt
```

```bash
cd app && ../.venv-app/bin/uvicorn demo_app.main:app --port 8000
```

---

## Running a demo

1. Open the demo app at `http://<host>:8000` and try each tab. The example buttons cover the interesting cases.
2. In Phoenix, open **Projects → phoenix-demo**:
   - **Traces / Spans**: click a `support-agent` trace to walk through LLM → tool → LLM, with the arguments of each tool
     call. Try "Where is order A5550?" to see a failed tool span the agent recovers from.
   - **Sessions**: the chat tab's turns appear as one conversation.
   - A `rag` trace for "Can I pay with cryptocurrency?" retrieves no documents, so the answer should be "I don't know".
     That makes it a good starting point for a hallucination evaluation.
   - `broken-chat` traces show a failed LLM span with the exception.
   - Click 👍/👎 in the app and the `user_feedback` annotation appears on that trace (filter with
     `annotations['user_feedback'].label == 'negative'`).
3. Optional next steps in Phoenix: add traces to a dataset, open an LLM span in the **Playground** to replay the prompt,
   or run an LLM-as-judge evaluator over the `rag` spans.

Generate traffic by hand:

```bash
python3 app/loadgen.py --once
```

```bash
curl -s -X POST localhost:8000/agent -H 'content-type: application/json' -d '{"question":"Where is order A1234?"}'
```

## Configuration reference

Demo app (`/etc/default/phoenix-demo-app` or container environment):

| Variable                     | Default                  | Meaning                                                   |
|------------------------------|--------------------------|-----------------------------------------------------------|
| `PHOENIX_COLLECTOR_ENDPOINT` | `http://localhost:6006`  | Phoenix base URL (traces go to `/v1/traces`)              |
| `PHOENIX_PROJECT_NAME`       | `phoenix-demo`           | Project the traces land in                                |
| `PHOENIX_API_KEY`            | –                        | Needed only when Phoenix auth is on                       |
| `LLM_MODE`                   | `auto`                   | `auto`, `claude` or `mock`                                |
| `ANTHROPIC_API_KEY`          | –                        | Claude API key                                            |
| `DEMO_CLAUDE_MODEL`          | `claude-opus-5-5`        | Model for Claude mode                                     |
| `DEMO_CLAUDE_EFFORT`         | `low`                    | `low`, `medium`, `high`, `xhigh` or `max`                 |
| `MOCK_FAILURE_RATE`          | `0.02`                   | Mock mode: share of LLM calls that fail as "overloaded"   |

Load generator: `APP_URL`, `RPM` (requests per minute, default 6), `ERROR_PCT` (default 3), `FEEDBACK_PCT` (default 30).

Phoenix server settings (`PHOENIX_WORKING_DIR`, `PHOENIX_SQL_DATABASE_URL` for Postgres, ports, auth) are covered in the
self-hosting section of the [Phoenix docs](https://arize.com/docs/phoenix).

### Notes on the instrumentation

- `phoenix.otel.register()` sets up the OTLP exporter and project name. `AnthropicInstrumentor` records Claude calls as
  OpenInference LLM spans.
- The Anthropic SDK's own OpenTelemetry spans are turned off (`Anthropic(open_telemetry=False)`), and so is FastAPI's
  built-in HTTP tracing (`FastAPI(telemetry={"tracing": False})`). Otherwise each Claude call would appear twice and
  every trace would start with a generic HTTP span instead of the chat/rag/agent span.
- To send the same traces to another OpenTelemetry backend as well (Dynatrace, for example), add a second span processor
  with an OTLP exporter to the tracer provider in `telemetry.py`.
