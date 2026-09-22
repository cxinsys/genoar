"use client";

import { useEffect, useRef, useState } from "react";
import { useStats } from "@/hooks/useStats";
import { useFilters } from "@/hooks/useFilters";
import type { FilterCategory, SpeciesCompleteness } from "@/types/api";
import { DASHBOARD_DATASET } from "@/lib/datasets";

function formatCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

// What the chart starts with, before it has measured the box it was given, and
// the most it will ever show. Twenty is where a ranked list stops being
// scannable; ten reads well in a column and is a fair guess at a first frame.
//
// Neither is a floor. The count that gets drawn is the count that fits, because
// the list is clipped to the box: asking for ten in a box that holds six does
// not show ten, it shows six and a half.
const INITIAL_TOP_BARS = 10;
const MAX_TOP_BARS = 20;

// How much of a side-by-side row the dial may take, and the ceilings for each
// arrangement. Beside the legend it is usually the height that binds; stacked
// above it, the width.
const DIAL_WIDTH_SHARE = 0.52;
const MAX_DIAL = 288;
const MAX_DIAL_STACKED = 224;

/* The donut's slices, named in globals.css so that a palette can answer them.
   A categorical set has to stay distinguishable from itself first and agree with
   the page second, but it does still have to agree with it — a violet and an
   orange from a general-purpose palette are two more schemes on a page that
   already has one. The order is the meaning: the first is the site's own colour,
   so the largest share of anything is the colour the site already is, and the
   last is Other. */
const DONUT_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
  "var(--chart-6)",
];

/* Keyed by rung rather than by position, so that a rung added or reordered in
   the API cannot silently repaint the others. `no_tissue` takes the same grey
   an "Other" slice would: it is the remainder, and reading it as one more rung
   of the ladder would be reading it as a kind of completeness. */
const COMPLETENESS_COLORS: Record<string, string> = {
  tissue_only: DONUT_COLORS[0],
  tissue_disease: DONUT_COLORS[1],
  tissue_cell_type: DONUT_COLORS[2],
  tissue_disease_cell_type: DONUT_COLORS[3],
  no_tissue: DONUT_COLORS[5],
};

function completenessSegments(species: SpeciesCompleteness) {
  return species.tiers
    .filter((t) => t.count > 0)
    .map((t) => ({
      label: t.label,
      pct: t.percentage,
      color: COMPLETENESS_COLORS[t.key] ?? DONUT_COLORS[5],
    }));
}

// Same box and same heading as the charts below — p-5, and the label styled as
// their h4 — so the top row reads as part of one set rather than a different
// kind of card. The figure keeps its own weight, which is the point of the card.
function KpiCard({
  label,
  value,
  sub,
  className = "",
}: {
  label: string;
  value: string;
  sub?: string;
  /** For the grid to say where the card goes, which differs by width. */
  className?: string;
}) {
  return (
    <div
      className={`bg-surface rounded-card border border-edge shadow-card p-5 flex flex-col ${className}`}
    >
      <h4 className="type-eyebrow">{label}</h4>
      {/* The cards are the same height whatever they hold, so a card with no
          second line has that line's room going spare. The figure takes it:
          bigger where there is nothing under it, and centred in what is left so
          neither kind of card sits with a gap at the bottom. */}
      <div className="mt-2 flex flex-1 flex-col justify-center">
        <span className={`type-figure ${sub ? "text-3xl" : "text-4xl"}`}>
          {value}
        </span>
        {sub && <span className="text-xs text-ink-faint mt-1">{sub}</span>}
      </div>
    </div>
  );
}

/** How much of the hole in the middle the icon takes. */
const CENTER_ICON_SHARE = 0.22;

function Donut({
  segments,
  centerIcon,
}: {
  segments: { label: string; pct: number; color: string }[];
  /** A Material Symbols name for the hole in the middle. The dial is a conic
      gradient with a disc over the centre, so there is already a circle here to
      put something in. Sized from the dial rather than fixed, so it keeps its
      proportion as the grid gives the card more or less room. */
  centerIcon?: string;
}) {
  const figureRef = useRef<HTMLDivElement>(null);
  const [dialSize, setDialSize] = useState<number>();

  const stops = segments
    .map((s, i) => {
      const start = segments.slice(0, i).reduce((a, x) => a + x.pct, 0);
      const end = start + s.pct;
      return `${s.color} ${start.toFixed(2)}% ${end.toFixed(2)}%`;
    })
    .join(", ");

  // The largest circle the space allows, measured rather than expressed in CSS.
  // `aspect-ratio` only holds while one axis is free: pinning the height and
  // capping the width means that once the cap bites, the ratio gives way and the
  // circle flattens into an ellipse. Taking the smaller of the two axes here
  // keeps it round and lets it use whichever one has room.
  //
  // Which way the legend sits is still CSS — the flex direction is read back
  // rather than duplicated as a second copy of the breakpoint.
  useEffect(() => {
    const figure = figureRef.current;
    if (!figure) return;

    const measure = () => {
      const style = getComputedStyle(figure);
      const padX =
        parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
      const padY =
        parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
      const width = figure.clientWidth - padX;
      const height = figure.clientHeight - padY;
      const beside = style.flexDirection === "row";
      const size = beside
        ? Math.min(width * DIAL_WIDTH_SHARE, height, MAX_DIAL)
        : Math.min(width, MAX_DIAL_STACKED);
      setDialSize(Math.max(0, Math.round(size)));
    };

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(figure);
    return () => observer.disconnect();
  }, []);

  return (
    // A container query, not a viewport one: how much room this chart has
    // depends on how many columns the grid is in, which the window width alone
    // does not tell you. Wide enough for both, the legend sits beside the dial;
    // narrower than that the card is taller than it is wide, and the legend
    // reads better underneath.
    <div className="@container flex flex-1 items-center justify-center">
      {/* Inset from the card's own padding. The other charts are bars that run
          to the edge, so the eye reads their margin from a straight line; a
          circle only touches that line at one point and its widest part looks
          tighter than the bars beside it. The extra space evens them out. */}
      <div
        ref={figureRef}
        className="flex w-full min-h-0 flex-col items-center gap-5 px-3 @sm:h-full @sm:flex-row @sm:justify-center @sm:gap-6 @sm:p-4"
      >
        {/* Sized from the space it is given rather than fixed: it takes the
            card's width up to a cap, so it grows when the grid gives the card
            more room and shrinks before it would crowd the legend. */}
        <div
          className="relative aspect-square shrink-0 rounded-full"
          style={{ width: dialSize, background: `conic-gradient(${stops})` }}
        >
          <div className="absolute inset-[22%] rounded-full bg-surface flex items-center justify-center">
            {centerIcon && (
              <span
                className="material-symbols-outlined text-ink-faint leading-none"
                style={{
                  fontSize: dialSize
                    ? Math.round(dialSize * CENTER_ICON_SHARE)
                    : undefined,
                }}
                aria-hidden="true"
              >
                {centerIcon}
              </span>
            )}
          </div>
        </div>
        <ul className="flex w-full min-w-0 flex-col gap-2 @sm:w-auto @sm:flex-1">
          {segments.map((s) => (
            <li
              key={s.label}
              className="flex items-center gap-2 text-xs text-ink-body"
            >
              <span
                className="size-2.5 rounded-sm shrink-0"
                style={{ backgroundColor: s.color }}
              />
              <span className="truncate">{s.label}</span>
              <span className="ml-auto font-semibold text-ink-soft pl-2">
                {s.pct.toFixed(1)}%
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function CompletenessCard({
  title,
  species,
  icon,
}: {
  title: string;
  species: SpeciesCompleteness | undefined;
  icon: string;
}) {
  const segments = species ? completenessSegments(species) : [];
  return (
    <div className="bg-surface rounded-card border border-edge shadow-card p-5 flex flex-col">
      <h4 className="type-eyebrow mb-4">{title}</h4>
      <div className="flex-1 flex min-h-0 items-stretch justify-center">
        {segments.length > 0 ? (
          <Donut segments={segments} centerIcon={icon} />
        ) : (
          <p className="text-sm text-ink-faint">No data</p>
        )}
      </div>
    </div>
  );
}

function TopBars({
  title,
  values,
  onSelect,
}: {
  title: string;
  values: { value: string; count: number }[];
  onSelect?: (value: string) => void;
}) {
  const rowsRef = useRef<HTMLDivElement>(null);
  const [rowCount, setRowCount] = useState(INITIAL_TOP_BARS);

  // How many bars fit is a question about the box this chart was given, and the
  // grid gives it whatever the tallest card in the row needs. Rather than
  // leaving that space blank under ten bars, fill it with the next entries.
  //
  // And rather than overrunning it: no floor. A minimum of ten draws rows the
  // box cannot hold whenever the cards beside it are short — two donuts are —
  // and the list is then cut through the middle of a bar.
  //
  // Measuring the box and not the list is what keeps this from running away:
  // when the card is the tallest one, the box is exactly as tall as the bars
  // already in it, so the count it computes is the count it has.
  useEffect(() => {
    const box = rowsRef.current;
    if (!box) return;

    const measure = () => {
      const row = box.querySelector("li");
      if (!row) return;
      const gap = 8; // gap-2
      const rowHeight = row.getBoundingClientRect().height + gap;
      if (rowHeight <= 0) return;
      const fits = Math.floor((box.clientHeight + gap) / rowHeight);
      setRowCount(Math.min(MAX_TOP_BARS, Math.max(1, fits)));
    };

    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(box);
    return () => observer.disconnect();
  }, [values]);

  const top = values.slice(0, Math.min(rowCount, values.length));
  const max = top.length > 0 ? top[0].count : 1;
  return (
    <div className="flex h-full flex-col">
      <h4 className="type-eyebrow mb-3">{title}</h4>
      <div
        ref={rowsRef}
        // The list is taken out of flow so this chart never sets the row's
        // height — the grid sizes the row from the other cards and this one
        // fills whatever that turns out to be. The floor is for the layouts
        // where it sits in a row by itself and has nothing to follow; at 2xl
        // it shares a row with the two charts beside it and takes their height.
        className="relative min-h-60 flex-1 2xl:min-h-0"
      >
        <ul className="absolute inset-0 flex flex-col gap-2 overflow-hidden">
          {top.map((v) => (
            <li key={v.value}>
              <button
                type="button"
                onClick={onSelect ? () => onSelect(v.value) : undefined}
                className={`w-full flex items-center gap-2 group ${onSelect ? "cursor-pointer" : "cursor-default"}`}
              >
                <span className="text-xs text-ink-body truncate w-28 text-left shrink-0 group-hover:text-brand">
                  {v.value}
                </span>
                <span className="flex-1 h-2 bg-sunken rounded-full overflow-hidden">
                  <span
                    className="block h-full rounded-full bg-brand group-hover:bg-accent transition-colors"
                    style={{ width: `${(v.count / max) * 100}%` }}
                  />
                </span>
                <span className="text-xs text-ink-faint w-12 text-right shrink-0">
                  {formatCount(v.count)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function DashboardOverview({
  onTissueSelect,
}: {
  onTissueSelect?: (tissue: string) => void;
}) {
  const { stats, isLoading: statsLoading } = useStats(DASHBOARD_DATASET);
  const { filters, isLoading: filtersLoading } = useFilters(DASHBOARD_DATASET);

  const isLoading = statsLoading || filtersLoading;

  const categoryOf = (name: string): FilterCategory | undefined =>
    filters?.categories.find((c) => c.name === name);

  // Two species and not a count of distinct organisms. The field holds 41
  // values, which reads as a corpus spanning 41 organisms; everything past
  // human and mouse comes to about 1% between them, and most of the 41 are one
  // submitter's spelling of a species already in the list.
  const completeness = stats?.metadata_completeness ?? [];
  const humanCompleteness = completeness.find((c) => c.species === "human");
  const mouseCompleteness = completeness.find((c) => c.species === "mouse");

  const tissueValues = categoryOf("tissue")?.values ?? [];
  const diseaseValues = categoryOf("disease")?.values ?? [];

  return (
    // px-6 is the padding the explorer above uses, so the cards line up with it
    // down the column. No top padding: the explorer's own bottom margin is the
    // gap between them, and two would read as a break rather than a boundary.
    <section className="px-6 pb-6">
      <div>
        {isLoading ? (
          <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 mb-4">
            {[1, 2, 3, 4].map((i) => (
              <div
                key={i}
                className="h-28 bg-surface rounded-card border border-edge shadow-card animate-pulse"
              />
            ))}
          </div>
        ) : (
          <>
            {/* KPI cards */}
            <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 mb-4">
              {/* No second line: it repeated the series count, which is the
                    card immediately beside it. */}
              {/* One row of four, or two rows of two, and the reading order is
                  not the same in both.

                  In one row it is the whole and then its parts: samples, the
                  two species that make them up, then series. Broken into two
                  rows that would put a species beside series, pairing figures
                  that have nothing to do with each other. So the columns become
                  the grouping instead — totals down the left, species down the
                  right — and the DOM is written in that order with the wide
                  layout restoring the other by hand.

                  None carries a second line: four cards of the same shape are
                  read as four of a kind, which is what they are. */}
              <KpiCard
                label="Total Samples"
                value={stats ? stats.total_samples.toLocaleString() : "--"}
                className="xl:order-1"
              />
              <KpiCard
                label="Homo sapiens Samples"
                value={
                  humanCompleteness
                    ? humanCompleteness.total.toLocaleString()
                    : "--"
                }
                className="xl:order-2"
              />
              <KpiCard
                label="Total Series"
                value={stats ? stats.total_series.toLocaleString() : "--"}
                className="xl:order-4"
              />
              <KpiCard
                label="Mus musculus Samples"
                value={
                  mouseCompleteness
                    ? mouseCompleteness.total.toLocaleString()
                    : "--"
                }
                className="xl:order-3"
              />
            </div>

            {/* Charts */}
            <div className="grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-4">
              {/* How much is known about each species' samples.

                  Two dials rather than one because the answer differs by more
                  than a little: a quarter of the human corpus carries a
                  diagnosis and almost none of the mouse corpus does, which a
                  combined chart would average away. What was here before —
                  assay type, library source, organism — describes a corpus that
                  is one assay, one library source and two organisms, so the
                  charts had nothing to distinguish. */}
              <CompletenessCard
                title="Homo sapiens Metadata"
                species={humanCompleteness}
                icon="person"
              />
              <CompletenessCard
                title="Mus musculus Metadata"
                species={mouseCompleteness}
                icon="pest_control_rodent"
              />

              {/* Top tissues */}
              <div className="bg-surface rounded-card border border-edge shadow-card p-5 md:col-span-2 2xl:col-span-1 flex flex-col">
                {tissueValues.length > 0 ? (
                  <TopBars
                    title="Top Tissues"
                    values={tissueValues}
                    onSelect={onTissueSelect}
                  />
                ) : diseaseValues.length > 0 ? (
                  <TopBars title="Top Diseases" values={diseaseValues} />
                ) : (
                  <p className="text-sm text-ink-faint">No data</p>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
