// Hand-rolled SVG bar charts (no chart library).
//
// House rules, applied to every chart:
//  * both axes carry labels: the value axis has numbered ticks, the category
//    axis names every category, and each axis has a title
//  * one series, one colour; text never wears the series colour
//  * every bar shows its value, has a hover / focus / tap read-out, and the same
//    numbers are available as a table under the chart

import { clear, h, svg, type Child } from "./dom";
import { formatInt } from "./format";

export interface ChartDatum {
  key: string;
  label: string; // full name, used in read-outs and the table
  axisLabel: string; // short name drawn on the category axis
  value: number;
}

export interface ChartOptions {
  title: Child; // heading content (may include glossary terms)
  summary: string; // one sentence for screen readers
  data: ChartDatum[];
  categoryTitle: string; // e.g. "Phase"
  valueTitle: string; // e.g. "Trials"
  unit: [string, string]; // singular, plural: ["trial", "trials"]
  orientation: "columns" | "bars";
  /** Fixed top of the value axis (e.g. 100 for percentages, 5 for a 1-5 score). */
  max?: number;
  /** Tick step when `max` is set. */
  step?: number;
  /** Formats values and ticks; defaults to whole numbers. */
  format?: (value: number) => string;
  /** Hide the "share of total" column and read-out (for rates and scores). */
  noShare?: boolean;
}

const FONT = '12px system-ui, -apple-system, "Segoe UI", sans-serif';
const BAR_MAX = 24;
const RADIUS = 4;

let measureContext: CanvasRenderingContext2D | null = null;
function textWidth(text: string): number {
  measureContext ??= document.createElement("canvas").getContext("2d");
  if (!measureContext) return text.length * 6.5;
  measureContext.font = FONT;
  return measureContext.measureText(text).width;
}

/** Round, evenly spaced axis ticks from 0 to at least `max`. */
export function niceTicks(max: number, target = 4): number[] {
  if (max <= 0) return [0, 1];
  const rough = max / target;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= rough) ?? 10 * power;
  const whole = Math.max(1, step);
  const ticks: number[] = [];
  for (let value = 0; value < max + whole; value += whole) ticks.push(value);
  return ticks;
}

/** Fit a category label into `room` pixels: one line, else two, shortening the second with "…". */
function fitLabel(text: string, room: number): string[] {
  if (textWidth(text) <= room) return [text];
  const words = text.split(" ");
  let first = "";
  while (words.length && textWidth(first ? `${first} ${words[0]}` : words[0]!) <= room) {
    first = first ? `${first} ${words.shift()}` : words.shift()!;
  }
  let second = words.join(" ");
  if (!first) {
    first = second;
    second = "";
  }
  const shorten = (line: string) => {
    if (textWidth(line) <= room) return line;
    while (line.length > 1 && textWidth(`${line}…`) > room) line = line.slice(0, -1);
    return `${line.trimEnd()}…`;
  };
  return second ? [shorten(first), shorten(second)] : [shorten(first)];
}

function share(value: number, total: number): string {
  if (!total) return "";
  const percent = (value / total) * 100;
  return percent > 0 && percent < 1 ? "under 1%" : `${Math.round(percent)}%`;
}

export function barChart(options: ChartOptions): HTMLElement {
  const { data, unit } = options;
  const fmt = options.format ?? formatInt;
  const total = options.noShare ? 0 : data.reduce((sum, d) => sum + d.value, 0);
  const describe = (d: ChartDatum) =>
    options.format ? `${fmt(d.value)} ${unit[1]}`.trim() : `${formatInt(d.value)} ${d.value === 1 ? unit[0] : unit[1]}`;
  const ticksFor = (target: number): number[] => {
    if (options.max !== undefined) {
      const step = options.step ?? options.max / 4;
      const out: number[] = [];
      for (let v = 0; v <= options.max + 1e-9; v += step) out.push(Math.round(v * 100) / 100);
      return out;
    }
    return niceTicks(Math.max(...data.map((d) => d.value), 1), target);
  };

  const plot = h("div", { class: "chart-plot" });
  const tip = h("div", { class: "chart-tip", hidden: true });
  const figure = h(
    "figure",
    { class: "chart" },
    h("figcaption", { class: "chart-title" }, options.title),
    h("div", { class: "chart-frame" }, plot, tip),
    h(
      "details",
      { class: "chart-table" },
      h("summary", null, "View as a table"),
      h(
        "table",
        null,
        h(
          "thead",
          null,
          h(
            "tr",
            null,
            h("th", { scope: "col" }, options.categoryTitle),
            h("th", { scope: "col", class: "num" }, options.valueTitle),
            options.noShare ? null : h("th", { scope: "col", class: "num" }, "Share"),
          ),
        ),
        h(
          "tbody",
          null,
          data.map((d) =>
            h(
              "tr",
              null,
              h("th", { scope: "row" }, d.label),
              h("td", { class: "num" }, fmt(d.value)),
              options.noShare ? null : h("td", { class: "num" }, share(d.value, total)),
            ),
          ),
        ),
      ),
    ),
  );

  const showTip = (d: ChartDatum, x: number, y: number) => {
    clear(tip);
    tip.append(
      h("strong", null, describe(d)),
      h("span", null, d.label + (total ? ` · ${share(d.value, total)} of ${formatInt(total)}` : "")),
    );
    tip.hidden = false;
    const frame = plot.getBoundingClientRect();
    const width = tip.offsetWidth;
    const left = Math.min(Math.max(x - width / 2, 0), Math.max(frame.width - width, 0));
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(y - tip.offsetHeight - 8, 0)}px`;
  };
  const hideTip = () => {
    tip.hidden = true;
  };

  /** A focusable, hoverable group covering one category's whole band. */
  const band = (d: ChartDatum, hit: { x: number; y: number; w: number; h: number }, anchor: [number, number]) => {
    const group = svg("g", { class: "chart-band", tabindex: "0", role: "img", "aria-label": `${d.label}: ${describe(d)}` });
    const show = () => showTip(d, anchor[0], anchor[1]);
    group.addEventListener("pointerenter", show);
    group.addEventListener("pointerleave", hideTip);
    group.addEventListener("focus", show);
    group.addEventListener("blur", hideTip);
    group.addEventListener("click", show);
    group.appendChild(svg("rect", { class: "chart-hit", x: hit.x, y: hit.y, width: hit.w, height: hit.h }));
    return group;
  };

  const draw = () => {
    const width = Math.floor(plot.clientWidth);
    if (width < 120) return;
    clear(plot);
    hideTip();
    plot.appendChild(options.orientation === "columns" ? drawColumns(width) : drawBars(width));
  };

  const drawColumns = (width: number): SVGElement => {
    const ticks = ticksFor(4);
    const top = ticks[ticks.length - 1] ?? 1;
    const tickLabels = ticks.map((t) => fmt(t));
    const left = Math.ceil(Math.max(...tickLabels.map(textWidth))) + 10;
    const right = 8;
    const bandWidth = (width - left - right) / data.length;
    // Category labels wrap onto two lines when a band is too narrow for them.
    const lines = data.map((d) =>
      textWidth(d.axisLabel) > bandWidth - 4 && d.axisLabel.includes(" ") ? d.axisLabel.split(" ") : [d.axisLabel],
    );
    const labelRows = Math.max(...lines.map((l) => l.length));
    const marginTop = 34; // room for the axis title and the tallest bar's value
    const plotHeight = 180;
    const bottom = 10 + labelRows * 14 + 22;
    const height = marginTop + plotHeight + bottom;
    const y = (value: number) => marginTop + plotHeight - (value / top) * plotHeight;

    const root = svg("svg", {
      class: "chart-svg",
      width,
      height,
      viewBox: `0 0 ${width} ${height}`,
      role: "group",
      "aria-label": options.summary,
    });
    root.appendChild(svg("text", { class: "axis-title", x: 0, y: 12 }, options.valueTitle));
    ticks.forEach((tick, i) => {
      root.appendChild(
        svg("line", { class: tick === 0 ? "axis-line" : "grid-line", x1: left, x2: width - right, y1: y(tick), y2: y(tick) }),
      );
      root.appendChild(
        svg("text", { class: "tick", x: left - 6, y: y(tick) + 4, "text-anchor": "end" }, tickLabels[i] ?? ""),
      );
    });
    const barWidth = Math.min(BAR_MAX, Math.max(bandWidth - 8, 4));
    data.forEach((d, i) => {
      const center = left + bandWidth * (i + 0.5);
      const barTop = y(d.value);
      const barHeight = marginTop + plotHeight - barTop;
      const group = band(d, { x: left + bandWidth * i, y: marginTop - 18, w: bandWidth, h: plotHeight + 18 + bottom }, [
        center,
        barTop - 14,
      ]);
      if (barHeight > 0) {
        const r = Math.min(RADIUS, barWidth / 2, barHeight);
        const x0 = center - barWidth / 2;
        const x1 = center + barWidth / 2;
        const base = marginTop + plotHeight;
        group.appendChild(
          svg("path", {
            class: "bar",
            d: `M${x0},${base} V${barTop + r} Q${x0},${barTop} ${x0 + r},${barTop} H${x1 - r} Q${x1},${barTop} ${x1},${barTop + r} V${base} Z`,
          }),
        );
      }
      group.appendChild(
        svg("text", { class: "bar-value", x: center, y: barTop - 5, "text-anchor": "middle" }, fmt(d.value)),
      );
      (lines[i] ?? []).forEach((line, row) => {
        group.appendChild(
          svg(
            "text",
            { class: "tick", x: center, y: marginTop + plotHeight + 16 + row * 14, "text-anchor": "middle" },
            line,
          ),
        );
      });
      root.appendChild(group);
    });
    root.appendChild(
      svg(
        "text",
        { class: "axis-title", x: left + (width - left - right) / 2, y: height - 4, "text-anchor": "middle" },
        options.categoryTitle,
      ),
    );
    return root;
  };

  const drawBars = (width: number): SVGElement => {
    const ticks = ticksFor(width < 420 ? 3 : 5);
    const top = ticks[ticks.length - 1] ?? 1;
    const labelWidth = Math.min(Math.ceil(Math.max(...data.map((d) => textWidth(d.axisLabel)))) + 10, width * 0.48);
    const valueRoom = Math.ceil(Math.max(...data.map((d) => textWidth(fmt(d.value))))) + 10;
    const left = labelWidth;
    const right = valueRoom;
    const rowHeight = 30;
    const marginTop = 22;
    const plotHeight = rowHeight * data.length;
    const bottom = 40;
    const height = marginTop + plotHeight + bottom;
    const x = (value: number) => left + (value / top) * (width - left - right);
    const barHeight = Math.min(BAR_MAX, 16);

    const root = svg("svg", {
      class: "chart-svg",
      width,
      height,
      viewBox: `0 0 ${width} ${height}`,
      role: "group",
      "aria-label": options.summary,
    });
    root.appendChild(svg("text", { class: "axis-title", x: 0, y: 12 }, options.categoryTitle));
    ticks.forEach((tick) => {
      root.appendChild(
        svg("line", {
          class: tick === 0 ? "axis-line" : "grid-line",
          x1: x(tick),
          x2: x(tick),
          y1: marginTop,
          y2: marginTop + plotHeight,
        }),
      );
      root.appendChild(
        svg("text", { class: "tick", x: x(tick), y: marginTop + plotHeight + 16, "text-anchor": "middle" }, fmt(tick)),
      );
    });
    data.forEach((d, i) => {
      const middle = marginTop + rowHeight * (i + 0.5);
      const end = x(d.value);
      const group = band(d, { x: 0, y: marginTop + rowHeight * i, w: width, h: rowHeight }, [
        Math.min(end + 4, width - 40),
        middle - barHeight / 2,
      ]);
      // Long labels wrap onto a second line (and shorten only if they still do not fit);
      // the full label stays in the bar's read-out and the data table.
      const lines = fitLabel(d.axisLabel, left - 10);
      lines.forEach((line, row) => {
        const y = lines.length === 1 ? middle + 4 : middle - 2 + row * 13;
        group.appendChild(svg("text", { class: "tick category", x: left - 8, y, "text-anchor": "end" }, line));
      });
      const length = end - left;
      if (length > 0) {
        const r = Math.min(RADIUS, barHeight / 2, length);
        const y0 = middle - barHeight / 2;
        const y1 = middle + barHeight / 2;
        group.appendChild(
          svg("path", {
            class: "bar",
            d: `M${left},${y0} H${end - r} Q${end},${y0} ${end},${y0 + r} V${y1 - r} Q${end},${y1} ${end - r},${y1} H${left} Z`,
          }),
        );
      }
      group.appendChild(svg("text", { class: "bar-value", x: end + 6, y: middle + 4 }, fmt(d.value)));
      root.appendChild(group);
    });
    root.appendChild(
      svg(
        "text",
        { class: "axis-title", x: left + (width - left - right) / 2, y: height - 6, "text-anchor": "middle" },
        options.valueTitle,
      ),
    );
    return root;
  };

  // Draw at the real pixel width so text stays the same size on every screen.
  let frame = 0;
  const observer = new ResizeObserver(() => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(draw);
  });
  observer.observe(plot);
  return figure;
}
