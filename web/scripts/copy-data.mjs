// Copies the site's data files from the repo's committed data/ into public/data,
// so `vite dev` serves them and `vite build` ships them in dist/data.
//
// Only what the pages read is copied: meta.json, changes.json, collections/,
// sites/, trials/, summaries/ and evals/ (top-level files only, so the eval
// caches stay behind). Snapshots are pipeline state and never ship. Files are
// re-serialized without indentation to keep downloads small.
import { mkdir, readdir, readFile, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const source = process.env.CTD_DATA_DIR ? path.resolve(process.env.CTD_DATA_DIR) : path.resolve(here, "../../data");
const target = path.resolve(here, "../public/data");

const FILES = ["meta.json", "changes.json"];
const DIRECTORIES = ["collections", "sites", "trials", "summaries", "evals"];

async function copyJson(from, to) {
  const parsed = JSON.parse(await readFile(from, "utf8"));
  await writeFile(to, JSON.stringify(parsed));
}

async function main() {
  if (!existsSync(path.join(source, "meta.json"))) {
    throw new Error(`No data found at ${source}. Run "python -m pipeline.run daily" first.`);
  }
  await rm(target, { recursive: true, force: true });
  await mkdir(target, { recursive: true });
  let count = 0;
  for (const name of FILES) {
    await copyJson(path.join(source, name), path.join(target, name));
    count += 1;
  }
  for (const dir of DIRECTORIES) {
    const from = path.join(source, dir);
    if (!existsSync(from)) continue;
    await mkdir(path.join(target, dir), { recursive: true });
    const names = (await readdir(from)).filter((n) => n.endsWith(".json"));
    // Small batches keep memory flat with thousands of trial files.
    for (let i = 0; i < names.length; i += 200) {
      await Promise.all(
        names.slice(i, i + 200).map((n) => copyJson(path.join(from, n), path.join(target, dir, n))),
      );
    }
    count += names.length;
  }
  console.log(`copy-data: ${count} files -> ${path.relative(process.cwd(), target)}`);
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
