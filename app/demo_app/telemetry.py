"""Send OpenTelemetry traces to Arize Phoenix.

`phoenix.otel.register` builds a tracer provider that exports OTLP to the
Phoenix collector (PHOENIX_COLLECTOR_ENDPOINT) and tags every span with the
Phoenix project name. The OpenInference Anthropic instrumentor then turns each
Claude call into an LLM span with prompts, completions and token counts.
"""

import os

from openinference.instrumentation.anthropic import AnthropicInstrumentor
from phoenix.otel import register

PROJECT_NAME = os.getenv("PHOENIX_PROJECT_NAME", "phoenix-demo")

tracer_provider = register(
    project_name=PROJECT_NAME,
    # Defaults to http://localhost:6006/v1/traces when PHOENIX_COLLECTOR_ENDPOINT is unset.
    endpoint=os.getenv("PHOENIX_COLLECTOR_ENDPOINT") and (
        os.environ["PHOENIX_COLLECTOR_ENDPOINT"].rstrip("/") + "/v1/traces"
    ),
    batch=True,
    verbose=False,
)
AnthropicInstrumentor().instrument(tracer_provider=tracer_provider)

tracer = tracer_provider.get_tracer("phoenix-demo-app")
