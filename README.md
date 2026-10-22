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
  -d '{"request":"Write a project plan for launching a customer support chatbot at a mid-size retail company, 3 months, team of 5."}'
```

Download the returned document URL. Generated files live under `data/documents/`.

## Local models

Set `PRIMARY_PROVIDER=ollama`, run Ollama, and pull a capable instruction model:

```bash
ollama pull qwen2.5:7b-instruct
ollama pull llama3.1:8b
```

Then set `MODEL_FAST` and `MODEL_STRONG` in `.env`.

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

Errors consistently return:

```json
{"error": {"code": "GUARDRAIL_REJECTED", "message": "...", "request_id": "..."}}
```
