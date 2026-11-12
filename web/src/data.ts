// The only place the site talks to the network. Every request is a same-origin
// GET for a committed JSON file under /data/. Nothing here (or anywhere in the
// app) calls an LLM, an API or a third-party host at view time;
// scripts/check-fetch-targets.mjs enforces that in CI.

import type {
  AISummary,
  ChangesFile,
  Collection,
  EvalHistory,
  EvalReport,
  FinderIndex,
  Meta,
  SitesIndex,
  SummaryIndex,
  Trial,
} from "./types";

const DATA_ROOT = "/data/";

export class NotFoundError extends Error {}

const cache = new Map<string, Promise<unknown>>();

async function loadJson<T>(path: string, fresh = false): Promise<T> {
  const response = await fetch(DATA_ROOT + path, fresh ? { cache: "no-cache" } : undefined);
  const type = response.headers.get("content-type") ?? "";
  // A missing file is a 404 in production; the dev server answers with the app
  // shell (HTML) instead. Both mean "not found".
  if (response.status === 404 || (response.ok && !type.includes("json"))) {
    throw new NotFoundError(path);
  }
  if (!response.ok) throw new Error(`Could not load ${path} (HTTP ${response.status})`);
  return (await response.json()) as T;
}

function cached<T>(path: string): Promise<T> {
  let hit = cache.get(path) as Promise<T> | undefined;
  if (!hit) {
    hit = loadJson<T>(path);
    cache.set(path, hit);
    hit.catch(() => cache.delete(path));
  }
  return hit;
}

/** Always asks the server, so the "data as of" badge reflects the latest refresh. */
export function loadMetaFresh(): Promise<Meta> {
  const fresh = loadJson<Meta>("meta.json", true);
  cache.set("meta.json", fresh);
  fresh.catch(() => cache.delete("meta.json"));
  return fresh;
}

export const loadMeta = (): Promise<Meta> => cached<Meta>("meta.json");
export const loadChanges = (): Promise<ChangesFile> => cached<ChangesFile>("changes.json");
export const loadCollection = (id: string): Promise<Collection> =>
  cached<Collection>(`collections/${encodeURIComponent(id)}.json`);
export const loadSites = (id: string): Promise<SitesIndex> =>
  cached<SitesIndex>(`sites/${encodeURIComponent(id)}.json`);
export const loadFinder = (): Promise<FinderIndex> => cached<FinderIndex>("finder.json");
export const loadTrial = (nctId: string): Promise<Trial> =>
  cached<Trial>(`trials/${encodeURIComponent(nctId)}.json`);

/** A trial's AI summary, or null if none has been written yet. */
export async function loadSummary(nctId: string): Promise<AISummary | null> {
  try {
    return await cached<AISummary>(`summaries/${encodeURIComponent(nctId)}.json`);
  } catch (error) {
    if (error instanceof NotFoundError) return null;
    throw error;
  }
}

async function optional<T>(path: string): Promise<T | null> {
  try {
    return await cached<T>(path);
  } catch (error) {
    if (error instanceof NotFoundError) return null;
    throw error;
  }
}

export const loadEvalReport = (): Promise<EvalReport | null> => optional<EvalReport>("evals/latest.json");
export const loadEvalHistory = (): Promise<EvalHistory | null> => optional<EvalHistory>("evals/history.json");
export const loadSummaryIndex = (): Promise<SummaryIndex | null> => optional<SummaryIndex>("summaries/index.json");

/** Drop everything cached, after the data on the server has been refreshed. */
export function clearCache(): void {
  cache.clear();
}
