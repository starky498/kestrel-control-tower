# Accessibility and Performance Acceptance

## Scope

Kestrel has two complementary acceptance tracks:

1. a repeatable automated Streamlit audit for all eight registered workspaces; and
2. a manual accessibility review for behavior that AppTest cannot establish.

The automated result is a regression gate, not a WCAG certification, legal accessibility opinion,
or substitute for testing with people who use assistive technology.

## Automated eight-workspace audit

Run from the repository root after a successful analytical build:

```bash
PYTHONPATH=src .venv/bin/python scripts/audit_ui.py \
  --expected-pages 8 \
  --threshold-ms 15000 \
  --output .kestrel/ui-audit.json
```

The script starts the app through Streamlit `AppTest`, discovers the workspace options from the
sidebar, visits each registered workspace, and records:

- expected/discovered workspace counts and `page_count_passed`;
- workspace name;
- measured page render time in milliseconds;
- whether the exact workspace heading is visible;
- Streamlit exceptions;
- Streamlit errors; and
- unlabelled controls across buttons, checkboxes, date inputs, multiselects, numeric inputs,
  radio groups, select sliders, select boxes, sliders, text areas, text inputs, and toggles.

The audit passes only when all discovered workspaces satisfy all of these conditions:

```text
page_count = 8
page_heading_visible = true
exceptions = []
errors = []
unlabelled_controls = []
render_ms <= 15000
```

The JSON also records initial application render time. That value is diagnostic; the implemented
15,000 ms acceptance gate applies to each subsequent workspace render.

The script exits `0` on pass and `1` on a page-count or per-page failure. The report stays under
`.kestrel/` and is excluded from Git because timing and generated evidence are environment-specific.

## Reproducible run conditions

Record these details with any accepted audit:

| Field | Required evidence |
|---|---|
| Commit | Exact Git commit SHA and clean/known worktree state |
| Environment | Local OS/architecture or CI image; Python version |
| Data | Source SHA-256 from the Parquet manifest |
| Warehouse | Build completion timestamp and model release path |
| Optional state | Whether freight, market, weather, and holiday snapshots were present |
| Command | Exact audit command and threshold |
| Result | Exit code, `passed`, page count, slowest page, and report path |

Run on an otherwise stable machine. Do not compare page milliseconds from substantially different
hardware as if they were a product benchmark. If one page exceeds the threshold, retain its report,
identify the query/render path, and rerun after the fix. Do not raise the threshold merely to turn
a failure green; a threshold change is a requirement change and must be documented.

## Current release-evidence status

| Gate | Required value | Status |
|---|---|---|
| Workspace discovery | Exactly eight | Passed: eight discovered |
| Visible heading | Every workspace | Passed: exact heading on all eight |
| Streamlit exception/error | None | Passed: none reported |
| Unlabelled interactive control | None | Passed: none reported |
| Per-workspace render | ≤15,000 ms | Passed: slowest rerender 2,593.651 ms (Executive Command Center) |

Evidence was recorded on 15 August 2026 against the final local release working tree after all 122
tests passed, with the full operational warehouse plus freight, market, weather, and holiday
snapshots. Initial render was 6,937.851 ms; the eight-workspace audit passed 8/8 and the separate
governed-question benchmark passed 8/8 with a slowest case of 1,064.450 ms. The ignored report path is
`.kestrel/ui-audit.json`. A real Chrome visual check also found
no horizontal document overflow at a 390 × 844 viewport. Keyboard-only operation, 200% zoom,
contrast measurement, and screen-reader testing remain pending; no WCAG claim is made.

## Manual accessibility checklist

Complete every item in the browser used for the demonstration. Record Pass, Fail, or Not Applicable
with a short note and the date/reviewer.

### Keyboard and focus

- Reach the sidebar workspace control, global period, quantity basis, customer region, DC region,
  DC, route, outlet/channel controls, and page-specific controls with the keyboard alone.
- Operate radio groups, select boxes, multiselects, buttons, expanders, text areas, and number inputs
  without a pointer.
- Confirm focus is visible against the surrounding surface and follows visual/DOM order.
- Confirm focus is not trapped in the sidebar, Plotly visualization, table, or expander.
- After asking a question or changing workspace, verify focus behavior remains understandable and
  there is no unexpected page jump that prevents continued navigation.

### Names, structure, and instructions

- Confirm each workspace has one clear visible page title matching its sidebar option.
- Confirm every interactive control has a meaningful visible label; placeholder text is not the
  only name.
- Confirm date basis, quantity basis, geography, denominator, minimum-volume threshold, and any
  ignored filter are stated in text.
- Confirm empty, withheld, loading, warning, and error states contain actionable text rather than a
  blank chart or color-only signal.
- Confirm repeated sections and definition expanders have distinct, understandable labels.
- Confirm Ask Kestrel exposes the interpreted definition and evidence and explains ambiguous or
  unsupported questions in plain language.

### Color, contrast, and non-color cues

- Check text, muted captions, links, input borders, focus indicators, chart labels, and warning
  surfaces in the target light/dark appearance actually used.
- Confirm positive, warning, and danger meaning is also communicated with text, label, value, shape,
  or position; red/amber/green alone must not carry meaning.
- Inspect Plotly series and legends for distinguishability without relying on hue alone where
  series overlap.
- Confirm table values and chart hover information are also available through a labelled table or
  written metric/evidence where the decision depends on them.

### Zoom, reflow, and readable scale

- At 200% browser zoom, verify the sidebar, page headings, KPI cards, controls, disclosures, and
  evidence tables remain reachable and do not overlap or clip critical text.
- At a narrow laptop viewport, confirm horizontal overflow does not hide controls or the active
  filter state. A data table may scroll horizontally if its label and purpose remain visible.
- Verify tooltips/help do not contain the only copy of a critical definition or warning.
- Confirm large tables remain bounded and do not create an unusable page-length or browser freeze.

### Screen-reader smoke test

- With VoiceOver or another screen reader, navigate workspace options and hear a meaningful
  “Workspace” group/name.
- Hear labels, roles, state, and current values for the global and page-specific controls.
- Navigate headings and confirm their order describes the page sections.
- Hear KPI label and value together with sufficient context; verify “not available” is not announced
  as zero.
- Confirm warnings, errors, and publication-gate reasons are discoverable and understandable.
- Confirm the Ask Kestrel answer, definition, and evidence are reachable after submission.
- For a decision-critical chart, confirm equivalent tabular or textual evidence is accessible.

## Performance acceptance beyond the script

The 15-second page threshold is a deliberately generous evaluation guard for a local analytical
application. It catches blocking regressions while allowing first-time DuckDB scans and chart
construction on ordinary laptops. It is not a production latency target.

During manual review also verify:

- global filter changes do not trigger an infinite rerun or leave inconsistent page sections;
- external network availability cannot delay dashboard rendering because pages query only local
  published snapshots;
- large evidence views are bounded (for example, operational and API history limits);
- missing optional data produces an immediate local explanation rather than a network timeout;
- Streamlit health responds while the app is ready; and
- repeat navigation does not increase memory or latency without bound in a short session.

The app uses cached service/definition setup, parameterized DuckDB queries, and bounded evidence
tables. At production scale, move common aggregates behind a cache/service and transform
incrementally rather than masking local latency with a higher threshold.

### Ask Kestrel correctness and latency

The separate governed-question benchmark requires complete local market and freight snapshots:

```bash
PYTHONPATH=src .venv/bin/python scripts/benchmark_qa.py \
  --threshold-ms 5000 \
  --output .kestrel/qa-benchmark.json
```

Its eight cases cover a canonical and paraphrased outlet ranking, strict OTIF, ambiguous geography,
an unsupported sentiment request, market price position, freight per case, and discontinued-SKU
evidence. A case passes only when answer status and expected intent fields are correct and execution
is no more than 5,000 ms. This checks a finite governed interface; it is not a claim that arbitrary
natural-language questions are supported.

## Failure triage

| Failure | First checks | Acceptance response |
|---|---|---|
| Workspace count is not eight | `PAGES` registry and sidebar options | Treat as a functional release failure; restore the required workspace registration. |
| Heading not visible | Page header text versus registry name | Make the page title exact and visible; do not weaken the audit string to accept an unrelated caption. |
| Streamlit exception/error | Page report plus terminal output | Fix the underlying query/render failure and add regression coverage. |
| Unlabelled control | Named collection/index in JSON | Add a concise meaningful label; do not rely only on placeholder/help or suppress the audit. |
| Page over 15 seconds | Named workspace, source state, query plan, chart/table size | Profile the page, reduce unnecessary scans/rendering, retain decision evidence, and rerun under recorded conditions. |
| Governed question benchmark fails | Named case, missing external prerequisite, status/intent mismatch, or latency | Restore required snapshots, diagnose router/service behavior, retain the finite contract, and rerun; do not broaden to unrestricted SQL. |
| Keyboard/focus failure | Browser, component, focus sequence | Treat as a manual release failure even when automated JSON passes. |
| Contrast/zoom/screen-reader failure | Exact element and browser/AT combination | Correct theme/content/component behavior and repeat the relevant manual test. |

## Release record template

```text
Commit:
Reviewer and date:
Environment / Python:
Source SHA-256:
Optional snapshots present:
Audit command:
Automated report path:
Automated result / slowest page:
Keyboard and focus:
Contrast and non-color cues:
200% zoom and narrow viewport:
Screen-reader smoke test:
Open exceptions / decision:
```

The automated report is the repository's repeatable regression gate. Manual evidence is strongly
recommended before a production rollout and must be completed before making any WCAG-conformance
claim, but the assignment handoff may proceed with the unchecked items explicitly recorded as a
known limitation. Never translate an AppTest pass into WCAG conformance.
