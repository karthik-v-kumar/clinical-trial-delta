// App shell, "data as of" badge and router.

import "./styles.css";

import { APP_NAME, REPO_URL, type Page } from "./config";
import { clearCache, loadMetaFresh, NotFoundError } from "./data";
import { clear, external, h, link } from "./dom";
import { daysBetween, formatDate, todayInLosAngeles } from "./format";
import { closeGlossaryPopup, initGlossaryPopups } from "./glossary";
import { collectionPage } from "./pages/collection";
import { glossaryPage } from "./pages/glossary";
import { homePage } from "./pages/home";
import { trialPage } from "./pages/trial";
import type { Meta } from "./types";
import { errorBox, loading } from "./ui";

type Handler = (meta: Meta, params: string[]) => Promise<Page> | Page;

const routes: [RegExp, Handler][] = [
  [/^\/$/, (meta) => homePage(meta)],
  [/^\/c\/([a-z0-9-]+)\/?$/, (meta, [id]) => collectionPage(meta, id ?? "")],
  [/^\/t\/(NCT\d{8})\/?$/i, (meta, [nct]) => trialPage(meta, (nct ?? "").toUpperCase())],
  [/^\/glossary\/?$/, () => glossaryPage()],
];

// --- shell -------------------------------------------------------------------

const main = h("main", { id: "main", tabindex: "-1" });
const badge = h("div", { class: "badge", role: "status" }, "Checking how fresh the data is…");
const announcer = h("div", { class: "sr-only", "aria-live": "polite" });

function logo(): SVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const mark = document.createElementNS(ns, "svg");
  mark.setAttribute("viewBox", "0 0 24 24");
  mark.setAttribute("class", "logo-mark");
  mark.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", "M12 3.5 21 20.5H3L12 3.5Zm0 5.2L7.6 17.5h8.8L12 8.7Z");
  mark.appendChild(path);
  return mark;
}

function shell(): HTMLElement {
  return h(
    "div",
    { class: "app" },
    h("a", { class: "skip", href: "#main" }, "Skip to content"),
    h(
      "header",
      { class: "site-header" },
      h(
        "div",
        { class: "wrap header-row" },
        link("/", { class: "brand", "aria-label": `${APP_NAME} home` }, logo(), h("span", null, APP_NAME)),
        h("nav", { "aria-label": "Site" }, link("/glossary", { class: "nav-link" }, "Glossary")),
      ),
      h("div", { class: "wrap" }, badge),
    ),
    h("div", { class: "wrap" }, main),
    h(
      "footer",
      { class: "site-footer" },
      h(
        "div",
        { class: "wrap" },
        h(
          "p",
          null,
          "Public data from ",
          external("https://clinicaltrials.gov/", null, "ClinicalTrials.gov"),
          ", copied once a day. This site gives information and links only. It is not medical advice; talk to a doctor before making any health decision.",
        ),
        h(
          "p",
          null,
          `${APP_NAME} is an independent project and is not affiliated with ClinicalTrials.gov or the U.S. National Library of Medicine. `,
          external(REPO_URL, null, "Source code and data"),
        ),
      ),
    ),
    announcer,
  );
}

// --- "data as of" badge --------------------------------------------------------

let meta: Meta | null = null;
let lastCheck = 0;

function renderBadge(current: Meta | null, failed = false): void {
  clear(badge);
  badge.className = "badge";
  if (!current) {
    badge.classList.add("badge-warn");
    badge.append(failed ? "Could not check how fresh the data is. Try reloading the page." : "Checking how fresh the data is…");
    return;
  }
  const age = daysBetween(current.snapshot_date, todayInLosAngeles());
  const stale = age >= 2;
  const partial = Boolean(current.run?.partial);
  if (stale || partial) badge.classList.add("badge-warn");
  const ageText = age <= 0 ? "updated today" : age === 1 ? "updated yesterday" : `${age} days old`;
  badge.append(
    h("span", { class: "badge-dot", "aria-hidden": "true" }),
    h("strong", null, `Data as of ${formatDate(current.snapshot_date)}`),
    h(
      "span",
      null,
      ` · ${ageText}`,
      stale ? " · the daily refresh may have missed a run" : "",
      partial ? " · some searches could not be refreshed this run" : "",
    ),
  );
}

/** Ask the server for meta.json; re-render the page if the data changed underneath us. */
async function refreshMeta(rerender: boolean): Promise<void> {
  lastCheck = Date.now();
  try {
    const fresh = await loadMetaFresh();
    const changed = meta !== null && (meta.snapshot_date !== fresh.snapshot_date || meta.generated_at !== fresh.generated_at);
    meta = fresh;
    renderBadge(meta);
    if (changed && rerender) {
      clearCache();
      void render(false);
    }
  } catch {
    renderBadge(meta, true);
  }
}

// --- router --------------------------------------------------------------------

let renderToken = 0;

async function render(moveFocus: boolean, restoreScroll: number | null = null): Promise<void> {
  const token = ++renderToken;
  closeGlossaryPopup();
  const path = location.pathname;
  clear(main);
  main.appendChild(loading("page"));
  let page: Page;
  try {
    if (!meta) meta = await loadMetaFresh();
    const match = routes.map(([pattern, handler]) => [pattern.exec(path), handler] as const).find(([m]) => m);
    if (!match || !match[0]) throw new NotFoundError(path);
    page = await match[1](meta, match[0].slice(1));
  } catch (error) {
    page =
      error instanceof NotFoundError
        ? { title: "Page not found", node: errorBox("There is nothing at this address. The trial may not be in any collection we track.") }
        : { title: "Something went wrong", node: errorBox(error instanceof Error ? error.message : "Unknown error") };
  }
  if (token !== renderToken) return; // a newer navigation won
  clear(main);
  main.appendChild(page.node);
  document.title = page.title === APP_NAME ? APP_NAME : `${page.title} · ${APP_NAME}`;
  announcer.textContent = page.title;
  if (location.hash) {
    document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView();
  } else {
    window.scrollTo(0, restoreScroll ?? 0);
  }
  if (moveFocus) main.focus({ preventScroll: true });
}

function saveScroll(): void {
  history.replaceState({ ...(history.state ?? {}), scroll: window.scrollY }, "");
}

export function navigate(url: string): void {
  saveScroll();
  history.pushState({}, "", url);
  void render(true);
}

function onLinkClick(event: MouseEvent): void {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  const anchor = (event.target as HTMLElement).closest<HTMLAnchorElement>("a[href]");
  if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
  const url = new URL(anchor.href, location.href);
  if (url.origin !== location.origin || url.pathname.startsWith("/data/")) return;
  if (url.pathname === location.pathname && url.hash) return; // in-page anchor: let the browser scroll
  event.preventDefault();
  navigate(url.pathname + url.search + url.hash);
}

function boot(): void {
  const root = document.getElementById("app");
  if (!root) return;
  clear(root);
  root.appendChild(shell());
  history.scrollRestoration = "manual";
  initGlossaryPopups();
  document.addEventListener("click", onLinkClick);
  window.addEventListener("popstate", (event) => {
    const state = event.state as { scroll?: number } | null;
    void render(true, state?.scroll ?? 0);
  });
  // Refresh-on-open: check the data's date when the page loads and whenever the
  // tab comes back into view after a while.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && Date.now() - lastCheck > 5 * 60_000) void refreshMeta(true);
  });
  void refreshMeta(false).then(() => render(false));
}

boot();
