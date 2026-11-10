// Evals page: how the AI summaries and the eligibility checklist are tested.
// Pass rates by case type, judge scores (or "not run"), run history, and a
// gallery of every failing case with the record, the output and the expected
// answer side by side.

import { barChart } from "../charts";
import type { Page } from "../config";
import { loadEvalHistory, loadEvalReport, loadSummaryIndex } from "../data";
import { h, link, type Child } from "../dom";
import { formatDate, formatInt, plural } from "../format";
import { term } from "../glossary";
import type { EvalCase, EvalReport, EvalTypeRow, SummaryIndex } from "../types";
import { emptyState, section, stat } from "../ui";

const percent = (value: number) => `${Math.round(value)}%`;
const NO_PROVIDER = "no LLM provider configured";

/** Pipeline notes are written for maintainers; say the common case in plain words. */
function plainNote(note: string | null | undefined): string | null {
  if (!note) return null;
  if (note.startsWith(NO_PROVIDER)) return "No AI provider is set up for the daily refresh yet.";
  return note;
}

const modelLabel = (model: string | null, provider: string | null) =>
  model ? (provider ? `${model} (${provider})` : model) : "None set up";
const score = (value: number) => (Number.isInteger(value) ? String(value) : value.toFixed(1));

function passRateChart(rows: EvalTypeRow[]): HTMLElement {
  const graded = rows.filter((r) => r.pass_rate !== null);
  return barChart({
    title: ["Cases passing the ", term("assertion", "automatic checks")],
    summary: "Bar chart of the share of golden-set cases that pass, by kind of case.",
    data: graded.map((r) => ({
      key: r.type,
      label: `${r.label} (${r.passed} of ${r.passed + r.failed})`,
      axisLabel: r.label,
      value: (r.pass_rate ?? 0) * 100,
    })),
    categoryTitle: "Kind of case",
    valueTitle: "Cases passing (%)",
    unit: ["", "passing"],
    orientation: "bars",
    max: 100,
    step: 25,
    format: percent,
    noShare: true,
  });
}

function judgeChart(rows: EvalTypeRow[]): HTMLElement {
  const scored = rows.filter((r) => r.judge.status === "scored");
  const data = scored.flatMap((r) => [
    { key: `${r.type}-f`, label: `${r.label}: faithfulness`, axisLabel: `${r.label}: faithful`, value: r.judge.faithfulness_mean ?? 0 },
    { key: `${r.type}-c`, label: `${r.label}: clarity`, axisLabel: `${r.label}: clear`, value: r.judge.clarity_mean ?? 0 },
  ]);
  return barChart({
    title: ["Average ", term("ai-judge", "AI judge"), " score"],
    summary: "Bar chart of the AI judge's average faithfulness and clarity scores, from 1 to 5.",
    data,
    categoryTitle: "Kind of case and score",
    valueTitle: "Average score (1 to 5)",
    unit: ["", "out of 5"],
    orientation: "bars",
    max: 5,
    step: 1,
    format: score,
    noShare: true,
  });
}

function judgeNotRun(report: EvalReport): HTMLElement {
  return h(
    "div",
    { class: "empty", role: "note" },
    h("p", { class: "empty-title" }, "AI judge: not run"),
    h(
      "p",
      null,
      "The judge scores answers only when an AI provider is available to the daily refresh. No score is estimated or filled in while it has not run.",
    ),
    report.run.judge_note ? h("p", { class: "muted" }, `Reason: ${plainNote(report.run.judge_note)}`) : null,
  );
}

function pretty(value: unknown): string {
  if (value === null || value === undefined) return "(none)";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function panel(title: string, value: unknown): HTMLElement {
  return h("div", { class: "panel" }, h("h4", null, title), h("pre", { class: "registry-pre" }, pretty(value)));
}

function caseOutput(c: EvalCase): unknown {
  if (!c.output) return null;
  if (c.type === "summary") return c.output;
  if (c.type === "refusal") return c.output;
  const out = c.output as { parsed?: boolean; inclusion?: { text: string }[]; exclusion?: { text: string }[]; notes?: string[] };
  return out;
}

function failureCard(c: EvalCase): HTMLElement {
  const inputTitle = c.type === "eligibility" ? "The criteria text" : "The record (what the AI saw)";
  const input = c.type === "eligibility" ? (c.input["criteria"] as string) : c.input;
  return h(
    "li",
    { class: "failure", id: c.id },
    h(
      "div",
      { class: "change-meta" },
      h("span", { class: "change-type" }, c.type),
      h("span", null, c.id),
      c.nct_id ? link(`/t/${c.nct_id}`, null, c.nct_id) : null,
    ),
    h("p", { class: "change-headline" }, c.title),
    h("ul", { class: "problems" }, c.problems.map((p) => h("li", null, p))),
    h("div", { class: "panels" }, panel(inputTitle, input), panel("What was produced", caseOutput(c)), panel("Expected", c.expected)),
    c.output_meta?.model ? h("p", { class: "muted" }, `Produced by ${c.output_meta.model}`) : null,
  );
}

function historyTable(history: NonNullable<Awaited<ReturnType<typeof loadEvalHistory>>>): HTMLElement {
  const runs = [...history.runs].reverse().slice(0, 30);
  const cell = (run: (typeof runs)[number], type: string) => {
    const row = run.by_type.find((r) => r.type === type);
    if (!row || row.pass_rate === null) return "—";
    const judge =
      row.faithfulness_mean !== null && row.clarity_mean !== null
        ? ` · judge ${score(row.faithfulness_mean)}/${score(row.clarity_mean)}`
        : "";
    return `${percent(row.pass_rate * 100)}${judge}`;
  };
  return h(
    "div",
    { class: "table-wrap scroll-x" },
    h(
      "table",
      { class: "history" },
      h(
        "thead",
        null,
        h(
          "tr",
          null,
          ["Date", "Model", "Prompt version", "Summaries", "Eligibility", "Refusals", "Judge"].map((label) => h("th", { scope: "col" }, label)),
        ),
      ),
      h(
        "tbody",
        null,
        runs.map((run) =>
          h(
            "tr",
            null,
            h("td", null, formatDate(run.date)),
            h("td", null, run.model ?? "none"),
            h("td", null, run.prompt_versions?.["summary"] ?? "—"),
            h("td", { class: "num" }, cell(run, "summary")),
            h("td", { class: "num" }, cell(run, "eligibility")),
            h("td", { class: "num" }, cell(run, "refusal")),
            h("td", null, run.judge_status === "scored" ? run.judge_model : "not run"),
          ),
        ),
      ),
    ),
  );
}

function productionBlock(report: EvalReport, index: SummaryIndex | null): HTMLElement {
  const p = report.production;
  const tiles: HTMLElement[] = [
    stat(formatInt(p.published), "summaries published"),
    stat(formatInt(p.passed), ["current ones passing the ", term("assertion", "checks")]),
    stat(formatInt(p.rejected), "rejected before publishing"),
  ];
  if (index) tiles.push(stat(formatInt(index.to_do), "still to write"));
  return h(
    "div",
    null,
    h("div", { class: "stats stats-wide" }, tiles),
    index
      ? h(
          "p",
          { class: "section-note" },
          index.status === "not_run"
            ? `The last summary run did not call a model. ${plainNote(index.stopped_because) ?? ""}`.trim()
            : `The last run wrote ${plural(index.written, "summary", "summaries")} with ${modelLabel(index.model, index.provider)}.` +
                (index.stopped_because ? ` It stopped early: ${plainNote(index.stopped_because)}.` : ""),
        )
      : null,
    p.failures.length
      ? h(
          "ul",
          { class: "changes" },
          p.failures.map((f) =>
            h(
              "li",
              { class: "change" },
              h("div", { class: "change-meta" }, h("span", { class: "change-type" }, f.rejected ? "Rejected" : "Failing"), link(`/t/${f.nct_id}`, null, f.nct_id)),
              h("ul", { class: "problems" }, f.problems.map((x) => h("li", null, x))),
              f.summary ? h("p", { class: "change-detail" }, `Output: ${f.summary}`) : null,
            ),
          ),
        )
      : null,
  );
}

export async function evalsPage(): Promise<Page> {
  const [report, history, index] = await Promise.all([loadEvalReport(), loadEvalHistory(), loadSummaryIndex()]);
  const intro = h(
    "div",
    { class: "hero" },
    h("h1", null, "How the AI is checked"),
    h(
      "p",
      { class: "lede" },
      "This site uses AI in two places: plain-language trial summaries, and an automatic eligibility checklist (which is rule-based, not AI). Both are tested on every daily refresh against a ",
      term("golden-set", "golden set"),
      " of hand-written cases with known answers.",
    ),
  );
  if (!report) {
    return {
      title: "Evals",
      node: h("div", { class: "page" }, intro, emptyState("No eval run yet", "Results appear here after the next daily refresh.")),
    };
  }
  const run = report.run;
  const failures = report.cases.filter((c) => c.status === "fail");
  const waiting = report.cases.filter((c) => c.status === "no_output");
  const graded = report.by_type.filter((r) => r.pass_rate !== null);

  const kinds: Child = h(
    "ul",
    { class: "kinds" },
    report.by_type.map((r) =>
      h(
        "li",
        null,
        h("strong", null, r.label),
        ` — ${plural(r.cases, "case")}: `,
        r.type === "summary"
          ? "a trial record and questions it answers (phase, sponsor, enrollment, status). The AI summary must state them exactly."
          : r.type === "eligibility"
            ? "a criteria text and the correct checklist. The checklist must match item for item."
            : ["a question the record cannot answer (", term("refusal", "refusal"), "). The AI must say so and must not invent specifics."],
      ),
    ),
  );

  const node = h(
    "div",
    { class: "page", style: "--accent: var(--eval-accent)" },
    intro,
    h(
      "dl",
      { class: "facts facts-run" },
      h("div", { class: "fact" }, h("dt", null, "Latest run"), h("dd", null, `${formatDate(run.date)} · ${plural(run.golden_cases, "case")}`)),
      h("div", { class: "fact" }, h("dt", null, "Summary model"), h("dd", null, modelLabel(run.model, run.provider))),
      h("div", { class: "fact" }, h("dt", null, "Prompt version"), h("dd", null, run.prompt_versions["summary"] ?? "—")),
      h("div", { class: "fact" }, h("dt", null, "Judge"), h("dd", null, run.judge_status === "scored" && run.judge_model ? run.judge_model : "Not run")),
    ),
    section("checks", ["Automatic checks (", term("assertion", "assertions"), ")"], kinds,
      graded.length ? passRateChart(report.by_type) : null,
      h(
        "p",
        { class: "section-note" },
        `${formatInt(run.passed)} passed, ${formatInt(run.failed)} failed` +
          (run.no_output ? `, ${formatInt(run.no_output)} waiting for a model run (shown below, not counted as passes or failures).` : "."),
      ),
    ),
    section("judge", "Judge scores", run.judge_status === "scored" ? judgeChart(report.by_type) : judgeNotRun(report)),
    section("production", "Summaries on the site", productionBlock(report, index)),
    section(
      "failures",
      `Failing cases (${failures.length})`,
      failures.length
        ? h("ul", { class: "failures" }, failures.map(failureCard))
        : emptyState("No failing cases", "Every case that has an output passes its checks."),
      waiting.length
        ? h(
            "details",
            { class: "definition" },
            h("summary", null, `Waiting for a model run (${waiting.length})`),
            h("ul", { class: "queries" }, waiting.map((c) => h("li", null, `${c.id}: ${c.title}`))),
          )
        : null,
    ),
    section("history", "Run history", history?.runs.length ? historyTable(history) : emptyState("No history yet", "")),
  );
  return { title: "Evals", node };
}
