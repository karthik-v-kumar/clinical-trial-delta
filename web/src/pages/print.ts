// A one-page summary of a trial to print or save as PDF and take to a doctor:
// the key facts, the AI summary when it is current, the eligibility checklist,
// and questions to ask the study team. Everything comes from the same trial
// file as the trial page.

import { APP_NAME, type Page } from "../config";
import { loadSummary, loadTrial } from "../data";
import { h, link, type Child } from "../dom";
import {
  allocationLabel,
  formatDate,
  formatDateValue,
  formatEnrollment,
  formatInt,
  interventionTypeLabel,
  maskingLabel,
  phaseBucket,
  phaseLabel,
  plural,
  sexLabel,
  statusLabel,
} from "../format";
import type { ChecklistItem, Meta, Trial } from "../types";

/** General questions, in the spirit of ClinicalTrials.gov's own guidance for people considering a study. */
const QUESTIONS = [
  "What is this trial trying to find out, and why might it suit me?",
  "What would I have to do: how many visits, tests and how long in total?",
  "Could I be given a placebo instead of the treatment being tested?",
  "What are the possible risks and side effects, and who do I call if something goes wrong?",
  "Would I need to stop or change any of my current medicines?",
  "Will it cost me anything? Are travel and time paid for?",
  "Can I keep seeing my own doctor during the trial?",
  "What happens when the trial ends: can I keep the treatment, and will I hear the results?",
];

function row(label: string, ...value: Child[]): HTMLElement {
  return h("tr", null, h("th", { scope: "row" }, label), h("td", null, ...value));
}

function ages(trial: Trial): string {
  const { minimum_age: min, maximum_age: max } = trial.eligibility;
  const lower = (text: string) => text.toLowerCase();
  if (min && max) return `${lower(min)} to ${lower(max)}`;
  if (min) return `${lower(min)} and older`;
  if (max) return `up to ${lower(max)}`;
  return "No age limits listed";
}

function items(list: ChecklistItem[]): HTMLElement {
  return h(
    "ul",
    { class: "print-criteria" },
    list.map((item) =>
      h(
        "li",
        null,
        item.text,
        item.sub_items?.length ? h("ul", null, item.sub_items.map((sub) => h("li", null, sub))) : null,
      ),
    ),
  );
}

export async function printPage(meta: Meta, nctId: string): Promise<Page> {
  const [trial, summary] = await Promise.all([loadTrial(nctId), loadSummary(nctId)]);
  const title = trial.brief_title ?? trial.nct_id;
  const current = summary && summary.content_hash === trial.content_hash ? summary : null;
  const recruiting = trial.sites.filter((s) => s.status === "RECRUITING").length;
  const countries = new Set(trial.sites.map((s) => s.country).filter(Boolean)).size;
  const placebo = trial.arm_groups.some((a) => a.type === "PLACEBO_COMPARATOR" || a.type === "SHAM_COMPARATOR");
  const checklist = trial.eligibility_checklist;
  const design = [
    trial.design.allocation ? allocationLabel(trial.design.allocation) : "",
    trial.design.masking ? maskingLabel(trial.design.masking) : "",
  ].filter((part) => part && part !== "—");

  const node = h(
    "article",
    { class: "page print-page" },
    h(
      "p",
      { class: "print-actions" },
      link(`/t/${trial.nct_id}`, null, "← Back to the trial page"),
      h("button", { type: "button", class: "button button-primary", onclick: () => window.print() }, "Print or save as PDF"),
    ),
    h(
      "header",
      { class: "print-head" },
      h("p", { class: "eyebrow" }, "Clinical trial summary to discuss with your doctor"),
      h("h1", null, title),
      h(
        "p",
        { class: "print-ids" },
        `${trial.nct_id} · ${trial.registry_url}`,
        h("br"),
        `From ${APP_NAME}, with data from ClinicalTrials.gov as of ${formatDate(meta.snapshot_date)}.`,
      ),
    ),
    h(
      "table",
      { class: "print-facts" },
      h(
        "tbody",
        null,
        row("Status", statusLabel(trial.overall_status)),
        row("Phase", phaseLabel(phaseBucket(trial.phases))),
        row(
          "What is tested",
          trial.interventions.length
            ? trial.interventions.map((iv, i) => [i ? "; " : "", `${iv.name ?? "Unnamed"} (${interventionTypeLabel(iv.type).toLowerCase()})`])
            : "Not listed",
        ),
        row("Placebo group", placebo ? "Yes: some participants receive a placebo or sham" : "None listed"),
        design.length ? row("Design", design.join(" · ")) : null,
        row("For", trial.conditions.join(", ") || "Not listed"),
        row(
          "Who can join",
          `${ages(trial)} · ${sexLabel(trial.eligibility.sex)}`,
          trial.eligibility.healthy_volunteers ? " · also healthy volunteers" : "",
        ),
        row("Enrollment", formatEnrollment(trial.enrollment)),
        row("Sponsor", trial.lead_sponsor?.name ?? "Not listed"),
        row(
          "Sites",
          trial.sites.length
            ? `${plural(trial.sites.length, "site")} in ${plural(countries, "country", "countries")}; ${formatInt(recruiting)} recruiting now`
            : "None listed",
        ),
        row("Start", formatDateValue(trial.start_date)),
        row("Main results expected", formatDateValue(trial.primary_completion_date)),
      ),
    ),
    current
      ? h(
          "section",
          { class: "print-section" },
          h("h2", null, "In plain language"),
          h("p", { class: "print-summary" }, current.summary),
          h(
            "p",
            { class: "print-small" },
            `AI-written summary. Summaries are generated from the official registry record; verify on ClinicalTrials.gov (${trial.registry_url}).`,
          ),
        )
      : null,
    h(
      "section",
      { class: "print-section" },
      h("h2", null, "Who can join"),
      checklist?.parsed
        ? [
            checklist.inclusion.length ? [h("h3", null, `Must have (${checklist.inclusion.length})`), items(checklist.inclusion)] : null,
            checklist.exclusion.length ? [h("h3", null, `Rules you out (${checklist.exclusion.length})`), items(checklist.exclusion)] : null,
          ]
        : trial.eligibility.criteria
          ? h("pre", { class: "registry-pre" }, trial.eligibility.criteria)
          : h("p", null, "The registry record lists no eligibility criteria."),
      h(
        "p",
        { class: "print-small" },
        "Sorted automatically from the registry text. Only the study team can decide whether someone qualifies.",
      ),
    ),
    h(
      "section",
      { class: "print-section print-questions" },
      h("h2", null, "Questions to ask the study team"),
      h("ol", null, QUESTIONS.map((q) => h("li", null, q))),
    ),
    h(
      "section",
      { class: "print-section print-notes" },
      h("h2", null, "Notes"),
      h("div", { class: "print-lines", "aria-hidden": "true" }, Array.from({ length: 5 }, () => h("div", { class: "print-line" }))),
    ),
    h(
      "footer",
      { class: "print-foot" },
      h(
        "p",
        null,
        "Information only, not medical advice. Contacts for the study team and the full, current record: ",
        trial.registry_url,
        `. ${APP_NAME} is independent and not affiliated with ClinicalTrials.gov or the U.S. National Library of Medicine.`,
      ),
    ),
  );
  return { title: `${trial.nct_id} summary for your doctor`, node };
}
