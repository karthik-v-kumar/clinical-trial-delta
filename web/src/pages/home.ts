// Home: the collections as cards, then the latest changes across all of them.

import { APP_NAME, type Page } from "../config";
import { loadChanges } from "../data";
import { h, link } from "../dom";
import { formatDate, formatInt, plural } from "../format";
import { term } from "../glossary";
import type { ChangeEvent, CollectionSummary, Meta } from "../types";
import { changeItem, emptyState, section, stat } from "../ui";

const FEED_LENGTH = 20;
const COUNT_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"];
const countWord = (n: number) => COUNT_WORDS[n] ?? formatInt(n);

/** Newest first, one entry per registry change (a trial in two collections appears once). */
export function latestChanges(events: ChangeEvent[], limit: number): { event: ChangeEvent; collections: string[] }[] {
  const byChange = new Map<string, { event: ChangeEvent; collections: string[] }>();
  for (let i = events.length - 1; i >= 0; i--) {
    const event = events[i];
    if (!event) continue;
    const seen = byChange.get(event.change_id);
    if (seen) seen.collections.unshift(event.collection);
    else byChange.set(event.change_id, { event, collections: [event.collection] });
  }
  return [...byChange.values()]
    .sort((a, b) => b.event.detected_date.localeCompare(a.event.detected_date))
    .slice(0, limit);
}

function collectionCard(collection: CollectionSummary, windowDays: number): HTMLElement {
  return h(
    "li",
    { class: "card collection-card", style: `--accent:${collection.accent}` },
    h("h3", null, link(`/c/${collection.id}`, { class: "card-link" }, collection.name)),
    h("p", { class: "card-why" }, collection.why_it_matters),
    h(
      "div",
      { class: "stats" },
      stat(formatInt(collection.trial_count), term("clinical-trial", "trials")),
      stat(formatInt(collection.recruiting_count), term("status-recruiting", "recruiting now")),
      stat(formatInt(collection.changes_recent), [term("change", "changes"), ` in ${windowDays} days`]),
    ),
    collection.partial ? h("p", { class: "note" }, "Part of this collection could not be refreshed on the last run.") : null,
  );
}

export async function homePage(meta: Meta): Promise<Page> {
  const changes = await loadChanges();
  const byId = new Map(meta.collections.map((c) => [c.id, c]));
  const feed = latestChanges(changes.events, FEED_LENGTH);
  const since = meta.tracking_since ?? meta.snapshot_date;

  const node = h(
    "div",
    { class: "page" },
    h(
      "div",
      { class: "hero" },
      h("p", { class: "eyebrow" }, "Independent registry monitor · Updated daily"),
      h("h1", null, "Version history for the clinical trial registry"),
      h(
        "p",
        { class: "lede" },
        "ClinicalTrials.gov publishes only the current version of each study. ",
        `${APP_NAME} keeps a daily copy of ${formatInt(meta.totals.trials)} `,
        term("study-type", "interventional trials"),
        ` across ${countWord(meta.collections.length)} therapeutic areas and records every change to their `,
        term("status", "status"),
        ", ",
        term("enrollment", "enrollment"),
        ", ",
        term("primary-endpoint", "endpoints"),
        ", dates, ",
        term("site", "sites"),
        " and posted results.",
      ),
      h(
        "ul",
        { class: "hero-facts" },
        h("li", null, "Recorded daily since ", h("strong", null, formatDate(since))),
        h(
          "li",
          null,
          h("strong", null, formatInt(meta.totals.changes_all_time)),
          meta.totals.changes_all_time === 1 ? " change documented" : " changes documented",
        ),
        h("li", null, link("/evals", null, "Every AI summary fact-checked against the record")),
      ),
    ),
    section(
      "collections",
      "Collections",
      h("ul", { class: "cards" }, meta.collections.map((c) => collectionCard(c, meta.recent_window_days))),
      h(
        "aside",
        { class: "find-cta", "aria-label": "Find a trial" },
        h(
          "div",
          { class: "find-cta-text" },
          h("p", { class: "find-cta-title" }, "Looking for a trial to join?"),
          h(
            "p",
            null,
            "Search the trials in these areas that are recruiting now, by where you live, your age and what is being tested.",
          ),
        ),
        link("/find", { class: "button button-primary" }, "Find a trial near you"),
      ),
    ),
    section(
      "changes",
      "Latest changes",
      feed.length
        ? [
            h(
              "p",
              { class: "section-note" },
              `The ${plural(feed.length, "most recent change")} across all collections, out of ${formatInt(meta.totals.changes_all_time)} recorded since ${formatDate(since)}.`,
            ),
            h(
              "ul",
              { class: "changes" },
              feed.map(({ event, collections }) =>
                changeItem({
                  change: event,
                  trial: { nctId: event.nct_id, title: event.brief_title },
                  collections: collections.flatMap((id) => byId.get(id) ?? []),
                }),
              ),
            ),
          ]
        : emptyState(
            "No changes recorded yet",
            `Tracking started on ${formatDate(since)} with ${formatInt(meta.totals.trials)} trials. Each day's copy of the registry is compared with the one before, and anything that changed will be listed here.`,
          ),
    ),
  );
  return { title: APP_NAME, node };
}
