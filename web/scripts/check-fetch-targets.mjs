// Acceptance check from BUILD_BRIEF.md: no page makes an LLM call (or any other
// network call) at view time. The only allowed targets are the site's own
// /data/*.json files and static assets.
//
// This scans web/src and fails if:
//   * fetch() is used anywhere except src/data.ts
//   * any other network API appears (XMLHttpRequest, WebSocket, EventSource, sendBeacon)
//   * src/data.ts builds a URL from anything but the same-origin "/data/" root
//   * an absolute URL appears outside the allowlist of plain outbound links
import { readdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const src = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../src");
const LINK_HOSTS = new Set(["clinicaltrials.gov", "github.com", "www.w3.org"]);
const NETWORK_APIS = /\b(XMLHttpRequest|WebSocket|EventSource|sendBeacon|importScripts)\b/;

async function files(dir) {
  const out = [];
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...(await files(full)));
    else if (/\.(ts|css)$/.test(entry.name)) out.push(full);
  }
  return out;
}

const problems = [];
for (const file of await files(src)) {
  const name = path.relative(src, file);
  const text = await readFile(file, "utf8");
  const code = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
  if (name !== "data.ts" && /\bfetch\s*\(/.test(code)) problems.push(`${name}: fetch() outside data.ts`);
  if (NETWORK_APIS.test(code)) problems.push(`${name}: uses ${code.match(NETWORK_APIS)[0]}`);
  for (const [url, host] of code.matchAll(/https?:\/\/([a-z0-9.-]+)[^\s"'`)]*/gi)) {
    if (!LINK_HOSTS.has(host.toLowerCase())) problems.push(`${name}: unexpected URL ${url}`);
  }
  if (name === "data.ts") {
    const calls = [...code.matchAll(/\bfetch\s*\(([^,)]*)/g)].map((m) => m[1].trim());
    if (calls.length !== 1 || calls[0] !== "DATA_ROOT + path") {
      problems.push(`data.ts: fetch must be called exactly once, as fetch(DATA_ROOT + path); found ${JSON.stringify(calls)}`);
    }
    if (!/const DATA_ROOT = "\/data\/";/.test(code)) problems.push('data.ts: DATA_ROOT must be "/data/"');
  }
}

if (problems.length) {
  console.error("Fetch-target check FAILED:\n  " + problems.join("\n  "));
  process.exit(1);
}
console.log("Fetch-target check passed: the site only requests its own /data/*.json files.");
