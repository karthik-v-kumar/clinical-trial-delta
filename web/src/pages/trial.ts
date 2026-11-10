// Trial page: the registry record in plain language, the eligibility checklist,
// the sites, and every change our snapshots have recorded for this trial.

import type { Page } from "../config";
import { loadSummary, loadTrial } from "../data";
import { external, h, link, type Child } from "../dom";
import {
  allocationLabel,
  armTypeLabel,
  formatDate,
  formatDateValue,
  formatEnrollment,
  formatInt,
  interventionTypeLabel,
  maskingLabel,
  modelLabel,
  phaseBucket,
  phaseLabel,
  phaseTermKey,
  plural,
  sexLabel,
  sponsorClassLabel,
  statusLabel,
  statusTermKey,
  whoMaskedLabel,
} from "../format";
import { term } from "../glossary";
import type { AISummary, Checklist, ChecklistItem, Enrollment, Meta, Site, Trial } from "../types";
import { changeItem, collectionTag, emptyState, phaseChip, section, statusChip } from "../ui";

const SITES_OPEN_LIMIT = 12;

function fact(label: Child, ...value: Child[]): HTMLElement {
  return h("div", { class: "fact" }, h("dt", null, label), h("dd", null, ...value));
}

function ageRange(minimum: string | null, maximum: string | null): string {
  const lower = (text: string) => text.toLowerCase();
  if (minimum && maximum) return `${lower(minimum)} to ${lower(maximum)}`;
  if (minimum) return `${lower(minimum)} and older`;
  if (maximum) return `up to ${lower(maximum)}`;
  return "No age limit given";
}

/** The earliest target we recorded, if enrollment has since been replaced by an actual count. */
function earlierTarget(trial: Trial): Enrollment | null {
  if (trial.enrollment?.type !== "ACTUAL") return null;
  for (let i = trial.history.length - 1; i >= 0; i--) {
    const entry = trial.history[i];
    const before = entry?.old_value as Enrollment | null;
    if (entry?.type === "enrollment_change" && before?.type === "ESTIMATED") return before;
  }
  return null;
}

function keyFacts(trial: Trial): HTMLElement {
  const bucket = phaseBucket(trial.phases);
  const design = trial.design;
  const target = earlierTarget(trial);
  const designParts: Child[] = [];
  if (design.allocation) {
    designParts.push(h("li", null, design.allocation === "RANDOMIZED" ? term("randomized", "Randomized") : allocationLabel(design.allocation)));
  }
  if (design.intervention_model) designParts.push(h("li", null, modelLabel(design.intervention_model)));
  if (design.masking) {
    designParts.push(
      h(
        "li",
        null,
        term(design.masking === "NONE" ? "open-label" : "masking", maskingLabel(design.masking)),
        design.who_masked.length ? ` (hidden from ${whoMaskedLabel(design.who_masked)})` : "",
      ),
    );
  }
  return h(
    "dl",
    { class: "facts" },
    fact(
      term("sponsor"),
      trial.lead_sponsor?.name ?? "—",
      trial.lead_sponsor?.class ? h("span", { class: "muted" }, ` · ${sponsorClassLabel(trial.lead_sponsor.class)}`) : null,
      trial.collaborators.length
        ? h("div", { class: "fact-sub" }, term("collaborator", plural(trial.collaborators.length, "collaborator")), ": ", trial.collaborators.join("; "))
        : null,
    ),
    fact(term("phase"), term(phaseTermKey(bucket), phaseLabel(bucket))),
    fact(
      term("status"),
      term(statusTermKey(trial.overall_status), statusLabel(trial.overall_status)),
      trial.why_stopped ? h("div", { class: "fact-sub" }, `Reason given: ${trial.why_stopped}`) : null,
    ),
    fact(
      term("enrollment"),
      formatEnrollment(trial.enrollment),
      target ? h("div", { class: "fact-sub" }, `Earlier target: ${plural(target.count, "person", "people")}`) : null,
    ),
    fact(term("start-date"), formatDateValue(trial.start_date)),
    fact(term("primary-completion"), formatDateValue(trial.primary_completion_date)),
    fact(term("completion"), formatDateValue(trial.completion_date)),
    designParts.length ? fact("Design", h("ul", { class: "plain-list" }, designParts)) : null,
    fact(
      term("results-posted", "Results"),
      trial.has_results
        ? [
            `Posted${trial.results_first_posted ? ` ${formatDate(trial.results_first_posted)}` : ""}. `,
            external(`${trial.registry_url}?tab=results`, null, "Read them on ClinicalTrials.gov"),
          ]
        : "Not posted yet",
    ),
  );
}

function aiSummaryBlock(trial: Trial, summary: AISummary | null): HTMLElement {
  if (!summary) {
    return h(
      "p",
      { class: "note" },
      "A plain-language AI summary has not been written for this trial yet. Summaries are added a few at a time each day.",
    );
  }
  if (summary.content_hash !== trial.content_hash) {
    // Never show a summary of an older version of the record.
    return h(
      "p",
      { class: "note" },
      "The registry record changed after the AI summary was written, so it is hidden until a new one is ready.",
    );
  }
  return h(
    "div",
    { class: "ai-summary" },
    h("p", { class: "source-label" }, term("ai-summary", "AI-written summary")),
    h("p", { class: "ai-summary-text" }, summary.summary),
    h(
      "p",
      { class: "ai-disclaimer" },
      "Summaries are generated from the official registry record; verify on ",
      external(trial.registry_url, null, "ClinicalTrials.gov"),
      ".",
    ),
    h(
      "p",
      { class: "muted ai-meta" },
      `Written by ${summary.model} (${summary.provider}) on ${formatDate(summary.generated_at.slice(0, 10))}. `,
      "Its phase, sponsor, enrollment and status were checked against the record automatically. ",
      link("/evals", null, "How the AI is checked"),
    ),
  );
}

function summarySection(trial: Trial, summary: AISummary | null): HTMLElement {
  return section(
    "summary",
    "What this trial is about",
    aiSummaryBlock(trial, summary),
    trial.brief_summary
      ? h(
          "details",
          { class: "sponsor-summary", open: !summary || summary.content_hash !== trial.content_hash },
          h("summary", null, "The sponsor's own summary, from the registry record"),
          trial.brief_summary.split(/\n\s*\n/).map((paragraph) => h("p", { class: "registry-text" }, paragraph.trim())),
        )
      : h("p", { class: "muted" }, "The registry record has no summary from the sponsor."),
  );
}

function testedSection(trial: Trial): HTMLElement {
  return section(
    "tested",
    "What is being tested",
    trial.interventions.length
      ? h(
          "ul",
          { class: "items" },
          trial.interventions.map((item) =>
            h(
              "li",
              null,
              h("span", { class: "item-kind" }, interventionTypeLabel(item.type)),
              h("span", { class: "item-name" }, item.name ?? "Unnamed"),
              item.other_names.length ? h("span", { class: "muted" }, ` (also called ${item.other_names.join(", ")})`) : null,
            ),
          ),
        )
      : h("p", { class: "muted" }, "No interventions listed."),
    trial.arm_groups.length
      ? [
          h("h3", null, "Groups (", term("arm", "arms"), ")"),
          h(
            "ul",
            { class: "items" },
            trial.arm_groups.map((arm) =>
              h(
                "li",
                null,
                h("span", { class: "item-kind" }, arm.type === "PLACEBO_COMPARATOR" ? term("placebo", armTypeLabel(arm.type)) : armTypeLabel(arm.type)),
                h("span", { class: "item-name" }, arm.label ?? "Unnamed group"),
                arm.intervention_names.length ? h("span", { class: "muted" }, ` — ${arm.intervention_names.join("; ")}`) : null,
              ),
            ),
          ),
        ]
      : null,
    trial.conditions.length
      ? [
          h("h3", null, term("condition", "Conditions"), " studied"),
          h("ul", { class: "tags" }, trial.conditions.map((condition) => h("li", null, condition))),
        ]
      : null,
    h("h3", null, term("primary-endpoint", "Primary endpoint")),
    trial.primary_outcomes.length
      ? h(
          "ul",
          { class: "items" },
          trial.primary_outcomes.map((outcome) =>
            h(
              "li",
              null,
              h("span", { class: "item-name" }, outcome.measure ?? "Not described"),
              outcome.time_frame ? h("span", { class: "muted" }, ` — measured: ${outcome.time_frame}`) : null,
            ),
          ),
        )
      : h("p", { class: "muted" }, "The registry record lists no primary endpoint."),
  );
}

function checklistGroup(items: ChecklistItem[], kind: "inclusion" | "exclusion"): Child[] {
  // Keep the registry's own sub-headings ("Main phase", "Extension phase").
  const groups: { name: string | undefined; items: ChecklistItem[] }[] = [];
  for (const item of items) {
    const last = groups[groups.length - 1];
    if (last && last.name === item.group) last.items.push(item);
    else groups.push({ name: item.group, items: [item] });
  }
  return groups.map((group) => [
    group.name ? h("h4", { class: "check-group" }, group.name) : null,
    h(
      "ul",
      { class: `checklist checklist-${kind}` },
      group.items.map((item) =>
        h(
          "li",
          null,
          h("span", { class: "check-mark", "aria-hidden": "true" }, kind === "inclusion" ? "✓" : "✕"),
          h(
            "div",
            null,
            item.text,
            item.sub_items?.length ? h("ul", { class: "sub-items" }, item.sub_items.map((sub) => h("li", null, sub))) : null,
          ),
        ),
      ),
    ),
  ]);
}

function eligibilitySection(trial: Trial): HTMLElement {
  const eligibility = trial.eligibility;
  const checklist: Checklist | undefined = trial.eligibility_checklist;
  const original = eligibility.criteria;
  return section(
    "eligibility",
    ["Who can join (", term("eligibility", "eligibility"), ")"],
    h(
      "dl",
      { class: "facts facts-compact" },
      fact("Age", ageRange(eligibility.minimum_age, eligibility.maximum_age)),
      fact("Sex", sexLabel(eligibility.sex)),
      fact(
        term("healthy-volunteers"),
        eligibility.healthy_volunteers === null ? "—" : eligibility.healthy_volunteers ? "Accepted" : "Not accepted",
      ),
    ),
    checklist?.parsed
      ? [
          checklist.notes.length ? checklist.notes.map((note) => h("p", { class: "registry-text" }, note)) : null,
          checklist.inclusion.length
            ? [h("h3", null, `You must meet these (${checklist.inclusion.length})`), checklistGroup(checklist.inclusion, "inclusion")]
            : null,
          checklist.exclusion.length
            ? [h("h3", null, `These rule you out (${checklist.exclusion.length})`), checklistGroup(checklist.exclusion, "exclusion")]
            : null,
          original
            ? h("details", { class: "original" }, h("summary", null, "Show the original registry text"), h("pre", { class: "registry-pre" }, original))
            : null,
        ]
      : original
        ? [
            h(
              "p",
              { class: "note" },
              "This record does not separate its criteria into clear inclusion and exclusion lists, so they are shown exactly as the registry has them.",
            ),
            h("pre", { class: "registry-pre" }, original),
          ]
        : h("p", { class: "muted" }, "The registry record lists no eligibility criteria."),
    h(
      "p",
      { class: "note" },
      "This checklist is sorted automatically from the registry text. Only the study team can decide whether someone qualifies.",
    ),
  );
}

function siteLine(site: Site): HTMLElement {
  const place = [site.city, site.state].filter(Boolean).join(", ") || "Location not given";
  return h(
    "li",
    { class: "site" },
    h("span", { class: "site-place" }, place),
    site.facility ? h("span", { class: "muted" }, ` · ${site.facility}`) : null,
    site.status === "RECRUITING"
      ? h("span", { class: "flag flag-recruiting" }, h("span", { class: "dot", "aria-hidden": "true" }), "Recruiting")
      : site.status
        ? h("span", { class: "flag" }, statusLabel(site.status))
        : null,
  );
}

function sitesSection(trial: Trial): HTMLElement {
  const sites = trial.sites;
  if (!sites.length) {
    return section("sites", ["Where (", term("site", "sites"), ")"], h("p", { class: "muted" }, "The registry record lists no sites."));
  }
  const byCountry = new Map<string, Site[]>();
  for (const site of sites) {
    const country = site.country ?? "Country not given";
    byCountry.set(country, [...(byCountry.get(country) ?? []), site]);
  }
  const recruiting = sites.filter((s) => s.status === "RECRUITING").length;
  const open = sites.length <= SITES_OPEN_LIMIT;
  const countries = [...byCountry.entries()].sort((a, b) => b[1].length - a[1].length || a[0].localeCompare(b[0]));
  return section(
    "sites",
    ["Where (", term("site", "sites"), ")"],
    h(
      "p",
      { class: "section-note" },
      `${plural(sites.length, "site")} in ${plural(byCountry.size, "country", "countries")}` +
        (recruiting ? `; ${formatInt(recruiting)} recruiting now.` : ". None are marked as recruiting now."),
    ),
    countries.map(([country, list]) => {
      const here = list.filter((s) => s.status === "RECRUITING").length;
      return h(
        "details",
        { class: "country", open: open || countries.length === 1 && list.length <= 40 },
        h(
          "summary",
          null,
          h("span", { class: "country-name" }, country),
          h("span", { class: "muted" }, ` ${plural(list.length, "site")}${here ? `, ${here} recruiting` : ""}`),
        ),
        h("ul", { class: "sites" }, list.map(siteLine)),
      );
    }),
  );
}

export async function trialPage(meta: Meta, nctId: string): Promise<Page> {
  const [trial, summary] = await Promise.all([loadTrial(nctId), loadSummary(nctId)]);
  const collections = trial.collections.flatMap((id) => meta.collections.find((c) => c.id === id) ?? []);
  const home = collections[0];
  const since = meta.tracking_since ?? meta.snapshot_date;
  const title = trial.brief_title ?? trial.nct_id;

  const node = h(
    "article",
    { class: "page trial", style: home ? `--accent:${home.accent}` : "" },
    h("p", { class: "crumb" }, home ? link(`/c/${home.id}`, null, `← ${home.name}`) : link("/", null, "← All collections")),
    h(
      "header",
      { class: "hero hero-accent" },
      h("p", { class: "eyebrow" }, "Clinical trial record"),
      h("h1", null, title),
      trial.official_title && trial.official_title !== title ? h("p", { class: "official-title" }, trial.official_title) : null,
      h(
        "p",
        { class: "trial-ids" },
        term("nct-number", "NCT number"),
        " ",
        external(trial.registry_url, { class: "nct" }, trial.nct_id),
        trial.acronym ? h("span", { class: "muted" }, ` · ${trial.acronym}`) : null,
      ),
      h(
        "p",
        { class: "chips" },
        statusChip(trial.overall_status),
        phaseChip(trial.phases),
        trial.has_results ? h("span", { class: "chip chip-plain" }, "Results posted") : null,
      ),
      collections.length ? h("p", { class: "change-collections" }, collections.map((c) => link(`/c/${c.id}`, { class: "tag-link" }, collectionTag(c)))) : null,
    ),
    trial.in_watchlist
      ? null
      : h(
          "p",
          { class: "note note-strong" },
          `This trial stopped matching our collections on ${formatDate(trial.left_watchlist_date)}. The details below are the last version we saved and are no longer updated.`,
        ),
    section("facts", "Key facts", keyFacts(trial)),
    summarySection(trial, summary),
    testedSection(trial),
    eligibilitySection(trial),
    sitesSection(trial),
    section(
      "history",
      "History of changes",
      trial.history.length
        ? [
            h("p", { class: "section-note" }, `Every change this site has recorded for this trial since ${formatDate(since)}, newest first.`),
            h("ul", { class: "changes" }, trial.history.map((entry) => changeItem({ change: entry }))),
          ]
        : emptyState(
            "No changes recorded",
            `This trial's registry record has not changed in any way we track since ${formatDate(since)}, when tracking started.`,
          ),
    ),
    h(
      "section",
      { class: "section record" },
      h("h2", null, "The official record"),
      h(
        "p",
        null,
        "Everything on this page comes from the trial's public record. ",
        external(trial.registry_url, null, `View ${trial.nct_id} on ClinicalTrials.gov`),
      ),
      h(
        "p",
        { class: "muted" },
        `First posted ${formatDate(trial.first_posted)} · last updated on the registry ${formatDate(trial.last_update_posted)} · our copy is from ${formatDate(meta.snapshot_date)}.`,
      ),
    ),
  );
  return { title: `${trial.nct_id} ${title}`, node };
}
