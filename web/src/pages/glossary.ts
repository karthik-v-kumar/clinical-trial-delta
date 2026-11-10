// Glossary: every term the site uses, in plain language.

import type { Page } from "../config";
import { h } from "../dom";
import { GLOSSARY, GLOSSARY_GROUPS } from "../glossary";

export function glossaryPage(): Page {
  const node = h(
    "div",
    { class: "page" },
    h(
      "div",
      { class: "hero" },
      h("p", { class: "eyebrow" }, "Reference"),
      h("h1", null, "Glossary"),
      h(
        "p",
        { class: "lede" },
        "Plain-language explanations of the trial terms used on this site. Anywhere you see a dotted underline, you can tap the word for the same explanation.",
      ),
    ),
    GLOSSARY_GROUPS.map((group) =>
      h(
        "section",
        { class: "section" },
        h("h2", null, group),
        h(
          "dl",
          { class: "glossary" },
          Object.entries(GLOSSARY)
            .filter(([, entry]) => entry.group === group)
            .map(([key, entry]) => h("div", { id: key, class: "glossary-entry" }, h("dt", null, entry.title), h("dd", null, entry.text))),
        ),
      ),
    ),
  );
  return { title: "Glossary", node };
}
