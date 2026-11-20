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
├── docs/PROJECT_NOTES.md        # build history, test status, decisions
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

Requires Ubuntu 22.04+/Debian 12+ or RHEL/Rocky/Alma 9+/Amazon Linux 2023. `setup.sh` installs Python 3.11+ if
needed, creates a `phoenix` service user, and installs services that start on boot:

| Service                 | What it is                         | Settings file                        |
|-------------------------|------------------------------------|--------------------------------------|
| `phoenix`               | Phoenix UI + collector (:6006)     | `/etc/default/phoenix`               |
| `phoenix-demo-app`      | Demo app (:8000)                   | `/etc/default/phoenix-demo-app`      |
| `phoenix-demo-loadgen`  | Steady background traffic          | `/etc/default/phoenix-demo-loadgen`  |

Script options: `APP=0` installs Phoenix only, `LOADGEN=0` skips the load generator, `ANTHROPIC_API_KEY=...` saves a
Claude key. Re-running the script upgrades and redeploys, and keeps trace data and existing `/etc/default/*` files.

Pick **B1** (everything on one VM, simplest) or **B2** (Phoenix and the app on separate VMs).

### B1. Everything on one VM

On the VM, get the code. If the repository is private, use a GitHub personal access token as the password.

```bash
git clone https://github.com/yahrghrr/arize-demo.git ~/arize-demo
```

Install Phoenix, the app and the load generator. Without a key the app runs in mock mode:

```bash
cd ~/arize-demo && sudo bash setup.sh
```

Or with real Claude calls:

```bash
cd ~/arize-demo && sudo ANTHROPIC_API_KEY=sk-ant-... bash setup.sh
```

Check that all three services are running (expect `active` three times, then the app's mode):

```bash
systemctl is-active phoenix phoenix-demo-app phoenix-demo-loadgen && curl -s http://localhost:8000/health
```

Open inbound TCP **6006** and **8000** in the VM's firewall or cloud security group (on Azure: VM → Networking → Add
inbound port rule), with your own IP as the source. Phoenix has no login by default, so don't allow "Any". Then open:

- Demo app: `http://<vm-ip>:8000`
- Phoenix: `http://<vm-ip>:6006`, project **phoenix-demo**

Without opening ports, you can tunnel instead and use `http://localhost:6006`:

```bash
ssh -N -L 6006:localhost:6006 -L 8000:localhost:8000 <user>@<vm-ip>
```

### B2. Phoenix and the app on separate VMs

Set up the Phoenix VM first. On the **Phoenix VM**, install Phoenix only:

```bash
git clone https://github.com/yahrghrr/arize-demo.git ~/arize-demo
```

```bash
cd ~/arize-demo && sudo APP=0 bash setup.sh
```

Note its private IP. If both VMs are in the same virtual network, the app should use this address, so port 6006
doesn't need to be open to the internet:

```bash
hostname -I | awk '{print $1}'
```

On the **app VM**, install everything (add `ANTHROPIC_API_KEY=...` after `sudo` for real Claude calls):

```bash
git clone https://github.com/yahrghrr/arize-demo.git ~/arize-demo
```

```bash
cd ~/arize-demo && sudo bash setup.sh
```

Point the app at the Phoenix VM (replace `PHOENIX_IP`):

```bash
sudo sed -i 's|^PHOENIX_COLLECTOR_ENDPOINT=.*|PHOENIX_COLLECTOR_ENDPOINT=http://PHOENIX_IP:6006|' /etc/default/phoenix-demo-app
```

`setup.sh` always installs a local Phoenix as well. Turn it off on the app VM (`mask` keeps it off after reboots),
then restart the app:

```bash
sudo systemctl disable --now phoenix && sudo systemctl mask phoenix
```

```bash
sudo systemctl restart phoenix-demo-app phoenix-demo-loadgen
```

Check that the app VM can reach Phoenix (expect `HTTP 200`):

```bash
curl -s -o /dev/null -w "HTTP %{http_code}\n" http://PHOENIX_IP:6006/healthz
```

Firewall rules:

| VM      | Inbound port              | Source                                               |
|---------|---------------------------|------------------------------------------------------|
| Phoenix | 6006 (4317 for gRPC)      | The app VM, plus your own IP to view the UI          |
| App     | 8000                      | Your own IP                                          |

### Adding a Claude API key later

Edit the app's settings file and replace the line `# ANTHROPIC_API_KEY=sk-ant-...` with
`ANTHROPIC_API_KEY=<your key>` (no leading `#`):

```bash
sudo nano /etc/default/phoenix-demo-app
```

```bash
sudo systemctl restart phoenix-demo-app
```

With `LLM_MODE=auto`, `curl -s localhost:8000/health` now reports `"mode":"claude"`. Re-running
`sudo ANTHROPIC_API_KEY=... bash setup.sh` does the same.

Every load generator request is now a billed API call, and agent requests make two or more. Lower `RPM` in
`/etc/default/phoenix-demo-loadgen` and restart it, or stop it:

```bash
sudo systemctl disable --now phoenix-demo-loadgen
```

### Turning on login

Phoenix runs without login by default. To require one, generate a secret of 32+ characters:

```bash
openssl rand -hex 32
```

Then, in `/etc/default/phoenix`, uncomment `PHOENIX_ENABLE_AUTH=True` and set `PHOENIX_SECRET` to that value:

```bash
sudo nano /etc/default/phoenix
```

```bash
sudo systemctl restart phoenix
```

Log in with the default admin, **`admin@localhost`** / **`admin`**. Phoenix asks for a new password on first login.

**With login on, Phoenix rejects traces that carry no API key** (the app logs `401 Unauthorized`). In Phoenix, go to
**Settings → API Keys**, create a **System** key, and set `PHOENIX_API_KEY=<key>` in `/etc/default/phoenix-demo-app`
on the VM running the app:

```bash
sudo nano /etc/default/phoenix-demo-app
```

```bash
sudo systemctl restart phoenix-demo-app
```

Only traces sent after the key is in place appear. Earlier ones were rejected.

### Logs and settings

After editing a settings file, restart that service. Follow the logs with:

```bash
journalctl -u phoenix -u phoenix-demo-app -u phoenix-demo-loadgen -f
```

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Chat page stuck on "Loading…" and Send only reloads the page | Fixed in this version (the page used a browser API that only works over HTTPS). Pull, then re-run `sudo bash setup.sh`, or copy `app/demo_app/static/index.html` to `/opt/phoenix/app/demo_app/static/`. Then hard-refresh. |
| Chats get answers but nothing appears in Phoenix | Look for export errors: `sudo journalctl -u phoenix-demo-app -n 80 --no-pager \| grep -iE 'export\|error'` |
| …log shows `401 Unauthorized` | Phoenix login is on and the app has no key. See [Turning on login](#turning-on-login). |
| …log shows connection refused or timeouts | Check `PHOENIX_COLLECTOR_ENDPOINT` in `/etc/default/phoenix-demo-app`. On one VM it must be `http://localhost:6006`. A VM's own public IP usually goes through the cloud firewall and gets blocked. With two VMs, use the Phoenix VM's private IP and allow the app VM on port 6006. |
| "Claude API error 529: Overloaded (mock)" in the chat | On purpose: mock mode fails about 2% of calls so Phoenix has errors to show. Set `MOCK_FAILURE_RATE=0` to turn it off. |
| No traffic unless you chat | `systemctl is-active phoenix-demo-loadgen`. It isn't installed with `LOADGEN=0` or `APP=0`. |

### Uninstall

`PURGE=1` also deletes trace data and config:

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
