"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { SampleSummary } from "@/types/api";

/**
 * The current page of semantic results as a radial map: the query at the centre,
 * each result a blob whose distance stands for how close it is to the query.
 *
 * Distance cannot come from the raw score. The embeddings put every neighbour in
 * a narrow band — a typical page spans about 0.02 of cosine similarity — so
 * plotting the raw value draws twenty blobs on one ring and says nothing. The
 * default therefore stretches the page's own range across the radius, which
 * shows the ordering at the cost of a scale that changes with every query. The
 * Absolute toggle plots the raw value on a fixed 0–1 scale, where the band is
 * the honest picture; both are offered because neither is the whole truth.
 */

type Mode = "normalized" | "absolute";

/** Which column the map is in: the sidebar, or the results' own space. */
export type MapVariant = "compact" | "expanded";

interface Geometry {
  /* The box the map is drawn in, in its own units. Not square when there is a
     wide column to fill: the drawing is a disc but its labels run sideways off
     it, so width past what the disc needs is exactly what the labels want. A
     square box in a wide column left the sides empty, drew the edge fade around
     the square rather than around the box, and let the labels run out past the
     fade and into the empty space — the fade marking a boundary that nothing
     was actually stopping at. */
  w: number;
  h: number;
  rInner: number; // closest a result sits to the query block
  rOuter: number; // furthest
  queryW: number;
  queryH: number;
  queryFont: number;
  queryLine: number;
  queryLines: number;
  blob: number;
  blobHover: number;
  labelGap: number; // blob to the start of its label
  fontId: number;
  fontMeta: number;
  hitWidth: number; // the invisible width given to a spoke as a target
  fade: number; // width of the fade at each edge
  labelLimit: number; // how many results carry a label
  tissueChars: number;
}

/* Expanded is not compact scaled up. Everything geometric grows — the radii most
   of all, since spreading the blobs is the point — but the type grows by much
   less, because the box is rendered nearly twice as wide and type that scaled
   with the box would arrive twice the size on screen. Growing the radii while
   holding the type is what turns extra width into room between labels rather
   than into bigger labels, and it is why the expanded map can name thirty-five
   results where the compact one names twenty. */
const GEOMETRY: Record<MapVariant, Geometry> = {
  compact: {
    w: 300,
    h: 300,
    rInner: 58,
    rOuter: 124,
    queryW: 76,
    queryH: 40,
    queryFont: 8.5,
    queryLine: 10.5,
    queryLines: 3,
    blob: 5,
    blobHover: 6.5,
    labelGap: 9,
    fontId: 7.5,
    fontMeta: 6.5,
    hitWidth: 7,
    fade: 18,
    labelLimit: 20,
    tissueChars: 16,
  },
  expanded: {
    // Landscape, and the disc is sized by the height: the radius has the shorter
    // side to fit inside, and what is left over sideways is where the labels go.
    //
    // The box is drawn into the column's full width whatever these numbers are,
    // so they are not a size but a proportion — fewer units across the same
    // pixels is a closer view. They are set so the outermost labels reach into
    // the edge fade rather than stopping short of it: the fade is there to soften
    // what the box cuts off, and a drawing that keeps clear of it leaves a margin
    // of empty card instead.
    w: 720,
    h: 500,
    rInner: 112,
    rOuter: 226,
    queryW: 150,
    queryH: 68,
    queryFont: 11,
    queryLine: 14,
    queryLines: 4,
    blob: 6,
    blobHover: 8,
    labelGap: 11,
    fontId: 9,
    fontMeta: 8,
    hitWidth: 9,
    fade: 18,
    labelLimit: 35,
    tissueChars: 22,
  },
};

/** The card's own inset. Narrower once the map is the wide column's subject:
    every pixel the padding gives back is a pixel the drawing is scaled into,
    and 20 of them on each side is a visible strip of nothing around a map that
    is trying to fill the column. The sidebar keeps the site's card padding,
    where the map is one card among several and has to match them. */
const CARD_PAD: Record<MapVariant, string> = {
  compact: "p-5",
  expanded: "p-4",
};

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5)); // spreads angles without clumping

/** Pointer travel that turns a press into a drag rather than a click. */
const DRAG_SLOP = 4;

/* How long a pointer has to stay on one blob before the map clears the writing
 * off everything else.
 *
 * Hovering answers straight away — the blob lights, the rest dim — because that
 * is the reading of the map you get by moving over it, and delaying it makes the
 * map feel unresponsive. Emptying the map of every other label is a different
 * kind of answer: it helps when you are studying one result and is a flicker
 * when you are passing over twenty. Half a second of staying still is what tells
 * the two apart, and it is a pause a sweep across the map never contains.
 */
const FOCUS_DELAY = 500;

interface Props {
  query: string;
  items: SampleSummary[];
  /** While true the map is not drawn at all. The items in hand belong to the
      previous search, and drawing them would play the arrival for results that
      are about to be replaced — then play it again for the ones that arrive. */
  isLoading?: boolean;
  variant?: MapVariant;
  /** Given, the header carries the button that trades places with the results. */
  onToggleVariant?: () => void;
}

interface Node {
  sample: SampleSummary;
  score: number;
  x: number;
  y: number;
  outward: 1 | -1; // which side of the centre the label extends to
  labelled: boolean;
}

function truncate(value: string, max: number): string {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value;
}

/* The card's frame, shared by the map and by the state that stands in for it
   while a search runs. Written once rather than twice on purpose: the two have
   to be the same height to the pixel, and two copies of a height are two heights
   waiting to disagree — which is what made the card jump as results arrived. */

/** What the map is, for a reader meeting it for the first time.
 *
 * The caption under the dial says which scale is in use. This answers the
 * question before that one — what the centre is, what a blob is, what the
 * distance between them means — which is what the map was asked in review and
 * had no answer to.
 *
 * `normal-case` and `tracking-normal` because the heading it sits in is an
 * eyebrow: without them the explanation would inherit the small capitals meant
 * for a two-word label.
 *
 * Answers focus as well as hover. A control that only replies to a mouse is not
 * a reply to everyone.
 */
function MapHelp() {
  return (
    <span className="relative group/help inline-flex">
      <button
        type="button"
        aria-label="What this map shows"
        className="inline-flex items-center text-ink-faint hover:text-brand focus-visible:text-brand transition-colors cursor-help"
      >
        <span className="material-symbols-outlined text-[15px] leading-none">
          help
        </span>
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-0 top-full z-50 mt-1.5 w-64 rounded-md bg-inverse px-2.5 py-2 text-[11px] font-normal normal-case leading-snug tracking-normal text-on-inverse opacity-0 transition-opacity group-hover/help:opacity-100 group-focus-within/help:opacity-100"
      >
        Your query sits at the centre. Each blob is one result, placed by how
        close its metadata is to what you asked for, nearer means more similar.
        Hover one for its accession and score, or click it to open the sample.
      </span>
    </span>
  );
}

function CardHead({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between mb-3 gap-2">
      {/* The same label the dashboard's chart cards carry. The icon follows the
          label's colour and size rather than out-weighing it. */}
      <h3 className="type-eyebrow flex items-center gap-1.5">
        <span className="material-symbols-outlined text-[16px] leading-none">
          hub
        </span>
        Similarity Map
        <MapHelp />
      </h3>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

function ScaleToggle({
  mode,
  onChange,
  disabled = false,
}: {
  mode: Mode;
  onChange?: (m: Mode) => void;
  disabled?: boolean;
}) {
  return (
    <div
      className="inline-flex bg-sunken p-0.5 rounded-md"
      role="group"
      aria-label="Distance scale"
    >
      {(["normalized", "absolute"] as Mode[]).map((m) => (
        <button
          key={m}
          type="button"
          disabled={disabled}
          onClick={() => onChange?.(m)}
          aria-pressed={mode === m}
          data-testid={`map-scale-${m}`}
          className={`px-2 py-1 rounded text-[11px] font-medium capitalize transition-colors cursor-pointer ${
            mode === m
              ? "bg-surface text-ink shadow-control"
              : "text-ink-soft hover:text-ink"
          }`}
        >
          {m}
        </button>
      ))}
    </div>
  );
}

/** Trades the map's place with the results list.
 *
 * Icon only, and last in the row: it acts on the card as a whole rather than on
 * anything the card shows, which is the corner a window control belongs in.
 * swap_horiz because that is what it does — the two columns exchange contents,
 * left for right; it does not expand the map into a layer of its own.
 */
function SwapButton({
  variant,
  onClick,
}: {
  variant: MapVariant;
  onClick: () => void;
}) {
  const expanded = variant === "expanded";
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={expanded}
      title={
        expanded
          ? "Move the map back to the sidebar"
          : "Swap the map with the results"
      }
      aria-label={
        expanded
          ? "Move the map back to the sidebar"
          : "Swap the map with the results"
      }
      data-testid="map-swap"
      // The scale toggle's height, so the row keeps one line.
      className={`w-[26px] h-[26px] inline-flex items-center justify-center rounded-md cursor-pointer transition-colors ${
        expanded
          ? "bg-brand text-on-brand hover:bg-brand-soft"
          : "bg-sunken text-ink-body hover:bg-edge hover:text-ink"
      }`}
    >
      <span className="material-symbols-outlined text-[16px] leading-none">
        swap_horiz
      </span>
    </button>
  );
}

/** The rings and their shading — the ruler the blobs are read against.
 *
 * Drawn the same in both states, and identically enough that the browser has no
 * reason to repaint it when the results arrive. Leaving it out of the loading
 * state made the shading appear from nothing the moment the map did, which reads
 * as a flicker rather than as an arrival.
 */
function MapBackdrop({ g }: { g: Geometry }) {
  const cx = g.w / 2;
  const cy = g.h / 2;
  const rings = [g.rInner, (g.rInner + g.rOuter) / 2, g.rOuter];
  return (
    <>
      {/* Bands, palest at the rim. Drawn as filled discs from the outside in, so
          each one lays its tint over the one before it and the centre ends up the
          deepest without any of them being opaque.

          Their edges are the rings' own radii, so every change of colour happens
          exactly at a dashed line rather than somewhere between two of them. */}
      {[...rings].reverse().map((r) => (
        <circle
          key={`band-${r}`}
          cx={cx}
          cy={cy}
          r={r}
          fill="var(--color-highlight)"
          fillOpacity="0.028"
        />
      ))}
      {/* Rings for the eye to judge distance against */}
      {rings.map((r) => (
        <circle
          key={r}
          cx={cx}
          cy={cy}
          r={r}
          fill="none"
          stroke="var(--color-edge)"
          strokeWidth={g.h / 400}
          strokeDasharray="2 3"
        />
      ))}
    </>
  );
}

/** What the distances mean. Shown while loading as well as after: it describes
    the scale, which is chosen before any result arrives, and a caption that
    appears with the map would take the card's foot with it. */
function Caption({
  mode,
  labelNote,
}: {
  mode: Mode;
  labelNote: number | null;
}) {
  return (
    <p className="text-[11px] leading-snug text-ink-soft mt-2 border-t border-edge pt-2">
      {mode === "normalized" ? (
        <>
          <span className="font-semibold text-amber-700">Relative scale.</span>{" "}
          Distance is stretched across this page&apos;s own range, so the
          nearest result always sits innermost. Compare positions within one
          search, not between searches. The percentages are the actual
          similarity.
        </>
      ) : (
        <>
          <span className="font-semibold text-ink-body">Absolute scale.</span>{" "}
          Distance is the raw similarity on a fixed 0–1 scale. Results cluster
          in a band because the embedding puts every neighbour close together.
        </>
      )}
      {labelNote != null && (
        <> The closest {labelNote} are labelled; hover any blob for its own.</>
      )}{" "}
      Drag to pan.
    </p>
  );
}

export default function SimilarityMap({
  query,
  items,
  isLoading = false,
  variant = "compact",
  onToggleVariant,
}: Props) {
  const [mode, setMode] = useState<Mode>("normalized");
  const [hovered, setHovered] = useState<string | null>(null);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  // Which set of results the pan belongs to. A new set is a new drawing, and
  // carrying the old offset into it would have the map arrive off to one side.
  const [pannedSet, setPannedSet] = useState("");
  const svgRef = useRef<SVGSVGElement>(null);
  // Where the press started, or null when no button is down. Cleared on release:
  // a record left behind would make the next bare mouse move read as a drag from
  // wherever the press had been, and throw the map off the box.
  const drag = useRef<{
    x: number;
    y: number;
    panX: number;
    panY: number;
  } | null>(null);
  // Whether that press turned into a drag. Outlives the record by one event, for
  // the click that follows the release. A ref, not state, because the click
  // handler reads it in the same tick — before any re-render could deliver it.
  const dragged = useRef(false);
  const router = useRouter();

  // The blob that has been hovered long enough to be the only one still labelled.
  // Dropped the moment the hover moves, and taken up again only after the new one
  // has been held for as long.
  const [focused, setFocused] = useState<string | null>(null);
  // Dropped during the render that moves the hover, not in an effect: the labels
  // would otherwise be drawn once for the blob the pointer has already left.
  if (focused && focused !== hovered) setFocused(null);
  useEffect(() => {
    if (!hovered) return;
    const timer = setTimeout(() => setFocused(hovered), FOCUS_DELAY);
    return () => clearTimeout(timer);
  }, [hovered]);

  const g = GEOMETRY[variant];
  const cx = g.w / 2;
  const cy = g.h / 2;

  const scored = useMemo(
    () =>
      items.filter(
        (s): s is SampleSummary & { similarity_score: number } =>
          s.similarity_score != null,
      ),
    [items],
  );

  const nodes = useMemo<Node[]>(() => {
    if (scored.length === 0) return [];
    const scores = scored.map((s) => s.similarity_score);
    const max = Math.max(...scores);
    const min = Math.min(...scores);
    const span = max - min;

    // Which results are labelled is decided by score, not by the order the page
    // happens to be sorted in: sorting by run_id must not change which blobs are
    // named.
    const rank = new Map<string, number>();
    [...scored]
      .sort((a, b) => b.similarity_score - a.similarity_score)
      .forEach((s, i) => rank.set(s.run_id, i));

    return scored.map((sample, i) => {
      // Fraction of the radius to travel outward: 0 is the best match.
      let out: number;
      if (mode === "normalized") {
        // A page whose scores are all equal has no ordering to show, so the
        // whole set sits at one middle ring rather than dividing by zero.
        out = span === 0 ? 0.5 : 1 - (sample.similarity_score - min) / span;
      } else {
        out = 1 - sample.similarity_score; // raw cosine on a fixed 0–1 scale
      }
      const r =
        g.rInner + Math.min(Math.max(out, 0), 1) * (g.rOuter - g.rInner);
      const angle = i * GOLDEN_ANGLE - Math.PI / 2; // first result at the top
      return {
        sample,
        score: sample.similarity_score,
        x: cx + r * Math.cos(angle),
        y: cy + r * Math.sin(angle),
        outward: Math.cos(angle) >= 0 ? 1 : -1,
        labelled: (rank.get(sample.run_id) ?? 0) < g.labelLimit,
      };
    });
  }, [scored, mode, g, cx, cy]);

  // Restarting a CSS animation means giving the element a new identity, so this
  // carries whatever a fresh set of results would change. Not the mode: toggling
  // the scale rearranges the same blobs, and replaying the arrival there would
  // read as the map reloading when it did not. The variant is in it because the
  // map is redrawn at another size, which is worth arriving for.
  const bloomKey = `${query}|${variant}|${nodes.length}|${nodes[0]?.sample.run_id ?? ""}`;
  if (pannedSet !== bloomKey) {
    setPannedSet(bloomKey);
    if (pan.x !== 0 || pan.y !== 0) setPan({ x: 0, y: 0 });
  }

  function handlePointerDown(e: React.PointerEvent<SVGSVGElement>) {
    drag.current = { x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y };
    dragged.current = false;
  }

  function handlePointerMove(e: React.PointerEvent<SVGSVGElement>) {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (!dragged.current && Math.hypot(dx, dy) < DRAG_SLOP) return;
    dragged.current = true;
    // Screen pixels are not view units: the box is drawn into whatever width the
    // card gives it, so a drag has to be divided by that scale to move the map
    // the same distance the pointer went.
    const rect = svgRef.current?.getBoundingClientRect();
    const scale = rect && rect.width > 0 ? g.w / rect.width : 1;
    setPan({ x: d.panX + dx * scale, y: d.panY + dy * scale });
  }

  function handlePointerUp() {
    drag.current = null;
  }

  function handleNodeClick(runId: string) {
    if (dragged.current) return; // the press was a pan, not a choice
    router.push(`/sample/${runId}`);
  }

  // Nothing is drawn while the results are being replaced. The items still in
  // hand are the previous search's, so drawing them would play the arrival for
  // a set about to be thrown away and then again for the one that replaces it —
  // which is the double bloom that follows a skeleton.
  if (isLoading) {
    return (
      <div
        className={`bg-surface border border-edge rounded-card shadow-card ${CARD_PAD[variant]}`}
      >
        {/* The real controls, only inert. A placeholder box could stand in for
            their width but not for the height their own text and padding work out
            to, and being a pixel off made the whole card twitch when the map
            arrived. */}
        <CardHead>
          <ScaleToggle mode={mode} disabled />
          {onToggleVariant && (
            <SwapButton variant={variant} onClick={onToggleVariant} />
          )}
        </CardHead>
        <svg
          viewBox={`0 0 ${g.w} ${g.h}`}
          className="w-full h-auto"
          aria-hidden="true"
        >
          <MapBackdrop g={g} />
          {/* Only the query block pulses. Pulsing the rings with it would fade
              them out and back as the map arrived, which is the flicker the
              shared backdrop is there to avoid. */}
          <rect
            className="animate-pulse"
            x={cx - g.queryW / 2}
            y={cy - g.queryH / 2}
            width={g.queryW}
            height={g.queryH}
            rx={g.queryW / 8}
            fill="var(--color-edge)"
          />
        </svg>
        {/* The same caption, not a "loading…" line: it explains the scale, which
            is already chosen, and swapping four lines for one would drop the foot
            of the card — and everything below it in the sidebar — by that much. */}
        <Caption mode={mode} labelNote={null} />
      </div>
    );
  }

  if (nodes.length === 0) return null;

  const panned = pan.x !== 0 || pan.y !== 0;

  /* The blobs are drawn in one fixed order, and the hovered one is not lifted to
     the front of it.

     Reordering was how the hovered label was kept on top of its neighbours', and
     it cost more than it bought: React answers a changed order by moving the DOM
     nodes, and a moved element restarts every CSS animation on it and its
     children. So the labels of every blob after the hovered one in the list
     replayed their fade — a blink of vanishing and coming back, on some elements
     and not others, on hovering and again on letting go. (The same moves were
     also swallowing mouseleave, which is the hover that would not clear.)

     What the raise was for barely arises now: once a blob has been held long
     enough for its label to matter on its own, the neighbours' labels are gone. */

  return (
    <div
      className={`bg-surface border border-edge rounded-card shadow-card ${CARD_PAD[variant]}`}
    >
      <CardHead>
        {/* Beside the scale toggle rather than over the map: on the map it sat on
            top of the thing it was meant to bring back into view, and it appears
            and disappears, which is the last thing a drawing wants floating in
            it. Its own height matches the toggle's, so the row does not grow when
            it turns up. */}
        {panned && (
          <button
            type="button"
            onClick={() => setPan({ x: 0, y: 0 })}
            data-testid="map-recentre"
            className="px-2.5 py-1.5 rounded-md bg-sunken hover:bg-edge text-[11px] font-medium text-ink-body hover:text-ink cursor-pointer transition-colors"
          >
            Recentre
          </button>
        )}
        <ScaleToggle mode={mode} onChange={setMode} />
        {onToggleVariant && (
          <SwapButton variant={variant} onClick={onToggleVariant} />
        )}
      </CardHead>

      <div className="relative">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${g.w} ${g.h}`}
          // No height cap. The box carries its own proportions now, so the height
          // follows from the column's width — and a cap would letterbox it, which
          // is the empty margin the fade was drawn around before.
          className="w-full h-auto select-none cursor-grab active:cursor-grabbing"
          // pan-y, not none: a vertical swipe still scrolls the sidebar the map
          // sits in, so the map cannot trap a phone's scroll. Horizontal drags,
          // which have nothing else to do here, pan.
          style={{ touchAction: "pan-y" }}
          role="img"
          aria-label={`${nodes.length} results arranged by similarity to the query`}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onPointerLeave={handlePointerUp}
          // The blobs clear their own hover on the way out, but a blob that was
          // reordered to the front while hovered can have its leave swallowed —
          // the browser loses the element it was tracking. Leaving the map at all
          // means nothing in it is hovered, whatever the blobs did or did not
          // report, so this is the one that cannot be missed.
          onMouseLeave={() => setHovered(null)}
        >
          <defs>
            {/* One gradient per edge. Opaque against the card's own white where it
                meets the edge, gone by the time it reaches the content. */}
            {[
              ["fade-t", "0", "0", "0", "1"],
              ["fade-b", "0", "1", "0", "0"],
              ["fade-l", "0", "0", "1", "0"],
              ["fade-r", "1", "0", "0", "0"],
            ].map(([id, x1, y1, x2, y2]) => (
              <linearGradient key={id} id={id} x1={x1} y1={y1} x2={x2} y2={y2}>
                <stop
                  offset="0"
                  stopColor="var(--color-surface)"
                  stopOpacity="1"
                />
                <stop
                  offset="1"
                  stopColor="var(--color-surface)"
                  stopOpacity="0"
                />
              </linearGradient>
            ))}
          </defs>

          <g transform={`translate(${pan.x} ${pan.y})`}>
            <MapBackdrop g={g} />

            {/* The results themselves, animated as one group out of the centre.
                The scale it grows from is not: the rings and their bands are the
                ruler the blobs are read against, and a ruler that arrives with
                the measurements reads as part of them. The query block is out of
                it too — it is where the map grows from.

                The origin is set here rather than in the stylesheet because it is
                the map's own centre, which depends on which size is being drawn. */}
            <g
              className="map-bloom"
              key={bloomKey}
              style={{ transformOrigin: `${cx}px ${cy}px` }}
            >
              {/* Spokes: query to result */}
              {nodes.map((n) => (
                <line
                  key={`line-${n.sample.run_id}`}
                  x1={cx}
                  y1={cy}
                  x2={n.x}
                  y2={n.y}
                  className="map-spoke"
                  stroke={
                    hovered === n.sample.run_id
                      ? "var(--color-highlight)"
                      : "var(--color-edge-firm)"
                  }
                  strokeWidth={
                    (hovered === n.sample.run_id ? 1.4 : 0.75) * (g.h / 300)
                  }
                />
              ))}

              {/* The spokes as targets. Where blobs crowd together the one behind
                  cannot be reached, but its spoke arrives at its own angle and is
                  always free — so the line stands in for the blob, for both the
                  hover and the click.

                  Only the outer half of each spoke: near the centre every line
                  converges and whichever was drawn last would win, which is not a
                  choice the reader made. Out by the blob a line belongs to one
                  result unambiguously. Transparent and several units wide, since a
                  hairline is not something a pointer can be expected to find. */}
              {nodes.map((n) => (
                <line
                  key={`hit-${n.sample.run_id}`}
                  x1={cx + (n.x - cx) * 0.5}
                  y1={cy + (n.y - cy) * 0.5}
                  x2={n.x}
                  y2={n.y}
                  stroke="transparent"
                  strokeWidth={g.hitWidth}
                  pointerEvents="stroke"
                  className="cursor-pointer"
                  onMouseEnter={() => setHovered(n.sample.run_id)}
                  onMouseLeave={() =>
                    setHovered((h) => (h === n.sample.run_id ? null : h))
                  }
                  onClick={() => handleNodeClick(n.sample.run_id)}
                />
              ))}

              {/* Result blobs with their three lines of label */}
              {nodes.map((n) => {
                const isHovered = hovered === n.sample.run_id;
                const isFocused = focused === n.sample.run_id;
                // Passing over a blob dims the others and leaves their labels
                // where they are; staying on one takes the labels away, so the
                // blob being studied is the only thing written on the map. An
                // unlabelled blob names itself as soon as it is hovered, which is
                // what makes the quiet ones reachable rather than lost.
                const showLabel = focused ? isFocused : n.labelled || isHovered;
                // The mark that says a blob has a name to give. Pointless once
                // one of them is answering.
                const showEllipsis = !focused && !showLabel;
                const labelX = n.x + n.outward * g.labelGap;
                const anchor = n.outward === 1 ? "start" : "end";
                return (
                  <g
                    key={n.sample.run_id}
                    onMouseEnter={() => setHovered(n.sample.run_id)}
                    // Only clears its own: moving from a spoke onto its blob fires
                    // the leave after the enter, and an unconditional clear would
                    // drop the highlight the blob had just taken.
                    onMouseLeave={() =>
                      setHovered((h) => (h === n.sample.run_id ? null : h))
                    }
                    onClick={() => handleNodeClick(n.sample.run_id)}
                    className="cursor-pointer map-node"
                    opacity={hovered && !isHovered ? 0.45 : 1}
                  >
                    <title>{`${n.sample.run_id}, ${Math.round(n.score * 100)}% similar`}</title>
                    <circle
                      className="map-blob"
                      cx={n.x}
                      cy={n.y}
                      r={isHovered ? g.blobHover : g.blob}
                      fill={
                        isHovered
                          ? "var(--color-highlight)"
                          : "var(--color-highlight-soft)"
                      }
                      stroke="#fff"
                      strokeWidth={1.25 * (g.h / 300)}
                    />
                    {showLabel && (
                      // No key that changes with the state. Keying it on that
                      // remounted a label that was already on screen and standing
                      // still, which played the fade from nothing — the same blink
                      // by another route. The fade is for labels that genuinely
                      // arrive: a quiet blob naming itself, or the set returning
                      // after one of them had the map to itself.
                      <g className="map-label-in">
                        <text
                          x={labelX}
                          y={n.y - g.fontMeta * 0.46}
                          textAnchor={anchor}
                          className="font-mono"
                          fontSize={g.fontId}
                          fontWeight="700"
                          fill="var(--color-ink)"
                        >
                          {n.sample.run_id}
                        </text>
                        <text
                          x={labelX}
                          y={n.y + g.fontMeta * 0.69}
                          textAnchor={anchor}
                          fontSize={g.fontMeta}
                          fill="var(--color-ink-soft)"
                        >
                          {truncate(n.sample.tissue ?? "—", g.tissueChars)}
                        </text>
                        <text
                          x={labelX}
                          y={n.y + g.fontMeta * 1.85}
                          textAnchor={anchor}
                          fontSize={g.fontMeta}
                          fontWeight="600"
                          fill="var(--color-highlight)"
                        >
                          {(n.score * 100).toFixed(1)}%
                        </text>
                      </g>
                    )}
                    {showEllipsis && (
                      // A mark rather than nothing: it says the blob has a name to
                      // give, which an unadorned dot does not.
                      <text
                        x={labelX}
                        y={n.y + g.fontMeta * 0.38}
                        textAnchor={anchor}
                        fontSize={g.fontMeta}
                        fontWeight="700"
                        fill="var(--color-edge-firm)"
                      >
                        …
                      </text>
                    )}
                  </g>
                );
              })}
            </g>

            {/* The query itself. foreignObject so the browser's own line breaking
                and line clamp apply — svg text has neither. */}
            <g>
              <title>{query}</title>
              <rect
                x={cx - g.queryW / 2}
                y={cy - g.queryH / 2}
                width={g.queryW}
                height={g.queryH}
                rx={g.queryW / 8}
                fill="var(--color-brand)"
              />
              <foreignObject
                x={cx - g.queryW / 2}
                y={cy - g.queryH / 2}
                width={g.queryW}
                height={g.queryH}
              >
                <div
                  style={{
                    width: "100%",
                    height: "100%",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    padding: `0 ${g.queryW * 0.09}px`,
                    boxSizing: "border-box",
                  }}
                >
                  <span
                    style={{
                      display: "-webkit-box",
                      WebkitBoxOrient: "vertical",
                      WebkitLineClamp: g.queryLines,
                      overflow: "hidden",
                      textAlign: "center",
                      color: "var(--color-surface)",
                      fontWeight: 700,
                      fontSize: `${g.queryFont}px`,
                      lineHeight: `${g.queryLine}px`,
                      overflowWrap: "break-word",
                    }}
                  >
                    {query}
                  </span>
                </div>
              </foreignObject>
            </g>
          </g>

          {/* The edge fade, outside the panned group so it stays on the box
              rather than travelling with the map. pointer-events none, or it
              would take the hover from every blob it covers. */}
          <g pointerEvents="none">
            <rect x="0" y="0" width={g.w} height={g.fade} fill="url(#fade-t)" />
            <rect
              x="0"
              y={g.h - g.fade}
              width={g.w}
              height={g.fade}
              fill="url(#fade-b)"
            />
            <rect x="0" y="0" width={g.fade} height={g.h} fill="url(#fade-l)" />
            <rect
              x={g.w - g.fade}
              y="0"
              width={g.fade}
              height={g.h}
              fill="url(#fade-r)"
            />
          </g>
        </svg>
      </div>

      <Caption
        mode={mode}
        labelNote={nodes.length > g.labelLimit ? g.labelLimit : null}
      />
    </div>
  );
}
