// Drug pages: every tracked trial that lists a drug among its treatments, by its
// generic name, a brand name or a sponsor code (pipeline/writers.py drug_matcher).
// A drug file has a collection's shape, so the landscape charts and the trial
// table are the collection page's own.

import type { Page } from "../config";
import { loadChanges, loadDrug, loadDrugIndex } from "../data";
import { h, link } from "../dom";
import { formatDate, formatInt, plural, shiftDate } from "../format";
import { term } from "../glossary";
import type { CollectionSummary, DrugEntry, Meta, Trial } from "../types";
import { changeItem, collectionTag, emptyState, section, stat } from "../ui";
import { landscape, trialTable } from "./collection";
import { latestChanges } from "./home";

const CHANGES_SHOWN = 20;

/** Whole-word pattern for a drug term; spaces and hyphens match either, as in the pipeline. */
function termPattern(term: string): string {
  const words = term
    .trim()
    .split(/[\s-]+/)
    .filter(Boolean)
    .map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  return `(?<![\\w-])${words.join("[\\s-]?")}(?!\\w)`;
}

/**
 * The drugs with a page that this trial tests, matched like the pipeline's
 * drug_matcher: intervention names and other names, placebo left out. The
 * index carries each drug's name and other names, which cover its terms.
 */
export function drugsIn(trial: Pick<Trial, "interventions">, drugs: DrugEntry[]): DrugEntry[] {
  const text = trial.interventions
    .filter((iv) => !/\b(placebo|sham)\b/i.test(iv.name ?? ""))
    .map((iv) => [iv.name ?? "", ...iv.other_names].join(" | "))
    .join(" || ");
  if (!text) return [];
  return drugs.filter((drug) =>
    new RegExp([drug.name, ...drug.also_listed_as].map(termPattern).join("|"), "i").test(text),
  );
}

function method(): HTMLElement {
  return h(
    "details",
    { class: "definition" },
    h("summary", null, "How these lists are built"),
    h(
      "p",
      null,
      "A trial is listed for a drug when one of the treatments its registry record lists (the ",
      term("intervention", "interventions"),
      ") names the drug: by its generic name, a brand name or the sponsor's code for it. A placebo made to look like the drug does not count, and neither does a mention only in the title or description.",
    ),
    h(
      "p",
      null,
      "Only trials in this site's three collections are counted. Older drugs that appear in hundreds of trials as standard care, such as metformin and statins, have no page; search for them in ",
      link("/find", null, "Find a trial"),
      " or a collection's table instead.",
    ),
  );
}

function counts(drug: DrugEntry): string {
  const open = [
    drug.recruiting_count ? `${formatInt(drug.recruiting_count)} recruiting` : "",
    drug.not_yet_recruiting_count ? `${formatInt(drug.not_yet_recruiting_count)} not yet recruiting` : "",
  ].filter(Boolean);
  return `${plural(drug.trial_count, "trial")}${open.length ? ` · ${open.join(" · ")}` : ""}`;
}

export async function drugsPage(meta: Meta): Promise<Page> {
  const index = await loadDrugIndex();
  const byCollection = new Map<string, DrugEntry[]>();
  for (const drug of index.drugs) {
    const home = drug.collections[0] ?? "";
    byCollection.set(home, [...(byCollection.get(home) ?? []), drug]);
  }
  const groups: { collection: CollectionSummary | null; drugs: DrugEntry[] }[] = [
    ...meta.collections.map((c) => ({ collection: c, drugs: byCollection.get(c.id) ?? [] })),
    { collection: null, drugs: byCollection.get("") ?? [] },
  ].filter((group) => group.drugs.length);

  const node = h(
    "div",
    { class: "page drugs-page" },
    h(
      "header",
      { class: "hero hero-accent" },
      h("p", { class: "eyebrow" }, "Reference"),
      h("h1", null, "Drugs"),
      h(
        "p",
        { class: "lede" },
        `Every tracked trial of ${index.drugs.length} drugs, with how many are recruiting, how they divide by `,
        term("phase", "phase"),
        " and what changed recently. Each drug is listed under the area where most of its trials are.",
      ),
    ),
    groups.map(({ collection, drugs }) =>
      section(
        `drugs-${collection?.id ?? "other"}`,
        collection?.name ?? "Not in a collection yet",
        h(
          "ul",
          { class: "drug-list", style: collection ? `--accent:${collection.accent}` : null },
          drugs.map((drug) =>
            h(
              "li",
              { class: "drug-item" },
              link(`/d/${drug.id}`, { class: "drug-name" }, drug.name),
              h("span", { class: "drug-class" }, drug.drug_class),
              h("span", { class: "drug-counts" }, counts(drug)),
            ),
          ),
        ),
      ),
    ),
    method(),
    h("p", { class: "note" }, `Data as of ${formatDate(index.snapshot_date)}.`),
  );
  return { title: "Drugs", node };
}

export async function drugPage(meta: Meta, id: string): Promise<Page> {
  const [drug, changes] = await Promise.all([loadDrug(id), loadChanges()]);
  const ids = new Set(drug.trials.map((t) => t.nct_id));
  const windowStart = shiftDate(meta.snapshot_date, -(meta.recent_window_days - 1));
  const recent = latestChanges(
    changes.events.filter((e) => ids.has(e.nct_id) && e.detected_date >= windowStart),
    Number.MAX_SAFE_INTEGER,
  );
  const collections = drug.collections.flatMap((cid) => meta.collections.find((c) => c.id === cid) ?? []);
  const open = drug.recruiting_count + drug.not_yet_recruiting_count;
  const since = meta.tracking_since ?? meta.snapshot_date;

  const node = h(
    "div",
    { class: "page drug-page", style: drug.accent ? `--accent:${drug.accent}` : null },
    h("p", { class: "crumb" }, link("/drugs", null, "← All drugs")),
    h(
      "header",
      { class: "hero hero-accent" },
      h("p", { class: "eyebrow" }, `Drug · ${drug.drug_class}`),
      h("h1", null, drug.name),
      drug.also_listed_as.length
        ? h("p", { class: "lede" }, "Also listed in trials as ", drug.also_listed_as.join(", "), ".")
        : null,
      collections.length
        ? h("p", { class: "change-collections" }, collections.map((c) => link(`/c/${c.id}`, { class: "tag-link" }, collectionTag(c))))
        : null,
    ),
    h(
      "div",
      { class: "stats stats-wide" },
      stat(formatInt(drug.trial_count), term("clinical-trial", "trials")),
      stat(formatInt(drug.recruiting_count), term("status-recruiting", "recruiting now")),
      stat(formatInt(drug.not_yet_recruiting_count), term("status-not_yet_recruiting", "not yet recruiting")),
      stat(formatInt(recent.length), [term("change", "changes"), ` in ${meta.recent_window_days} days`]),
    ),
    open
      ? h(
          "aside",
          { class: "find-cta", "aria-label": `Find a ${drug.name} trial` },
          h(
            "div",
            { class: "find-cta-text" },
            h("p", { class: "find-cta-title" }, `${drug.name} trials near you`),
            h(
              "p",
              null,
              `Search the ${plural(open, "trial")} of ${drug.name.toLowerCase()} that ${open === 1 ? "is" : "are"} recruiting or about to, by where you live and your age.`,
            ),
          ),
          link(`/find?q=${encodeURIComponent(drug.name.toLowerCase())}`, { class: "button button-primary" }, "Find a trial"),
        )
      : null,
    drug.trial_count
      ? section("landscape", "Landscape", landscape(drug))
      : emptyState("No tracked trials", `No trial in this site's collections lists ${drug.name.toLowerCase()} as a treatment right now.`),
    section(
      "changes",
      `Changed in the last ${meta.recent_window_days} days`,
      recent.length
        ? [
            h(
              "ul",
              { class: "changes" },
              recent
                .slice(0, CHANGES_SHOWN)
                .map(({ event }) => changeItem({ change: event, trial: { nctId: event.nct_id, title: event.brief_title } })),
            ),
            recent.length > CHANGES_SHOWN
              ? h("p", { class: "note" }, `Showing the ${CHANGES_SHOWN} most recent of ${formatInt(recent.length)}.`)
              : null,
          ]
        : emptyState(
            "No changes in this period",
            windowStart <= since
              ? `Tracking started on ${formatDate(since)}. None of these trials has changed since then.`
              : `None of these trials changed between ${formatDate(windowStart)} and ${formatDate(meta.snapshot_date)}.`,
          ),
    ),
    drug.trial_count ? section("trials", "All trials", trialTable(drug)) : null,
    method(),
  );
  return { title: drug.name, node };
}
