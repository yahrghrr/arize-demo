# Project notes

Background on how this demo was built and where it stands. Usage instructions are in the main [README](../README.md).

## Origin

The project was first built in a `phoenix/` folder of another repository, then copied here as the repository root.
The earlier copy is still on that repository's `claude/awesome-fermi-aiku6n` branch and can be removed there.

## What was tested

| Area                                    | Status                                                                                             |
|-----------------------------------------|----------------------------------------------------------------------------------------------------|
| Phoenix 22 server (local `phoenix serve`) | Ran; traces received and viewed in the UI                                                        |
| Demo app, mock mode                     | Every endpoint exercised; chat, RAG, agent, error and feedback traces appeared as expected        |
| Demo app, real Claude                   | Partly: a dummy key reached the API and got a 401, which Phoenix recorded. No successful answer seen yet |
| Docker Compose                          | Not run (no Docker daemon available). The pinned image tag `arizephoenix/phoenix:version-22.0.0` was confirmed to exist |
| `setup.sh` (systemd, one VM)            | Deployed on an Azure Ubuntu VM: Phoenix, app and chat page working, traces visible after the fixes below |
| `uninstall.sh`                          | Syntax-checked only                                                                                |

## Design decisions

- **Duplicate tracing turned off.** The Anthropic SDK instrumentor and FastAPI's built-in telemetry both emit spans.
  Left on, each Claude call showed twice and every trace started with a generic HTTP span. The app uses
  `FastAPI(telemetry={"tracing": False})` so each trace's root is the `chat`, `rag` or `support-agent` span.
- **Settings prefixed `DEMO_CLAUDE_*`.** `DEMO_CLAUDE_MODEL`, `DEMO_CLAUDE_EFFORT` and `DEMO_CLAUDE_MAX_TOKENS`,
  because Claude Code sets its own `CLAUDE_EFFORT`, which the app was picking up.
- **Model and effort.** Real mode calls `claude-opus-5-5` at effort `low`.
- **Refusal fallbacks on.** Requests send `fallbacks: "default"`, so a declined request is retried on a fallback model
  within the same call. This is a beta feature; turn it off in `app/demo_app/llm.py` if unwanted.
- **Deliberate failures.** `/agent` order IDs ending in `0` make the tool span fail; `/error` calls a model that does
  not exist.
- **No authentication by default.** Phoenix ships without login; the README explains how to enable it before exposing
  the server beyond a private network.

## Issues found on the first VM deploy

- **Chat page stuck on "Loading…".** `crypto.randomUUID()` only exists on HTTPS or localhost, so over
  `http://<vm-ip>` the page script failed before attaching any handlers. Fixed with a `crypto.getRandomValues()`
  fallback; reproduced and verified in a browser over plain HTTP on a non-localhost address.
- **No traces in Phoenix (401).** After login was turned on, the app had no `PHOENIX_API_KEY`, so every export was
  rejected and only logged. The README now covers this, and `setup.sh` warns when login is on without a key.

## Open items

- Run once with a real `ANTHROPIC_API_KEY` and confirm a successful Claude response is traced.
- Run `docker compose up -d --build` on a host with Docker.
- Try the two-VM layout (README, B2) end to end.
- Keep the load generator's `RPM` low in Claude mode: every request is billed, and agent requests make two or more calls.
