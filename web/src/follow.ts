// Trials a visitor follows, kept in this browser only (localStorage). Nothing is
// sent anywhere. Each entry remembers the data date the visitor last looked at
// the trial, so the Following page can show what changed since then.
//
// Storage can be missing or throw (private windows, blocked site data); every
// access is wrapped, and the site works without it: following just isn't kept.

import { h } from "./dom";

const KEY = "ctd.following.v1";

export interface Followed {
  title: string | null;
  /** Data date (YYYY-MM-DD) when the visitor started following. */
  since: string;
  /** Data date the visitor last marked as seen; changes detected after it are new. */
  seen: string;
}

type Store = Record<string, Followed>;

const listeners = new Set<() => void>();

function isFollowed(value: unknown): value is Followed {
  const v = value as Followed;
  return Boolean(v) && typeof v.since === "string" && typeof v.seen === "string";
}

function read(): Store {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    if (!parsed || typeof parsed !== "object") return {};
    const store: Store = {};
    for (const [nct, entry] of Object.entries(parsed as Record<string, unknown>)) {
      if (/^NCT\d{8}$/.test(nct) && isFollowed(entry)) store[nct] = entry;
    }
    return store;
  } catch {
    return {};
  }
}

function write(store: Store): boolean {
  try {
    localStorage.setItem(KEY, JSON.stringify(store));
  } catch {
    return false;
  }
  for (const listener of listeners) listener();
  return true;
}

/** Whether this browser can keep the list at all. */
export function canFollow(): boolean {
  try {
    const probe = `${KEY}.probe`;
    localStorage.setItem(probe, "1");
    localStorage.removeItem(probe);
    return true;
  } catch {
    return false;
  }
}

export function followedTrials(): Store {
  return read();
}

export function followCount(): number {
  return Object.keys(read()).length;
}

export function isFollowing(nctId: string): boolean {
  return nctId in read();
}

export function follow(nctId: string, title: string | null, dataDate: string): boolean {
  const store = read();
  store[nctId] = { title, since: dataDate, seen: dataDate };
  return write(store);
}

export function unfollow(nctId: string): void {
  const store = read();
  delete store[nctId];
  write(store);
}

export function markSeen(nctIds: string[], dataDate: string): void {
  const store = read();
  for (const nct of nctIds) {
    const entry = store[nct];
    if (entry) entry.seen = dataDate;
  }
  write(store);
}

/** Call `listener` whenever the list changes, in this tab or another. Returns an unsubscribe function. */
export function onFollowChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

window.addEventListener("storage", (event) => {
  if (event.key === KEY || event.key === null) for (const listener of listeners) listener();
});

/** A toggle button: "Follow" / "Following". */
export function followButton(nctId: string, title: string | null, dataDate: string, extraClass = ""): HTMLElement {
  if (!canFollow()) return h("span", { hidden: true });
  const button = h("button", { type: "button", class: `button follow-button ${extraClass}`.trim() });
  const render = () => {
    const on = isFollowing(nctId);
    button.setAttribute("aria-pressed", String(on));
    button.textContent = on ? "Following" : "Follow";
    button.title = on
      ? "Saved in this browser. Select to stop following."
      : "Save this trial in this browser and see what changes";
  };
  button.addEventListener("click", () => {
    if (isFollowing(nctId)) unfollow(nctId);
    else follow(nctId, title, dataDate);
  });
  const stop = onFollowChange(() => {
    if (!button.isConnected) stop();
    else render();
  });
  render();
  return button;
}
