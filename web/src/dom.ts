// Tiny DOM helpers. Text always goes in through text nodes, never innerHTML,
// because registry text is untrusted input.

export type Child = Node | string | number | null | undefined | false | Child[];
type AttrValue = string | number | boolean | null | undefined | ((event: Event) => void);
export type Attrs = Record<string, AttrValue>;

const SVG_NS = "http://www.w3.org/2000/svg";

function applyAttrs(el: Element, attrs: Attrs | null | undefined): void {
  if (!attrs) return;
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (typeof value === "function") {
      el.addEventListener(key.replace(/^on/, ""), value as EventListener);
    } else if (value === true) {
      el.setAttribute(key, "");
    } else {
      el.setAttribute(key === "className" ? "class" : key, String(value));
    }
  }
}

function append(el: Element | DocumentFragment, children: Child[]): void {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    if (Array.isArray(child)) append(el, child);
    else if (child instanceof Node) el.appendChild(child);
    else el.appendChild(document.createTextNode(String(child)));
  }
}

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs?: Attrs | null,
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  applyAttrs(el, attrs);
  append(el, children);
  return el;
}

export function svg(tag: string, attrs?: Attrs | null, ...children: Child[]): SVGElement {
  const el = document.createElementNS(SVG_NS, tag);
  applyAttrs(el, attrs);
  append(el, children);
  return el;
}

export function fragment(...children: Child[]): DocumentFragment {
  const frag = document.createDocumentFragment();
  append(frag, children);
  return frag;
}

export function clear(el: Element): void {
  while (el.firstChild) el.removeChild(el.firstChild);
}

/** Internal link. The router picks up clicks on same-origin anchors. */
export function link(href: string, attrs: Attrs | null, ...children: Child[]): HTMLAnchorElement {
  return h("a", { href, ...attrs }, ...children);
}

/** External link that opens in a new tab and says so. */
export function external(href: string, attrs: Attrs | null, ...children: Child[]): HTMLAnchorElement {
  return h(
    "a",
    { href, target: "_blank", rel: "noopener noreferrer", ...attrs },
    ...children,
    h("span", { class: "ext", "aria-hidden": "true" }, "↗"),
    h("span", { class: "sr-only" }, " (opens in a new tab)"),
  );
}
