# DocPilot

DocPilot is an autonomous business-document agent. It accepts a natural-language
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
cp .env.example .env        # add GROQ_API_KEY or point at Ollama
uvicorn app.main:app --reload
```

Then open <http://localhost:8000/docs> or send:

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

Run either request from the command line:

```bash
python scripts/demo.py --example examples/standard_request.json
python scripts/demo.py --example examples/complex_request.json
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
- Ollama: IDs returned by `ollama list`

Free tiers are rate-limited. For longer complex runs, raise `RUN_TIMEOUT_S` and
lower `MAX_CONCURRENCY` to stay within provider limits; use `MAX_CONCURRENCY=1` for
Groq's 8,000-TPM free tier. Keep `LLM_MAX_OUTPUT_TOKENS` low enough that the prompt
plus reserved output tokens stays under the provider limit, and set
`LLM_REASONING_EFFORT=low` for Groq gpt-oss reasoning models. Reflection is best-effort
and bounded by `REFLECTION_TIMEOUT_S`; if it expires, the current validated draft is
rendered with the unresolved review items in `warnings`.

## Development

```bash
make test
make lint
make typecheck
python scripts/render_demo.py
```

All tests use `FakeLLM` and never touch the network.

## API

- `GET /health` — liveness
- `GET /ready` — configuration readiness
- `POST /agent` — run the full agent; returns plan, assumptions, conflicts, reflection,
  warnings, trace, and a document link
- `GET /documents/{run_id}` — download the generated `.docx`

Set `API_KEYS=key1,key2` to require an `X-API-Key` header on `/agent` and
`/documents/{run_id}`. With `API_KEYS` empty, authentication is disabled for local use.

Runs are synchronous and bounded by `RUN_TIMEOUT_S`. Generated files contain no
LLM-authored binary content: every block is validated Pydantic data and rendered by
the deterministic `python-docx` renderer.

Errors consistently return:

```json
{"error": {"code": "GUARDRAIL_REJECTED", "message": "...", "request_id": "..."}}
```
