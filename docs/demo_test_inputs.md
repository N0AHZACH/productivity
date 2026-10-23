# Demo test inputs

Two requests cover the assignment's standard and complex cases, plus a third quick
input for smoke testing. They are the same strings used by the preset buttons
(**i**, **ii**, **iii**) in the browser demo, and they live in the repo as runnable
JSON files:

- `examples/standard_request.json` — Test input 1
- `examples/complex_request.json` — Test input 2
- `examples/smoke_request.json` — Test input 3 (quick check, not part of the graded demo)

Run them from the UI (presets **i**, **ii**, **iii**), the API, or the CLI:

```bash
# API
curl -s -X POST http://localhost:8000/agent \
  -H "Content-Type: application/json" \
  --data @examples/standard_request.json

curl -s -X POST http://localhost:8000/agent \
  -H "Content-Type: application/json" \
  --data @examples/complex_request.json

# CLI (prints run id, document path, reflection result, warnings)
python scripts/demo.py --example examples/standard_request.json
python scripts/demo.py --example examples/complex_request.json
python scripts/demo.py --example examples/smoke_request.json
```

---

## Test input 1 — standard business request (project plan)

```json
{
  "request": "Write a project plan for launching a customer support chatbot at a mid-size retail company. The target is a twelve-week launch with a team of five, working within a fixed operating budget. Include the budget, delivery timeline, risks, and immediate next steps."
}
```

**Why this is the standard case.** The request is fully specified: one document
type, a fixed deadline, a fixed team size, and a named budget constraint. Nothing
conflicts, and the agent does not have to invent the brief — it has to plan and
execute it well.

**What it exercises.**

- Analysis settles on `project_plan` and needs few or no assumptions.
- Planning produces a DAG that includes the deterministic tools:
  - `estimate_budget` — staffing lines, Decimal arithmetic, subtotal, contingency, total
  - `build_timeline` — ordered phases, weekend-aware dates, milestones
  - `generate_risk_register` — likelihood/impact/mitigation scored from the risk library
  - `get_section_template` — recommended `project_plan` outline
- Writing tasks depend on those tool tasks, so prose can only cite real tool output.
- Reflection normally passes on the first loop.

**What to point at on screen.**

1. The plan list: task IDs, kinds, and `depends on` edges — tools first, writers after.
2. The budget table in the `.docx`: line items, then subtotal, contingency, and a total
   that reconcile exactly.
3. The phase timeline: sequential dates that skip weekends, with milestones.
4. The assumptions section: only the gaps the request actually left (for example role
   day rates), stated explicitly instead of buried in prose.

---

## Test input 2 — complex request (decision memo)

```json
{
  "request": "Prepare a board-level decision memo on replacing our legacy billing platform. We must deliver before Q1, the current contract expires in March, and the project must satisfy data-protection and financial-control requirements. The available budget and the compliance scope are both unresolved, and the fastest option is not the most compliant. Compare credible options and recommend a prioritized path."
}
```

**Why this is the complex case.** The brief contains all four difficulty modes the
assignment asks for:

- **Missing information** — the budget amount and the compliance scope are unresolved.
- **Conflicting requirements** — the fastest option is explicitly not the most compliant.
- **Time pressure** — delivery before Q1 versus a contract that expires in March.
- **Open structure** — the request asks for a comparison and a prioritized path, not a
  fixed outline, so the agent has to decide how to satisfy it.

**What it exercises.**

- Analysis must record the unresolved items as missing information and conflicts
  rather than silently guessing.
- Planning has to choose a comparison strategy; it typically adds `compare_options`
  with weighted criteria and scores, plus a risk register and extra writing tasks.
- Assumptions become visible in the result panel — budget planning figure, compliance
  scope, migration approach.
- Reflection is more likely to run its critic and repair a section, because the
  document has to justify a recommendation instead of describing a plan.

**What to point at on screen.**

1. The result panel's **Assumptions**, **Missing information**, and **Conflicts
   addressed** lists — this is the agent showing its reasoning, not hiding it.
2. The `compare_options` task and its weighted ranking; missing scores are reported,
   never invented.
3. The **Reflection** fact — passed, or repaired with issues found.
4. The memo's recommendation: a prioritized path with the speed-versus-compliance
   trade-off stated.

---

## Test input 3 — quick smoke check (meeting minutes)

```json
{
  "request": "Turn my notes from this morning's weekly operations sync into proper meeting minutes. The notes are: shipped the returns portal fix; support queue is still high after the holiday; Priya raised that nobody owns the chatbot knowledge base; we agreed to freeze non-critical releases on Fridays; next sync is Tuesday. I did not capture the attendee list or the exact date."
}
```

**Why it exists.** It is the fastest full run (a different document type, fewer
sections, no financial tools), so it is the input to use when checking a fresh
provider key, a new model, or a code change before recording. It also exercises
missing information: the date and attendee list become stated assumptions rather
than invented facts.

Run it from preset **iii**, the API, or the CLI:

```bash
python scripts/demo.py --example examples/smoke_request.json
```

**What to point at on screen.** The `meeting_minutes` document type, the
Assumptions panel (date and attendee list), and the Action Items table with owners
and due dates.

---

## Why inputs 1 and 2, together

The standard case proves the agent can execute a clear brief end to end. The complex
case proves it can plan under ambiguity and make defensible assumptions. Showing both
is what the assignment means by "one standard business request and one complex
request", and they take the same UI path, so the demo stays simple.

## Demo tips

- On a free tier, keep `RUN_TIMEOUT_S` generous (for example `300`) and
  `MAX_CONCURRENCY=1`; the complex memo makes more model calls than the plan.
- Set `LLM_MIN_INTERVAL_S=4.2` on Gemini's 15-requests/minute free tier so the run is
  paced under the cap instead of retrying 429s.
- Run preset **iii** once before the graded runs: it is the fastest way to confirm the
  provider key, model IDs, and document pipeline on a new machine.
- Analysis and plan results are cached by normalized request, so a second run of the
  same input starts faster. Generated files remain under `data/documents/{run_id}.docx`.
- Record the standard run first: it is the fastest way to show a complete document,
  and it warms the UI before the longer memo.
