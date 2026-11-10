// App shell, "data as of" badge and router.

import "@fontsource-variable/source-serif-4/opsz.css";
import "@fontsource-variable/source-sans-3";
import "./styles.css";

import { APP_NAME, REPO_URL, type Page } from "./config";
import { clearCache, loadMetaFresh, NotFoundError } from "./data";
import { clear, external, h, link } from "./dom";
import { daysBetween, formatDate, todayInLosAngeles } from "./format";
import { closeGlossaryPopup, initGlossaryPopups } from "./glossary";
import { collectionPage } from "./pages/collection";
import { evalsPage } from "./pages/evals";
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
  [/^\/evals\/?$/, () => evalsPage()],
];

// --- shell -------------------------------------------------------------------

const main = h("main", { id: "main", tabindex: "-1" });
const badge = h("div", { class: "badge", role: "status" }, "Checking how fresh the data is…");
const announcer = h("div", { class: "sr-only", "aria-live": "polite" });

/** The mark: a delta in a square seal. */
function logo(): SVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const mark = document.createElementNS(ns, "svg");
  mark.setAttribute("viewBox", "0 0 32 32");
  mark.setAttribute("class", "logo-mark");
  mark.setAttribute("aria-hidden", "true");
  const seal = document.createElementNS(ns, "rect");
  seal.setAttribute("width", "32");
  seal.setAttribute("height", "32");
  seal.setAttribute("rx", "2");
  seal.setAttribute("class", "logo-seal");
  const delta = document.createElementNS(ns, "path");
  delta.setAttribute("d", "M16 7.5 24.5 23.5H7.5Z");
  delta.setAttribute("class", "logo-delta");
  mark.append(seal, delta);
  return mark;
}

const NAV: [string, string, RegExp][] = [
  ["/", "Collections", /^\/(c\/|t\/)?/],
  ["/evals", "AI evaluation", /^\/evals/],
  ["/glossary", "Glossary", /^\/glossary/],
];
const navLinks = NAV.map(([href, label]) => link(href, { class: "nav-link" }, label));

/** Mark the section the reader is in (trial and collection pages belong to Collections). */
function updateNav(path: string): void {
  const active = NAV.findIndex(([, , pattern], i) => i > 0 && pattern.test(path));
  navLinks.forEach((a, i) => {
    if (i === (active === -1 ? 0 : active)) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
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
        { class: "wrap masthead" },
        link(
          "/",
          { class: "brand", "aria-label": `${APP_NAME} home` },
          logo(),
          h(
            "span",
            { class: "brand-text" },
            h("span", { class: "brand-name" }, APP_NAME),
            h("span", { class: "brand-tag" }, "Daily record of clinical trial registry changes"),
          ),
        ),
        h("nav", { "aria-label": "Site", class: "nav" }, navLinks),
      ),
      h(
        "div",
        { class: "dateline" },
        h(
          "div",
          { class: "wrap dateline-row" },
          badge,
          h("p", { class: "dateline-source" }, "Source: ClinicalTrials.gov registry, copied daily"),
        ),
      ),
    ),
    h("div", { class: "wrap" }, main),
    h(
      "footer",
      { class: "site-footer" },
      h(
        "div",
        { class: "wrap footer-grid" },
        h(
          "div",
          { class: "footer-about" },
          h("p", { class: "footer-name" }, APP_NAME),
          h(
            "p",
            null,
            "An independent, open-source record of how clinical trial registrations change over time, built from public data.",
          ),
        ),
        h(
          "div",
          null,
          h("h2", { class: "footer-head" }, "Data"),
          h(
            "ul",
            { class: "footer-list" },
            h("li", null, "Registry records from ", external("https://clinicaltrials.gov/", null, "ClinicalTrials.gov")),
            h("li", null, "Copied once a day; every change is kept"),
            h("li", null, external(REPO_URL, null, "Source code and data")),
          ),
        ),
        h(
          "div",
          null,
          h("h2", { class: "footer-head" }, "Notice"),
          h(
            "p",
            null,
            "This site gives information and links only. It is not medical advice; talk to a doctor before making any health decision.",
          ),
        ),
      ),
      h(
        "div",
        { class: "wrap footer-legal" },
        h(
          "p",
          null,
          `${APP_NAME} is an independent project and is not affiliated with ClinicalTrials.gov or the U.S. National Library of Medicine.`,
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
    h("span", { class: "badge-mark", "aria-hidden": "true" }),
    h("span", { class: "badge-label" }, "Data as of "),
    h("time", { datetime: current.snapshot_date }, formatDate(current.snapshot_date)),
    h(
      "span",
      { class: "badge-age" },
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
  updateNav(path);
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
