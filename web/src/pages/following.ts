// Following: the trials a visitor follows in this browser, with what changed in
// each one's registry record since they last looked. The list lives only in
// this browser (see follow.ts); the trial files are the same ones every page reads.

import type { Page } from "../config";
import { loadTrial } from "../data";
import { clear, h, link } from "../dom";
import { formatDate, plural } from "../format";
import { canFollow, followedTrials, markSeen, onFollowChange, unfollow } from "../follow";
import { term } from "../glossary";
import type { HistoryEntry, Meta, Trial } from "../types";
import { changeItem, emptyState, phaseChip, section, statusChip } from "../ui";

const CHANGES_SHOWN = 5;

interface Row {
  nctId: string;
  title: string | null;
  seen: string;
  since: string;
  trial: Trial | null;
  fresh: HistoryEntry[];
}

function feedsList(meta: Meta): HTMLElement {
  return h(
    "ul",
    { class: "feed-list" },
    h("li", null, h("a", { href: "/data/feeds/all.xml", type: "application/rss+xml" }, "All changes")),
    meta.collections.map((c) =>
      h("li", null, h("a", { href: `/data/feeds/${c.id}.xml`, type: "application/rss+xml" }, c.name)),
    ),
  );
}

/** The list re-renders itself on every change to it (see onFollowChange below). */
function trialCard(row: Row, meta: Meta): HTMLElement {
  const { trial } = row;
  const actions = h(
    "p",
    { class: "follow-actions" },
    row.fresh.length
      ? h(
          "button",
          {
            type: "button",
            class: "button button-quiet",
            onclick: () => markSeen([row.nctId], meta.snapshot_date),
          },
          "Mark as seen",
        )
      : null,
    h(
      "button",
      {
        type: "button",
        class: "link-button",
        onclick: () => unfollow(row.nctId),
      },
      "Stop following",
    ),
  );
  if (!trial) {
    return h(
      "li",
      { class: "follow-item" },
      h("h3", { class: "find-title" }, row.title ?? row.nctId),
      h("p", { class: "note" }, `${row.nctId} could not be loaded. It may no longer be in any collection this site tracks.`),
      actions,
    );
  }
  const status = row.fresh.length
    ? h(
        "p",
        { class: "follow-new" },
        `${plural(row.fresh.length, "change")} since you last looked (${formatDate(row.seen)})`,
      )
    : h("p", { class: "follow-quiet" }, `No changes since ${formatDate(row.seen)}.`);
  return h(
    "li",
    { class: `follow-item${row.fresh.length ? " follow-item-new" : ""}` },
    h("h3", { class: "find-title" }, link(`/t/${trial.nct_id}`, { class: "row-title" }, trial.brief_title ?? trial.nct_id)),
    h(
      "p",
      { class: "row-meta" },
      statusChip(trial.overall_status),
      phaseChip(trial.phases),
      h("span", { class: "row-sub" }, trial.nct_id, ` · following since ${formatDate(row.since)}`),
    ),
    trial.in_watchlist
      ? null
      : h(
          "p",
          { class: "note" },
          `This trial stopped matching this site's collections on ${formatDate(trial.left_watchlist_date)}, so changes after that are not tracked here.`,
        ),
    status,
    row.fresh.length
      ? h(
          "ul",
          { class: "changes changes-compact" },
          row.fresh.slice(0, CHANGES_SHOWN).map((entry) => changeItem({ change: entry })),
        )
      : null,
    row.fresh.length > CHANGES_SHOWN
      ? h(
          "p",
          { class: "note" },
          `And ${plural(row.fresh.length - CHANGES_SHOWN, "earlier change")} on the `,
          link(`/t/${trial.nct_id}#history`, null, "trial page"),
          ".",
        )
      : null,
    actions,
  );
}

export async function followingPage(meta: Meta): Promise<Page> {
  const list = h("div", { class: "follow-list-wrap" });
  const summary = h("p", { class: "find-count", "aria-live": "polite" });

  let latest = 0;
  const render = async () => {
    const token = ++latest;
    const store = followedTrials();
    const ids = Object.keys(store);
    clear(list);
    clear(summary);
    if (!canFollow()) {
      list.appendChild(
        emptyState(
          "This browser is not keeping a list",
          "It blocks this site from saving anything, for example in a private window. Following needs that, because the list is kept only on this device. ",
          "The RSS feeds below work in any feed reader instead.",
        ),
      );
      return;
    }
    if (!ids.length) {
      list.appendChild(
        emptyState(
          "You are not following any trials yet",
          "Select “Follow” on a trial's page, or on a result in ",
          link("/find", null, "Find a trial"),
          ". The trial is saved in this browser only, and this page then shows anything that changes in its registry record.",
        ),
      );
      return;
    }
    list.appendChild(h("p", { class: "loading", role: "status" }, `Loading ${plural(ids.length, "trial")}…`));
    const loaded = await Promise.allSettled(ids.map((id) => loadTrial(id)));
    if (token !== latest) return; // the list changed while loading; a newer render owns the page
    const rows: Row[] = ids.map((nctId, i) => {
      const entry = store[nctId] as (typeof store)[string];
      const result = loaded[i];
      const trial = result?.status === "fulfilled" ? result.value : null;
      const fresh = (trial?.history ?? []).filter((change) => change.detected_date > entry.seen);
      return { nctId, title: entry.title, seen: entry.seen, since: entry.since, trial, fresh };
    });
    rows.sort(
      (a, b) =>
        Number(b.fresh.length > 0) - Number(a.fresh.length > 0) ||
        (a.trial?.brief_title ?? a.title ?? a.nctId).localeCompare(b.trial?.brief_title ?? b.title ?? b.nctId),
    );
    const withNews = rows.filter((row) => row.fresh.length);
    clear(list);
    summary.append(
      `${plural(rows.length, "trial")} followed. `,
      withNews.length
        ? `${plural(withNews.length, "trial has", "trials have")} changed since you last looked.`
        : "Nothing has changed since you last looked.",
    );
    if (withNews.length > 1) {
      summary.append(
        " ",
        h(
          "button",
          {
            type: "button",
            class: "link-button",
            onclick: () =>
              markSeen(
                withNews.map((row) => row.nctId),
                meta.snapshot_date,
              ),
          },
          "Mark all as seen",
        ),
      );
    }
    list.appendChild(h("ul", { class: "follow-list" }, rows.map((row) => trialCard(row, meta))));
  };

  // Re-render on any change to the list: a button here, or another tab.
  const stop = onFollowChange(() => {
    if (list.isConnected) void render();
    else stop();
  });
  void render();

  const node = h(
    "div",
    { class: "page following-page" },
    h(
      "header",
      { class: "hero hero-accent" },
      h("p", { class: "eyebrow" }, "Your list"),
      h("h1", null, "Trials you follow"),
      h(
        "p",
        { class: "lede" },
        "See what changed in each trial's registry record since you last looked: its ",
        term("status", "status"),
        ", ",
        term("site", "sites"),
        ", dates, ",
        term("enrollment", "enrollment"),
        " and results.",
      ),
    ),
    h(
      "aside",
      { class: "find-notice", "aria-label": "About this list" },
      h(
        "p",
        null,
        h("strong", null, "Saved in this browser only. "),
        "Nothing about which trials you follow is sent anywhere. Clearing this site's data in your browser, or using another device, starts an empty list.",
      ),
    ),
    summary,
    list,
    section(
      "feeds",
      "Get changes in a feed reader",
      h(
        "p",
        { class: "section-note" },
        "These RSS feeds list the latest 50 changes and update after each daily refresh. Add a link to any feed reader to be told about changes without visiting.",
      ),
      feedsList(meta),
    ),
  );
  return { title: "Trials you follow", node };
}
