# Productivity

Productivity is an autonomous business-document agent. It accepts a natural-language
request, analyzes it, writes its own validated task plan, runs deterministic tools,
writes sections through an LLM, self-reviews the draft, and renders a styled Word
document.

The LLM never writes the `.docx`. It emits validated Pydantic models; a deterministic
renderer produces the file.

## Quick start

```bash
python -m venv .venv
. .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env        # add GROQ_API_KEY / OPENROUTER_API_KEY or point at Ollama
uvicorn app.main:app --reload
```

Then open <http://localhost:8000/> for the live demo, or <http://localhost:8000/docs>
for the API reference. You can also send:

```bash
curl -X POST http://localhost:8000/agent \
  -H "Content-Type: application/json" \
  --data @examples/standard_request.json
```

The response includes the run status, plan, assumptions, conflicts, reflection
results, warnings, trace events, and `document_url`. Download the returned URL; the
same file is stored under `data/documents/{run_id}.docx`.

Ready-made requests:

- `examples/standard_request.json` — project plan with budget and timeline tools
- `examples/complex_request.json` — board decision memo with conflicting constraints
- `examples/smoke_request.json` — quick rough-notes-to-meeting-minutes run for smoke testing

Run either request from the command line:

```bash
python scripts/demo.py --example examples/standard_request.json
python scripts/demo.py --example examples/complex_request.json
python scripts/demo.py --example examples/smoke_request.json
```

## Local models

Set `PRIMARY_PROVIDER=ollama`, run Ollama, and pull a capable instruction model:

```bash
ollama pull qwen2.5:7b-instruct
ollama pull llama3.1:8b
```

Then set `MODEL_FAST` and `MODEL_STRONG` in `.env`.

## Free-tier providers

Model IDs are provider-specific; change `MODEL_FAST` and `MODEL_STRONG` whenever
you switch providers.

- Groq: `openai/gpt-oss-20b` and `openai/gpt-oss-120b`
- Gemini: `gemini-3.5-flash-lite` (or `gemini-3.5-flash` when available)
- OpenRouter: `dots-studio/dots-3-note-preview:free` (free-model availability varies)
- Ollama: IDs returned by `ollama list`

Free tiers are rate-limited. For longer complex runs, raise `RUN_TIMEOUT_S` and
lower `MAX_CONCURRENCY` to stay within provider limits; use `MAX_CONCURRENCY=1` for
Groq's 8,000-TPM free tier. `LLM_MIN_INTERVAL_S` spaces provider calls, which keeps
a run under a per-minute request cap: use about `4.2` for Gemini's 15 RPM free tier. Keep `LLM_MAX_OUTPUT_TOKENS` low enough that the prompt
plus reserved output tokens stays under the provider limit, and set
`LLM_REASONING_EFFORT=low` for Groq gpt-oss reasoning models; use `none` for
OpenRouter free reasoning models that otherwise consume the output budget. Leave it
empty for providers that do not support reasoning-effort parameters.

OpenRouter's free-model pool is capped (50 requests/day on a key with fewer than
10 credits), upstream models may return 429, and output quality varies. Reflection
is best-effort and bounded by `REFLECTION_TIMEOUT_S`; if it expires, the current
validated draft is rendered with the unresolved review items in `warnings`.

## Development

```bash
make test
make lint
make typecheck
python scripts/render_demo.py
```

All tests use `FakeLLM` and never touch the network.

## API

- `GET /` (or `GET /demo`) - interactive browser demo with live SSE progress and
  a document download
- `GET /health` — liveness
- `GET /ready` — configuration readiness
- `POST /agent` — run the full agent synchronously; returns plan, assumptions,
  conflicts, reflection, warnings, trace, and a document link
- `POST /runs` — accept a run for background execution; returns `202` with a
  `run_id` and `events_url`
- `GET /runs/{run_id}/events` — replay and live-stream run trace events as SSE
- `GET /documents/{run_id}` — download the generated `.docx`

Set `API_KEYS=key1,key2` to require an `X-API-Key` header on `/agent`, `/runs`,
`/runs/{run_id}/events`, and `/documents/{run_id}`. With `API_KEYS` empty,
authentication is disabled for local use.

Analysis and plan results are cached in memory by normalized request and planner
configuration. Set `CACHE_ENABLED=false` to bypass the cache; `CACHE_TTL_S` and
`CACHE_MAX_ENTRIES` bound its lifetime and size. Per-stage model routing is
controlled by `ANALYSIS_TIER`, `PLANNING_TIER`, `WRITING_TIER`, `REFLECTION_TIER`,
`COMPOSITION_TIER`, and `CORRECTION_TIER` (`fast` or `strong`).

Runs are bounded by `RUN_TIMEOUT_S`; `/agent` waits for completion while `/runs`
returns immediately and streams progress over SSE. Generated files contain no
LLM-authored binary content: every block is validated Pydantic data and rendered by
the deterministic `python-docx` renderer.

Errors consistently return:

```json
{"error": {"code": "GUARDRAIL_REJECTED", "message": "...", "request_id": "..."}}
```
