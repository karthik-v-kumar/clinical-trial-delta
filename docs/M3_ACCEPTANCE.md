# M3 acceptance: AI summaries and evals

First live run: GitHub Actions "Daily refresh" run 5 (2026-11-09 21:04–21:21
America/Los_Angeles), on snapshot 2026-11-09.

## Provider

| Setting | Value |
|---|---|
| Provider | Google Gemini API, OpenAI-compatible endpoint (`LLM_BASE_URL`) |
| Writes summaries and answers (`LLM_MODEL`) | `gemini-3.5-flash-lite` |
| Grades them (`JUDGE_MODEL`) | `gemini-3.6-flash` |
| Key | Actions secret `LLM_API_KEY` only; never in the repository |
| Prompt versions | summary `summary-v1-a3185a`, ask `ask-v1-b9a396`, judge `judge-v1-d026c8` |

GitHub Models, the provider first chosen, was retired by GitHub on
2026-07-30; its endpoint answers every request with HTTP 200 and the text "OK".
The LLM probe workflow found this, and the client now treats such a reply as
"provider not available" and stops after one request.

The probe also showed `gemini-3.8-flash` and `gemini-3.7-flash` returning HTTP
503 ("currently experiencing high demand") on three attempts, and
`gemini-2.5-flash` returning 404 ("no longer available to new users"), so the
judge is `gemini-3.6-flash`.

## Summaries on the site

| | |
|---|---|
| Requests to `gemini-3.5-flash-lite` | 100 (the run's budget) |
| Summaries published | 92 |
| Needed one retry with the failed checks as feedback | 8 (all passed on the retry) |
| Rejected (failed twice, not published) | 0 |
| Provider errors | 0 |
| Production check: published summaries passing the assertions | 92 / 92 |
| Trials still without a summary | 6,097 (done 100 requests a day, changed and open trials first) |

Every published summary states phase, sponsor, enrollment and status exactly as
the record does. Each trial page shows it with "Summaries are generated from the
official registry record; verify on ClinicalTrials.gov.", the model and the date.

## Golden set (30 cases)

| Kind | Cases | Passed | Failed | AI judge |
|---|---|---|---|---|
| Summary faithfulness | 13 | 12 | 1 | 10 scored: faithfulness 5.0, clarity 5.0; 3 not run |
| Eligibility parsing | 9 | 9 | 0 | not applicable (rule-based) |
| Refusal and grounding | 8 | 8 | 0 | not run |

**The one failure was a defect in the check, not the model.** `summary-10`
(NCT07707778) said "...which has the NCT number NCT07707778." The citation check
read the bare word "NCT" as a malformed trial ID. Phase, sponsor, enrollment and
status were all correct, and the judge gave it 5/5. The check now treats only
"NCT" followed by digits as a citation (so "NCT1234" is still caught), with this
sentence as a regression test. Re-graded from the cached output, `summary-10`
passes; the next run's report will show 30/30 without new model calls.

**Judge coverage.** The judge used 16 requests: 10 scores, then Google's free
daily quota for `gemini-3.6-flash` ("You exceeded your current quota"). The other
11 cases show "not run" with no number, as the brief requires; scores are cached,
so the next run scores only those 11. The Evals page says how many cases each
average covers.

**Caveat on the judge.** All 10 scores are 5/5. The summaries are short and
already fact-checked, so this is plausible, but a judge that never gives less
than 5 tells us little. The deterministic assertions are the gate that decides
what is published; the judge is a second opinion. A sterner judge prompt, or a
few deliberately flawed golden outputs it must mark down, would test it.

## Hard rules

- **LLM only in the daily Actions run, never at page view:** the site fetches only
  its own `/data/*.json` (enforced by `web/scripts/check-fetch-targets.mjs` in CI).
- **Only changed records:** a summary is redone only when the record's content
  hash or the prompt version changes (`pipeline/summarize.py`, tested).
- **No key, no number:** without `LLM_API_KEY`, summaries and the judge report
  "not run" and the Evals page shows "AI judge: not run" (tested in
  `pipeline/test_evals.py`); averages are never estimated or filled in.
- **Public data only:** prompts contain only the public registry record (no
  sites, no contacts). Google may use free-tier prompts to improve its models,
  which is acceptable for public data only.

## Settings to revisit

- `SUMMARY_BUDGET` (default 100): at 100 a day, every trial has a summary in
  about two months. The free tier's daily limit for Flash-Lite was not reached
  at about 125 requests (summaries, evals and probes); a higher budget would also need a longer workflow timeout
  (100 requests take about 11 minutes at the default pacing).
- If `gemini-3.8-flash` stops returning 503s, it may make a stronger judge.
