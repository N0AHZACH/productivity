# Video submission script

Target runtime **9:30**. The four required sections sit inside their limits, and the
hard checkpoints below keep them there.

| # | Segment                   | Window      | Length | Limit   | Spoken target |
|---|---------------------------|-------------|--------|---------|---------------|
| 1 | Opening                   | 0:00–0:20   | 0:20   | —       | ~50 words     |
| 2 | Live demo                 | 0:20–4:00   | 3:40   | 3–4 min | ~330 words    |
| 3 | What you built            | 4:00–6:30   | 2:30   | 2–3 min | ~360 words    |
| 4 | Debugging insight         | 6:30–7:50   | 1:20   | 1–2 min | ~195 words    |
| 5 | Tradeoff discussion       | 7:50–9:10   | 1:20   | 1–2 min | ~195 words    |
| 6 | Close                     | 9:10–9:30   | 0:20   | —       | ~50 words     |

**Hard checkpoints.** If you are not starting "What you built" at 4:00, cut demo
narration. If you are not starting debugging at 6:30, drop the composer/caching
sentence. If you pass 7:50, stop the debugging story on the lesson line. If you pass
9:10, go straight to the close.

Spoken totals are deliberately lower than a word-per-minute estimate: the demo has
waiting time, and the architecture section has one pause per diagram beat. Do not
fill silence with new content.

---

## Before recording (not part of the runtime)

- `.env`: provider key set, `PRIMARY_PROVIDER=groq` (or `ollama`), `MODEL_FAST` and
  `MODEL_STRONG` set, `RUN_TIMEOUT_S=300`, `MAX_CONCURRENCY=1` on a free tier, and
  `LLM_MIN_INTERVAL_S=4.2` on Gemini's free tier to stay under 15 requests/minute.
- Smoke-test first with preset **iii** (`examples/smoke_request.json`); it is the
  fastest end-to-end run and confirms the key and model IDs before the graded runs.
- Start the server: `uvicorn app.main:app --host 127.0.0.1 --port 8000`.
- Open <http://127.0.0.1:8000/> and hard-refresh. Zoom to 110–125%.
- Keep `docs/demo_test_inputs.md` open in a second window for the exact prompts.
- Warm the cache by running both requests once beforehand, and keep the two generated
  `.docx` files in `data/documents` as fallback. The recording should still show a
  live run; the fallback is only for a network or quota failure.
- Have these files ready in an editor tab for the architecture and debugging segments:
  `app/agent/orchestrator.py`, `app/agent/planner.py`, `app/agent/reflector.py`,
  `app/agent/reconcile.py`, `app/tools/budget.py`, `app/render/docx_renderer.py`,
  `app/api/routes.py`.
- Do a 10-second recording test for font size and audio level.

---

## 1. Opening — 0:00–0:20

*On screen: the demo page, idle.*

> "Hi, I'm [name]. This is Productivity, an autonomous business-document agent built
> with Python and FastAPI. It takes a plain-English request, writes its own task plan,
> runs deterministic tools, reviews and repairs its own draft, and returns a Word
> document. I'll demo it, then cover the architecture, a real bug I fixed, and one
> trade-off."

---

## 2. Live demo — 0:20–4:00 (3:40)

### 2a. The two inputs — 0:20–0:40

*Point at the two presets, then click the first.*

> "Two requests. The first is standard: a twelve-week project plan with five people and
> a fixed budget. The second is deliberately difficult: a board decision memo where the
> budget and compliance scope are unresolved and the fastest option conflicts with the
> compliant one. A third preset is just a quick smoke test I used before recording."

### 2b. Standard run — 0:40–2:15

*Click preset i, let the request text show for two seconds, then press Generate document.*

> "I'll run the standard case first. The UI submits the request and streams progress
> over server-sent events."

*As the stages appear, read the plan list, not the log.*

> "First the request is validated and analyzed: document type, known facts, assumptions,
> missing information, conflicts. Then the planner emits a dependency graph. Each node
> is either a deterministic tool or a writing task. Here you can see the budget, the
> timeline, and the risk register, each with its dependencies, followed by the section
> writers."

> "The executor runs dependency layers concurrently: tools first, then writers that can
> cite the tool output. The composer assembles the summary, the reflector checks the
> draft, and a deterministic renderer builds the Word file. The model never writes the
> `.docx` itself."

*When the result appears, open the `.docx` and scroll once through the budget table and
timeline.*

> "Here is the file. The budget table uses exact decimal arithmetic, so subtotal,
> contingency, and total reconcile. The timeline is weekend-aware, and every risk has a
> likelihood, an impact, and a mitigation. The assumptions the request left open are
> stated explicitly."

### 2c. Complex run — 2:15–3:50

*Click preset ii. Do not read the whole prompt aloud; name the three problems.*

> "Now the complex case. The budget is unresolved, the compliance scope is unresolved,
> and the request says outright that the fastest option is not the most compliant. There
> is no single correct plan, so the agent has to choose one and record why."

*Press Generate. Narrate over the wait; if the plan appears late, repeat the sentence
about the comparison instead of adding new content.*

> "Watch what changes. Analysis now records missing information and conflicts instead of
> inventing answers. The planner adds a weighted option comparison, because the request
> asks for a ranked recommendation, not a template. The writers then have to justify a
> path under those constraints."

*At the result, point at Assumptions, Missing information, Conflicts addressed, then the
reflection fact.*

> "These panels are the agent's reasoning made visible: the planning budget it assumed,
> the unresolved compliance scope, and the speed-versus-compliance conflict it was told
> about. Reflection ran here too — [passed / repaired the section that …]. The memo ends
> with a prioritized path rather than a single invented answer."

### 2d. Handoff — 3:50–4:00

> "That's the end-to-end behaviour. Now the architecture behind it."

---

## 3. What you built — 4:00–6:30 (2:30)

*On screen: a split view of the repo — `orchestrator.py` on the left, `tools/` and
`routes.py` on the right. Scroll only where you point.*

> "It's a staged async pipeline in Python. FastAPI exposes `POST /agent` for a
> synchronous run, `POST /runs` plus an SSE stream for long runs, and
> `GET /documents/{run_id}` for the file. Errors use one envelope with a code, message,
> and request id; optional API-key auth and a request-length guardrail sit in front."

> "Six stages. Guardrails validate and sanitize the request. The analyzer returns the
> document type, complexity, known facts, assumptions, missing information, and
> conflicts. The planner returns a task DAG with IDs, dependencies, tool choices, and
> acceptance criteria. The executor runs it."

> "The LLM is reached through an OpenAI-compatible client, so Groq, Gemini, OpenRouter,
> and a local Ollama server share one code path. Two model tiers: a fast model for
> analysis and planning, a strong one for writing and review. Every response is parsed
> into Pydantic models; invalid output is rejected and retried, and a fallback provider
> sits behind a circuit breaker."

> "Tools are ordinary typed Python functions in a registry: exact-decimal budget, a
> weekend-aware timeline, a curated risk library, weighted option comparison, section
> templates, and mock company data. The planner can only name registered tools, and the
> executor validates arguments before calling them. The comparison tool reports missing
> scores instead of inventing them."

> "Execution is layer by layer: independent tasks run concurrently under a concurrency
> cap, and a failed task retries before it fails the run with a structured error. That is
> also where the reflection improvement lives — deterministic checks plus an LLM critic,
> up to two loops, targeting repairs at specific sections and bounded by a timeout."

> "Finally, the validated block models go to a `python-docx` renderer: headings,
> paragraphs, bullets, tables, and callouts. The file is stored by run ID and streamed
> back. 150 tests run against a fake LLM, so the suite never touches the network, and
> the same run ID appears in every structured log line."

---

## 4. Debugging insight — 6:30–7:50 (1:20)

*On screen: `app/agent/reconcile.py`, then the matching test file.*

> "The bug I hit was a document that contradicted itself. In the decision memo, an
> assumption stated a budget figure that disagreed with the budget table two pages
> later. Same document, two numbers."

> "Root cause: two writers of truth. Analysis writes assumptions before any tool runs,
> and when the request doesn't specify a number it can infer one. The budget tool runs
> later and computes an exact total from staffing lines. Nothing reconciled the two, so
> the inferred sentence survived into the draft."

> "I traced it from the `.docx` back into state: compare `analysis.assumptions` with the
> `estimate_budget` task result. The mismatch only appeared when the request omitted the
> figure — exactly the complex case, which is why the standard plan looked fine."

> "The fix is a reconciliation stage after execution: detect quantified budget
> assumptions that were inferred, replace them with the tool's exact total, and add
> subtotal, contingency, and total rows when the table omits them. It has its own
> regression tests. The lesson: tools own numbers; the model may phrase them, but
> something has to reconcile prose with tool output after execution, not just in the
> prompt."

---

## 5. Tradeoff discussion — 7:50–9:10 (1:20)

*On screen: `planner.py` next to `models/plan.py`.*

> "The trade-off is autonomous planning versus a deterministic workflow. A fixed
> template pipeline is faster and predictable: same steps, same shape, easy to test. But
> it cannot handle a request that withholds its own structure, which is the whole point
> of the complex case."

> "I chose LLM-proposed planning, constrained by a schema. The model decides the tasks,
> dependencies, and tools, which buys adaptability. The cost is latency, token spend,
> and variance — every run makes several model calls, and output quality tracks the
> provider."

> "So the autonomy is fenced. The planner can only emit a validated DAG over registered
> tools. Numbers come from deterministic code. Caching removes repeated analysis and
> planning. Model tiers keep cheap stages cheap. Reflection and fallback absorb bad
> output, and timeouts bound the worst case. The result is a system that adapts its plan
> but not its guarantees — the structure and the numbers stay reproducible, which is the
> trade I wanted for business documents."

---

## 6. Close — 9:10–9:30

> "That's Productivity: autonomous planning, deterministic tools, a reviewed draft, and
> a Word file — exposed through a small, testable API. Thanks for watching."

---

## If you run long — cut in this order

1. The composer sentence in "What you built".
2. The structured-logging sentence.
3. The mock-company-data mention.
4. In the demo, the timeline description; keep the budget reconciliation point.
5. In the tradeoff, the model-tier sentence.

Never cut: the second test case, the reflection explanation, the root cause, or the
lesson from the bug.
