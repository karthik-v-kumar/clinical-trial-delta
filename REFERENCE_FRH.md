# REFERENCE — financial-research-hub conventions

Distilled Nov 9, 2026 from karthik-v-kumar/financial-research-hub (main) so this
build mirrors its architecture without needing access to that repo. Read together
with BUILD_BRIEF.md; where this note and the brief differ, the brief wins.

## Layout (mirror this shape)

- `pipeline/` — Python package, stdlib-first, Python 3.11. Entrypoint is a module:
  `python -m pipeline.run <mode>`. Fetchers live in a `pipeline/fetch/`
  subpackage, one module per source. A `writers` module is the ONLY path from
  pipeline outputs to `data/` files (see Writers rule below). `pipeline/run.py`
  opens with a docstring that documents each mode; do the same.
- `data/` — committed JSON only; the frontend reads nothing else. One file per
  entity under a subdirectory (`data/tickers/<SYM>.json` there →
  `data/trials/<NCT>.json` here) plus top-level aggregates (`scanner.json`,
  `meta.json`, `watchlist.json` there → `changes.json`, `meta.json` here).
- `web/` — the frontend app lives in its own directory with its own
  `vercel.json`; Vercel's project root is `web/`, not the repo root.
- `.github/workflows/` — refresh automation (below).
- `docs/` — design/ops notes.

## Frontend stack (what "same stack as financial-research-hub" means)

- Vite + TypeScript single-page app (`web/src/`), `web/package.json` +
  `web/vite.config.ts`, static build deployed on Vercel from `web/`.
- Plain hand-rolled CSS and charts — no chart library and no CSS framework in
  FRH. If you introduce one, say so in your report and justify it.
- Charts there are custom SVG with numbered axis ticks — the brief's
  numbered-axes rule is a hard requirement here too.

## Refresh workflow (mirror `.github/workflows/refresh.yml`)

- Triggers: `workflow_dispatch` with a `mode` choice input + a daily `schedule`
  cron. Concurrency group serializes runs (queue, never overlap —
  `cancel-in-progress: false`) so runs can't interleave pushes.
- `permissions: contents: write`; steps: checkout main → setup-python 3.11 with
  pip cache → run pipeline → commit the changed `data/` JSON back to main.
  That push is what redeploys the site.
- Secrets come from GitHub Actions secrets ONLY (FRH: the API keys;
  here: `LLM_API_KEY` for the optional eval judge — absence must mean
  "not run", per the brief). No key is ever committed.

## Run discipline (from `pipeline/run.py` — adopt verbatim in spirit)

- Per-entity failures never kill the run: mark the entity partial, log it,
  continue with the rest.
- `meta.json` records what ran: mode, timestamps (FRH stamps America/Los_Angeles
  ISO), counts — the site's staleness badge reads it.
- Modes are named slots (FRH: premarket / midmorning / midday / evening) so a
  run's provenance is visible in `meta.json`. Here v1 needs one daily mode;
  keep the mode field anyway.

## Writers rule (the Beacon lesson — do not relearn it)

Pipeline modules produce rich internal objects; `writers` copies ONLY an
explicit allowlist of keys into each `data/` JSON file. Anything not on the
allowlist is silently dropped. Therefore: whenever you add a new output field,
add it to the writer allowlist in the SAME change, and name the allowlist
location in your milestone report.

## Tests

- Pipeline tests live beside the code as `pipeline/test_*.py` (FRH:
  `test_prices_twostage.py`, `test_contrarian_stage.py`, …). The brief's differ
  fixtures follow the same pattern.

## Deliberate differences for this project (per BUILD_BRIEF.md)

- v1 scope is smaller than FRH: static data reads only. FRH's `web/api/` +
  `web/server/` (refresh endpoint, accounts) are NOT v1 here — no Refresh
  button, no accounts, no server code.
- Daily refresh only (no intraday modes).
- The eval module and its CI assertions have no FRH counterpart; the brief's
  Eval section is the whole spec for it.
