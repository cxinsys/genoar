"use client";

import { useSyncExternalStore } from "react";
import { BOAT, BIRD, TAIL } from "@/components/logoArt";

/** The header's sea, and the boat that sits on it.
 *
 * The water is drawn twice, once behind the header's content and once in front,
 * with the wordmark and the boat between them — which is what puts the letters
 * in the water rather than beside it. The near wave laps over their feet and the
 * hull as it goes past, so what is submerged changes with the swell.
 *
 * The waterline is the one number the whole drawing agrees on, and it is
 * geometric rather than declared: the mark is MARK_SIZE tall and centred in a
 * 64px row, and at that size the drawing's own waterline lands on the row's y=43
 * where the sea is drawn. Nothing checks that they agree — see MARK_SIZE.
 */

const BAND_H = 64; // the header row's height, so the svg draws at 1:1
const WATERLINE = 43;

/** How far the sea runs either side of the mark.
 *
 * Left, it runs off the page: the header clips it at the window's edge, so what
 * is needed is only enough to reach that edge from the logo on the widest window
 * anyone is likely to have. Everything past it costs a handful of path commands
 * and nothing else.
 *
 * Right, it ends deliberately, because the water is the logo's and not the
 * header's — it should not arrive at the search field. */
const LEFT_RUN = 3000;
const RIGHT_RUN = 364;

/** Band coordinate of the mark's left edge. The whole drawing is placed from
    here, and it is where a phase of zero is taken from. */
const ORIGIN = LEFT_RUN;
const BAND_W = LEFT_RUN + RIGHT_RUN;

interface Wave {
  /** Half a repeat. The distance the layer travels by is twice this. */
  half: number;
  amp: number;
  baseline: number;
  dur: number;
  /** How much navy the body is, or null for the pale veil the near water uses. */
  fill: number | null;
  /** The surface itself. Every layer gets one, fainter the further away it is:
      a line is what makes a body of water read as a surface seen edge-on rather
      than as a shape. */
  stroke: number;
  strokeWidth: number;
}

/** The far water, which only has to look like distance. Speed is what says how
    far away it is — more than colour and much more than height. */
const FAR: Wave = {
  half: 95,
  amp: 4,
  baseline: WATERLINE + 2,
  dur: 17,
  fill: 0.07,
  stroke: 0.06,
  strokeWidth: 1,
};

/** The body of the sea, and the one everything floats to. */
const MID: Wave = {
  half: 75,
  amp: 5.5,
  baseline: WATERLINE,
  dur: 10.5,
  fill: 0.13,
  stroke: 0.09,
  strokeWidth: 1.2,
};

/** The water in front of the header's content, drawn as a veil rather than as
    ink so that it pales what it passes over instead of darkening it. */
const NEAR: Wave = {
  half: 59,
  amp: 5,
  baseline: WATERLINE + 1,
  dur: 7.5,
  fill: null,
  stroke: 0.14,
  strokeWidth: 1.4,
};

/** How strongly the water is inked, as a multiplier on every layer at once.
 *
 * The opacities above are fractions of the sea's colour, and they were chosen
 * against a white bar. On a near-black one the same fractions leave almost
 * nothing: seven percent of a colour over #120e2b is a rumour of a wave. So a
 * palette can turn them all up together, and it is one number rather than eight
 * because what is wanted is the same water further forward, not a different
 * arrangement of layers.
 *
 * A calc() rather than a second set of constants, so the relationships between
 * near, middle and far — which is what makes the sea read as depth — survive
 * whatever a palette does to the volume. */

/** Which water the boat and the wordmark ride.
 *
 * The near wave is physically the one they sit on, but it is drawn as a veil and
 * a hairline and so is barely visible; a rhythm taken from water nobody can see
 * is a rhythm nobody can see the sense of. The middle layer is the one that
 * reads as the sea, so it is the one that sets the beat. */
const PACE = MID;

/** How long a floating thing takes to go round once.
 *
 * Not a number chosen for how it looks. A crest passes any fixed point once per
 * repeat of the pacing wave, so anything bobbing on a different period is
 * bobbing to water that is not there. */
export const SEA_PERIOD = `${PACE.dur}s`;

/** Where in that cycle something standing `x` px right of the mark's left edge
    is. The water travels leftward, so a point further right meets each crest
    sooner and runs that much ahead — written as a negative delay, which starts
    an animation already in progress.
 *
 * `lag` puts it back: water first, then the hull it lifts, then the passenger in
 * the hull. */
export function bobDelay(x: number, lag = 0): string {
  const lead = ((x + ORIGIN) / (2 * PACE.half)) * PACE.dur;
  // Folded back into a single cycle. A phase is only ever meaningful modulo the
  // period, and ORIGIN is far enough out to sea that the raw figure runs to
  // hundreds of seconds — correct, and unreadable in the inspector.
  return `${(lag - (lead % PACE.dur)).toFixed(2)}s`;
}

/** A run of waves as an open curve.
 *
 * Drawn from repeated half-periods, because `t` reflects the previous control
 * point and so alternates crest and trough on its own — one command per half of
 * a wave rather than one per curve of it.
 *
 * It is a whole repeat wider than the band it fills, which is what lets it
 * travel without a seam: slid left by exactly that much, the crest leaving at
 * the left is the crest arriving at the right, because they are the same part of
 * the same repeat. */
function waveCurve(baseline: number, amp: number, half: number, cover: number) {
  const halves = Math.ceil(cover / half);
  let d = `M0 ${baseline} q${half / 2} ${-amp} ${half} 0`;
  for (let i = 1; i < halves; i++) d += ` t${half} 0`;
  return { d, width: halves * half };
}

/** The same run closed off against the bottom of the band, as a body of water
    rather than a surface. */
function waveBody(baseline: number, amp: number, half: number, cover: number) {
  const { d, width } = waveCurve(baseline, amp, half, cover);
  return `${d} L${width} ${BAND_H} L0 ${BAND_H} Z`;
}

/** A gull: a 3 laid on its side, which is every gull anyone has ever drawn.
 *
 * The two ends of a beat, and the curve is interpolated between them. They must
 * be written with the same commands in the same order — the same M and the same
 * two Qs — because what is interpolated is the numbers, one against its
 * counterpart. Change one of them to a C and the shape stops being a shape the
 * other can be turned into.
 *
 * 18 wide, the body at (9,6), the wingtips at either end. The down-stroke is the
 * up-stroke reflected in the line through the body: every number the same
 * distance from 6 and on the other side of it. So the curves turn right over —
 * arches above the body become arches below it — and the two halves of the beat
 * carry the same amount of shape, which is why neither end of it looks like the
 * bird resting. Push both controls further from 6 to beat harder. */
const WING_UP = "M0 7Q4.5 1 9 6Q13.5 1 18 7";
const WING_DOWN = "M0 5Q4.5 11 9 6Q13.5 11 18 5";

/** Where the three of them sit, out to the left of the boat and above it, with
 *  their own sizes and their own wingbeats — three birds, not one bird drawn
 *  three times.
 *
 *  x is measured back from the mark, so the furthest needs 124px of open header
 *  to its left. That is why they only appear past a certain window width: below
 *  it the page's own inset is all the room there is beside the logo, and a gull
 *  would be half a gull. */
const GULLS = [
  { x: -51, y: 11, scale: 1, opacity: 0.34, flap: 0.72, drift: 6.5 },
  { x: -27, y: 22, scale: 0.72, opacity: 0.26, flap: 0.62, drift: 5.2 },
  { x: -71, y: 27, scale: 0.55, opacity: 0.2, flap: 0.85, drift: 7.4 },
];

/** The width at which there is room for them: the content stops widening at
    1600, so everything past that is gutter, and 1700 is comfortably past where
    the gutter and the page inset together clear the furthest gull's 71px. */
const GULL_MIN_WIDTH = "min-[1700px]:block";

/** Whether the reader has asked for less movement.
 *
 * Read here rather than left to the stylesheet because SVG's <animate> is not a
 * CSS animation and a media query cannot reach it — the only way to honour the
 * preference is not to render the element. Everything else on this page is
 * turned off from globals.css, where it belongs.
 *
 * The server has no preference to read, so it answers "no" and the first client
 * render agrees with it; the real answer arrives a moment later and the gulls
 * settle. Guessing "yes" instead would spare that at the cost of a still header
 * for the first frame of everyone else's visit.
 */
const REDUCE_MOTION = "(prefers-reduced-motion: reduce)";

function useStillness(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const query = window.matchMedia(REDUCE_MOTION);
      query.addEventListener("change", onChange);
      return () => query.removeEventListener("change", onChange);
    },
    () => window.matchMedia(REDUCE_MOTION).matches,
    () => false,
  );
}

export type SeaLayer = "behind" | "front";

/** One band of water.
 *
 * Rendered twice in the header, once on each side of its content. Both are
 * positioned and neither carries a z-index: document order is what decides, and
 * the header itself is left unclipped so the tooltips that hang out of its
 * bottom edge still show.
 */
export function HeaderSea({ layer }: { layer: SeaLayer }) {
  // Ids rather than a CSS mask. A gradient mask declared in the stylesheet is
  // the shorter way to write this and it does not take here; an SVG mask is
  // self-contained and cannot be missed. The header renders once per page, so
  // fixed ids are safe.
  const fadeId = `sea-fade-${layer}`;
  const maskId = `sea-mask-${layer}`;
  const veilId = "sea-veil";
  const stillness = useStillness();

  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-0 overflow-hidden"
    >
      {/* Two boxes, and the inner one carries the `relative`. An absolutely
          positioned child is laid out against its containing block's padding
          box, not its content box, so hanging the water off the padded element
          itself put it a whole page inset further left than LOGO_X said — and by
          a different amount at every breakpoint, which is why trimming LOGO_X
          kept not showing. Against the inner box, x=0 is where the logo is. */}
      <div className="mx-auto h-full max-w-[1600px] px-4 sm:px-6 lg:px-8">
        <div className="relative h-full">
          <svg
            className="absolute inset-y-0 left-0 block text-sea"
            style={{ marginLeft: -ORIGIN }}
            width={BAND_W}
            height={BAND_H}
            viewBox={`0 0 ${BAND_W} ${BAND_H}`}
            fill="none"
          >
            <defs>
              {/* Only the far end fades. Open water that stops at a vertical
                line is the one thing that gives the drawing away, and at the
                near end nothing has to be done about it: the water simply runs
                off the page and the header cuts it at the window's edge.

                Measured in the drawing's own units from the mark rather than as
                a fraction of the band, so that lengthening the run to the left
                does not move where the fade to the right happens. */}
              <linearGradient
                id={fadeId}
                gradientUnits="userSpaceOnUse"
                x1={ORIGIN}
                y1="0"
                x2={ORIGIN + 300}
                y2="0"
              >
                <stop offset="0" stopColor="#fff" stopOpacity="1" />
                <stop offset="0.43" stopColor="#fff" stopOpacity="1" />
                <stop offset="1" stopColor="#fff" stopOpacity="0" />
              </linearGradient>

              {/* The near water is a veil, not ink. Against the header's white it
                is very nearly nothing; against the navy of a letter or a hull it
                pales what is under it, which is what being underwater looks like
                from above. Painting it as more navy instead would darken the
                letters, and a letter is not darker for being submerged.

                It thins out downward rather than filling to the bottom of the
                band, so it veils the feet of the letters and then lets the water
                behind it show through. It starts above the highest crest so that
                the whole thickness of the wave is veiled evenly — begun at the
                mean waterline instead, a crest would rise out of its own veil. */}
              <linearGradient
                id={veilId}
                gradientUnits="userSpaceOnUse"
                x1="0"
                y1={NEAR.baseline - NEAR.amp}
                x2="0"
                y2={NEAR.baseline + 16}
              >
                <stop
                  offset="0"
                  stopColor="var(--color-surface)"
                  stopOpacity="0.82"
                />
                <stop
                  offset="1"
                  stopColor="var(--color-surface)"
                  stopOpacity="0"
                />
              </linearGradient>

              <mask id={maskId}>
                <rect width={BAND_W} height={BAND_H} fill={`url(#${fadeId})`} />
              </mask>
            </defs>

            {/* Sky, so outside the mask that fades the water — and left of where
              that fade begins in any case. */}
            {layer === "behind" &&
              GULLS.map((g) => (
                <g
                  key={g.x}
                  className={`hidden ${GULL_MIN_WIDTH}`}
                  transform={`translate(${ORIGIN + g.x} ${g.y}) scale(${g.scale})`}
                >
                  {/* The wander and the wingbeat are on separate elements: a CSS
                    transform replaces the transform attribute rather than
                    composing with it, so an animated element cannot also be the
                    one that carries the bird's position. */}
                  <g
                    className="gull-drift"
                    style={{
                      animationDuration: `${g.drift}s`,
                      // Three weights by default, one if a palette says so.
                      // The three are distance: the nearest bird is the darkest
                      // and the furthest nearly gone, which is what makes them
                      // sit at different depths in the same sky. A palette that
                      // is drawing rather than depicting wants none of that —
                      // one pen, one weight — and --gull-ink is how it says so
                      // without this file having to know which palette that is.
                      opacity:
                        `var(--gull-ink, ` +
                        `calc(${g.opacity} * var(--sea-ink, 1)))`,
                    }}
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={1.4}
                    strokeLinecap="round"
                  >
                    <path d={WING_UP}>
                      {/* SVG's own animation, because this is the one thing CSS
                        cannot do here: `d` is animatable as a CSS property in
                        Chrome and Safari but not in Firefox, while <animate> on
                        the attribute has worked in all three for twenty years.
                        No library, and the browser interpolates the curve.

                        Nothing gives the three of them a phase offset — their
                        beats are simply different lengths, so they wander in and
                        out of step on their own, which is what birds do. */}
                      {!stillness && (
                        <animate
                          attributeName="d"
                          values={`${WING_UP};${WING_DOWN};${WING_UP}`}
                          dur={`${g.flap}s`}
                          repeatCount="indefinite"
                          calcMode="spline"
                          keyTimes="0;0.5;1"
                          keySplines="0.4 0 0.6 1;0.4 0 0.6 1"
                        />
                      )}
                    </path>
                  </g>
                </g>
              ))}

            {/* The mask sits on the parent, so it stays put while the waves travel
              underneath it rather than travelling with them. */}
            <g mask={`url(#${maskId})`}>
              {(layer === "behind" ? [FAR, MID] : [NEAR]).map((w) => (
                // Body and surface line travel as one group, so the line is always
                // the top edge of the water it belongs to.
                <g
                  key={w.half}
                  className="sea-drift"
                  style={
                    {
                      "--sea-tile": `${-2 * w.half}px`,
                      "--sea-dur": `${w.dur}s`,
                    } as React.CSSProperties
                  }
                >
                  <path
                    d={waveBody(w.baseline, w.amp, w.half, BAND_W + 2 * w.half)}
                    fill={w.fill === null ? `url(#${veilId})` : "currentColor"}
                    style={
                      w.fill === null
                        ? undefined
                        : { opacity: `calc(${w.fill} * var(--sea-ink, 1))` }
                    }
                  />
                  <path
                    d={
                      waveCurve(w.baseline, w.amp, w.half, BAND_W + 2 * w.half)
                        .d
                    }
                    fill="none"
                    stroke="currentColor"
                    strokeWidth={w.strokeWidth}
                    style={{ opacity: `calc(${w.stroke} * var(--sea-ink, 1))` }}
                  />
                </g>
              ))}
            </g>
          </svg>
        </div>
      </div>
    </div>
  );
}

/** The drawing's own box.
 *
 * The export placed everything inside a group translated by (32.17, 218.69),
 * which would have sat between each moving part and the coordinates its pivot is
 * written in. Rather than fold that offset into three thousand path numbers it
 * is folded into the viewBox: give the box that translation as its negative
 * origin and the paths land where they landed in Inkscape, untouched, so a fresh
 * export can be dropped in whole.
 *
 * What it buys is that `transform-box: view-box` measures from the box's own
 * top-left corner, which makes the pivots in globals.css exactly the numbers
 * measured off the artwork — no arithmetic between the two. */
const ART_BOX = "-32.167498 -218.69074 380.93225 360.92874";
const ART_WIDTH = 380.93225;

/** The mark's rendered size.
 *
 * Not quite a free choice. The drawing's own waterline lands on the sea's at
 * almost exactly 35px, and drifts about a third of a pixel for every 1px away
 * from that — at 34 it rides a third of a pixel high, which is nothing, and at
 * 24 it would ride three pixels high, which is a boat hovering. So this can be
 * nudged but not swung, and if it ever needs to move far the mark wants a
 * translate to go with it.
 *
 * The wordmark's phase is measured from here too, so this and the gap after it
 * are written here rather than in the header. */
export const MARK_SIZE = 34;
export const MARK_GAP = 8;

/** One screen pixel, in the artwork's units.
 *
 * A length inside an SVG transform is in user units, and the mark's box is 380
 * of them drawn 36 pixels wide — so the rise the letters are given would be a
 * quarter of a pixel if it were handed to the hull unconverted. It is the only
 * number the mark has to translate; degrees are degrees at any scale. */
const ART_UNIT = ART_WIDTH / MARK_SIZE;

/** How far the hull lifts, in screen pixels before conversion — the same rise
    the wordmark is given, because they are on the same water. */
const HULL_RISE = 2.6;

/** The tail swings on a third of the sea's period rather than on a number of its
 *  own. A tail flicking once every ten seconds does not read as a flick, but a
 *  period merely chosen to look lively would beat against the swell. An exact
 *  third is three flicks to the wave's one and never drifts out of step. */
const TAIL_PERIOD = `${(PACE.dur / 3).toFixed(2)}s`;

/** Where the tail joins the body, and how far it swings.
 *
 * The pivot is the midpoint of the open path's two ends, which is where it was
 * cut from the bird, and it checks out against the drawing: it sits on the flat
 * edge where the tail's root meets the rump.
 *
 * It swings further down than up, because up is where the rump and the wing
 * already are and the tail arrives at them before it has gone anywhere.
 *
 * The sum of the two is a ceiling rather than a taste. The root is a straight cut
 * about twenty units long, so turning it about the middle of that cut swings
 * either end by ten units times twice the sine of the angle; past the mid-to-high
 * teens in either direction the far end of the cut clears the body's outline and
 * a white wedge opens between tail and rump. Wider than this wants the artwork
 * changed rather than the numbers: extend the tail's root further into the body
 * and there is overlap to spend. */
/* Local coordinates, not the ones the pivot was measured in.
 *
 * rotate(a cx cy) takes its centre in the element's own user space, and this
 * drawing's user space is Inkscape's — the offset that would have converted one
 * to the other went into the viewBox instead, so a path's user-space coordinate
 * is the number in its own `d`. Handed the measured pair, the tail turned about
 * a point off below the boat and swung bodily rather than wagging.
 *
 * CSS's transform-origin is the other way round: `transform-box: view-box`
 * measures from the box's top-left corner, so the boat's and the bird's pivots
 * in globals.css really are the measured numbers. The two conventions differ,
 * which is the whole of the bug. */
const TAIL_PIVOT = `${(234 - 32.167498).toFixed(2)} ${(208 - 218.69074).toFixed(2)}`;
const TAIL_UP = 8;
const TAIL_DOWN = 18;

/** The swing as a rotation about that point, written out for SVG's own
 *  animateTransform rather than as a CSS rotation.
 *
 * CSS would need `transform-box` to say what the pivot is measured against, and
 * inside two ancestors that are themselves turning, what it is measured against
 * is exactly the question — at the bird's two degrees the ambiguity is invisible
 * and at the tail's twenty it is the tail sliding out of its own socket.
 * `rotate(angle cx cy)` takes the centre in the element's own coordinates and
 * leaves nothing to interpret. */
const TAIL_VALUES = [-TAIL_UP, TAIL_DOWN, -TAIL_UP]
  .map((deg) => `${deg} ${TAIL_PIVOT}`)
  .join(";");

/** The bird is painted in two coats. This is the first: yellow at the crest
 *  falling to the orange the rest of it stays, which is what the drawing it came
 *  from does. The cool colour is the second coat, below.
 *
 * The axis runs left to right and tilts slightly down, because that is how the
 * bird is laid out — crest and beak on the left, body and paws through the
 * middle. Yellow at the top-left and orange along the bottom fall out of that on
 * their own: the bottom of the drawing *is* its horizontal middle.
 *
 * The orange is held from 0.40 all the way out rather than passed through.
 * Without the hold the body is a moment on the way somewhere; with it, orange is
 * a colour the bird is.
 *
 * userSpaceOnUse rather than the default: the endpoints are the bird's own
 * bounding box in x, measured off the path data, and saying so in the drawing's
 * coordinates keeps them checkable against it. Being in user space the gradient
 * is carried by the lean, which is the right way round — plumage is coloured on
 * the bird, not on the screen behind it.
 *
 * The tail is painted flat orange and sits outside both coats. It is a separate
 * path, and it is entirely inside the stretch where the orange is held, so on
 * this gradient it was one flat colour already — flattening it costs nothing and
 * keeps the cool coat off it.
 *
 * Both coats step their colour changes out by hand, as color-mix in oklab.
 * An SVG gradient interpolates its stops in sRGB, and the straight line from
 * orange to blue-green passes within a hair of neutral — the halfway colour is
 * #90a06b, a dull olive that painted a dirty band across the wing. oklab mixed
 * rectangularly goes through a soft desaturated gold instead: a warm colour
 * cooling, which is what the feathers are doing. Round the oklch hue circle is
 * the other option and it keeps chroma up the whole way, which gives a saturated
 * green streak — correct by the numbers, a parrot on the page.
 *
 * color-mix and not computed hex values, because the endpoints are tokens. In
 * the dark all of them are the brand blue, every mix between them is that same
 * blue, and the mark collapses back to one flat silhouette on its own. Baked
 * colours would have left a gold wing on a dark page. (color-mix is Baseline
 * 2023 and Tailwind v4 already asks for newer browsers than that.) */
const PLUME_ID = "mark-plume";
const PLUME_FROM = [85.6, -215] as const;
const PLUME_TO = [335.5, -135] as const;
const PLUME_STOPS: [number, string][] = [
  [0, "var(--color-mark-plume)"],
  [0.4, "var(--color-mark-plume-deep)"],
  [1, "var(--color-mark-plume-deep)"],
];

/** The second coat: the same axis again, but carrying on past the orange into
 *  the cool blue-green, and clipped so that only the wing receives it.
 *
 * The axis is not chosen twice. Its direction is what puts the colour change
 * across the wing at the angle the feathers fan at — steep, leaning right,
 * within a few degrees of the line the tips lie on — and nothing else tried
 * came close. A circle centred past the wingtip confines the colour just as
 * well but its contours arc the wrong way, and what you see is a shallow band
 * running down to the right across a wing that points up to the right.
 *
 * What the axis cannot do is tell a wingtip from the rump. Measured off a
 * render, the rump reaches 0.72 along it and the wing runs 0.68 to 0.78 — the
 * two are interleaved, so any stop that reaches the tips also greens the bird's
 * backside. That is not a defect of these particular numbers. Nothing about a
 * direction knows which part of a drawing is a wing.
 *
 * So the wing is separated the only way that is actually true to the drawing:
 * by cutting it out. Everything above CUT is painted with this coat, everything
 * below keeps the plain warm one, and the rump is below.
 *
 * The cut is a straight line and it is invisible, which took choosing rather
 * than luck. Between x=190 and the wing's outer tip it passes through the white
 * gap that the illustration already draws between the lowest feather and the
 * haunch, so there is nothing there to cut. Left of that the wing and the body
 * are one mass of ink and the line does cross it — at x=180, x=170, x=150, all
 * the way to the edge — but every one of those crossings is at less than 0.56
 * along the axis, and below 0.56 this coat and the warm one beneath it are the
 * same colours at the same offsets. The line is drawn where the two coats agree.
 *
 * An extra layer cut to the wing, and the same path rather than a second copy
 * of the artwork: nothing to redraw, and nothing to keep in step with the bird's
 * lean, because it *is* the bird. */
/** The mark as one shape, for a palette that paints it that way.
 *
 * A single run across the whole drawing rather than a colour per part: boat,
 * bird and tail all take this, so what the reader sees is one thing lit from the
 * top left rather than three things that happen to be touching. Which parts take
 * it is decided in the stylesheet — see --mark-fill-* — so nothing here has to
 * know which palette is showing.
 *
 * The axis is the artwork's own diagonal, corner to corner, which is why it is
 * written from the box rather than from the ink: the boat runs the full width
 * and the bird the full height, so between them they fill it.
 *
 * Two colours and not three. Running the warm end through the orange on its way
 * to the blue-green makes the mark read as orange-to-mint rather than as
 * yellow-to-teal, because the middle of a gradient is where a gradient is
 * looked at: the ends are corners and the orange is the whole boat.
 *
 * The warm end is given about a third of the run and the cool end the rest. It
 * is meant to be light catching one edge of a shape, and light on an edge is not
 * half the shape. Evenly split, it reads as two logos meeting in the middle.
 *
 * The oklab steps are the same ones the wingtip coat uses, for the same reason —
 * yellow to blue-green in a straight sRGB line goes through a flat green. */
const WHOLE_ID = "mark-whole";
const WHOLE_FROM = [-32.17, -218.69] as const;
const WHOLE_TO = [348.77, 142.24] as const;

/** Where the warm stops being held and where the cool has fully arrived. The
 *  midpoint of what is between them lands near a third of the way along, which
 *  is the rim of light this is supposed to be. */
const WHOLE_WARM_UNTIL = 0.1;
const WHOLE_COOL_FROM = 0.55;

const WHOLE_STOPS: [number, string][] = [
  [0, "var(--color-mark-plume)"],
  [WHOLE_WARM_UNTIL, "var(--color-mark-plume)"],
  ...Array.from({ length: 5 }, (_, i): [number, string] => {
    const t = (i + 1) / 6;
    const offset = WHOLE_WARM_UNTIL + t * (WHOLE_COOL_FROM - WHOLE_WARM_UNTIL);
    return [
      Number(offset.toFixed(3)),
      `color-mix(in oklab, var(--color-mark-plume), var(--color-mark-plume-cool) ${Math.round(t * 100)}%)`,
    ];
  }),
  [WHOLE_COOL_FROM, "var(--color-mark-plume-cool)"],
  [1, "var(--color-mark-plume-cool)"],
];

const WING_ID = "mark-wing";

/** A point the cut passes through, and how fast it falls to the right. */
const CUT_THROUGH = [190, -36.5] as const;
const CUT_SLOPE = 0.42;

/** Far enough out that the polygon covers the drawing whatever it is doing. */
const CUT_REACH = 900;
const cutAt = (x: number) => CUT_THROUGH[1] + CUT_SLOPE * (x - CUT_THROUGH[0]);
const WING_CLIP = [
  [-CUT_REACH, -CUT_REACH],
  [CUT_REACH, -CUT_REACH],
  [CUT_REACH, cutAt(CUT_REACH)],
  [-CUT_REACH, cutAt(-CUT_REACH)],
]
  .map(([x, y]) => `${x},${y.toFixed(1)}`)
  .join(" ");

/** Where the orange stops being held and where the cool has fully arrived. */
const WING_WARM_UNTIL = 0.56;
const WING_COOL_FROM = 0.82;

/** Five, which is enough: the steps land about a pixel apart at the size this is
 *  drawn, and past that the browser is interpolating inside one pixel and the
 *  space it does that in stops mattering. */
const BRIDGE_STEPS = 5;

const WING_STOPS: [number, string][] = [
  ...PLUME_STOPS.slice(0, 2),
  [WING_WARM_UNTIL, "var(--color-mark-plume-deep)"],
  ...Array.from({ length: BRIDGE_STEPS }, (_, i): [number, string] => {
    const t = (i + 1) / (BRIDGE_STEPS + 1);
    const offset = WING_WARM_UNTIL + t * (WING_COOL_FROM - WING_WARM_UNTIL);
    return [
      Number(offset.toFixed(3)),
      `color-mix(in oklab, var(--color-mark-plume-deep), var(--color-mark-plume-cool) ${Math.round(t * 100)}%)`,
    ];
  }),
  [WING_COOL_FROM, "var(--color-mark-plume-cool)"],
  [1, "var(--color-mark-plume-cool)"],
];

/** The mark: a boat riding the swell, a bird in it that rides it less, and a
 *  tail on the bird that rides it more.
 *
 * Each is nested inside the last rather than sitting beside it, so each is
 * carried by the one above and only has to describe the difference — the bird a
 * small counter-turn, late, which is a passenger keeping their balance; the tail
 * a wider one, later and three times as often. As siblings each would have to
 * restate every motion above it, and they would have to be kept in step by hand.
 *
 * The pivots are the three places the drawing hinges: where the hull meets the
 * water, where the bird sits in the hull, and where the tail meets the bird.
 * The last is not a guess: the tail is an open path, and the two ends of an
 * open path are where it was cut from the body.
 */
export function BoatMark() {
  const stillness = useStillness();

  return (
    <svg
      aria-hidden
      // The drawing fills its box to all four edges, so anything that moves it
      // leaves that box — and an svg clips to its viewport by default, which cut
      // the hull off square against nothing at the ends of every roll. Nothing
      // above this clips either, so the couple of pixels it strays are simply
      // drawn.
      className="block h-full w-full overflow-visible"
      viewBox={ART_BOX}
      xmlns="http://www.w3.org/2000/svg"
    >
      <defs>
        <linearGradient
          id={PLUME_ID}
          gradientUnits="userSpaceOnUse"
          x1={PLUME_FROM[0]}
          y1={PLUME_FROM[1]}
          x2={PLUME_TO[0]}
          y2={PLUME_TO[1]}
        >
          {PLUME_STOPS.map(([offset, color]) => (
            <stop key={offset} offset={offset} stopColor={color} />
          ))}
        </linearGradient>
        <linearGradient
          id={WING_ID}
          gradientUnits="userSpaceOnUse"
          x1={PLUME_FROM[0]}
          y1={PLUME_FROM[1]}
          x2={PLUME_TO[0]}
          y2={PLUME_TO[1]}
        >
          {WING_STOPS.map(([offset, color]) => (
            <stop key={offset} offset={offset} stopColor={color} />
          ))}
        </linearGradient>
        <linearGradient
          id={WHOLE_ID}
          gradientUnits="userSpaceOnUse"
          x1={WHOLE_FROM[0]}
          y1={WHOLE_FROM[1]}
          x2={WHOLE_TO[0]}
          y2={WHOLE_TO[1]}
        >
          {WHOLE_STOPS.map(([offset, color]) => (
            <stop key={offset} offset={offset} stopColor={color} />
          ))}
        </linearGradient>
        <clipPath id={`${WING_ID}-clip`}>
          <polygon points={WING_CLIP} />
        </clipPath>
      </defs>
      <g
        className="boat-float"
        style={
          {
            animationDuration: SEA_PERIOD,
            animationDelay: bobDelay(MARK_SIZE / 2, 0.35),
            "--bob-rise": `${(HULL_RISE * ART_UNIT).toFixed(1)}px`,
          } as React.CSSProperties
        }
      >
        <path d={BOAT} fill="var(--mark-fill-boat)" />
        <g
          className="bird-lean"
          style={{
            animationDuration: SEA_PERIOD,
            animationDelay: bobDelay(MARK_SIZE / 2, 0.7),
          }}
        >
          <path d={BIRD} fill="var(--mark-fill-bird)" />
          <g clipPath={`url(#${WING_ID}-clip)`}>
            <path d={BIRD} fill="var(--mark-fill-wing)" />
          </g>
          <g>
            {!stillness && (
              <animateTransform
                attributeName="transform"
                type="rotate"
                values={TAIL_VALUES}
                dur={TAIL_PERIOD}
                repeatCount="indefinite"
                calcMode="spline"
                keyTimes="0;0.5;1"
                keySplines="0.4 0 0.6 1;0.4 0 0.6 1"
              />
            )}
            <path d={TAIL} fill="var(--mark-fill-tail)" />
          </g>
        </g>
      </g>
    </svg>
  );
}
