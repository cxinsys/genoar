/** Shafts of sun falling through the header bar.
 *
 * Five of them, and five elements rather than one drawing, because each has to
 * sway and breathe on its own clock. A single SVG with five paths would have to
 * animate five children anyway, and these are rectangles with a gradient in
 * them — there is no shape here that needs a path.
 *
 * Rendered on every palette and visible on one. Nothing is drawn unless the
 * stylesheet has been given --ray-ink, so no component has to ask which theme is
 * showing; see .header-rays in globals.css.
 */

/** The header row's own box, repeated verbatim.
 *
 * The three shafts by the logo are measured from the logo, and the logo is not
 * at the left of the window — it is at the left of a column capped at 1600 and
 * centred, so on a wide display it starts eleven hundred pixels in. Positioned
 * against the window they were nowhere near the thing they were meant to be
 * falling on, and the wider the display the further away.
 *
 * The two at the far end are measured from the window, and that is not an
 * oversight: the bar runs the full width, the far end of it is the far end of
 * the window, and past the column cap there is nothing there but sky. They are
 * also the two that go when there is no room — see .header-rays in globals.css.
 *
 * The inset shrinks with the window (px-4, then px-6, then px-8), so an offset
 * that reaches left of the mark reaches further past the window edge the
 * narrower it gets. The leftmost shaft was simply being cut off. The stylesheet
 * gathers them instead. */
const TRACK = "max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 h-full";

/** Where each shaft crosses the bar, and how it moves while it does.
 *
 * `at` is measured from the mark's left edge for the three by the logo, and from
 * the window's right edge for the two at the end. The mark is 34 across and the
 * word about 105 after it, so the third of them at 168 clears the R by roughly
 * twenty pixels once the lean is counted — a shaft beside the word rather than
 * across it. The first is a little to the left of the mark rather than on it,
 * which is what puts light on the edge of a thing instead of over it.
 *
 * They are paler at the far end. The sun on this palette is a wash of yellow at
 * the left edge of the header, and light that is as strong four hundred pixels
 * away from its source as it is beside it does not read as coming from
 * anywhere.
 *
 * The numbers are deliberately not round and deliberately not multiples of each
 * other. Nothing here is randomised at runtime — a random number would differ
 * between the server's render and the browser's and React would complain, and it
 * would also mean the header looked different every time you loaded it for no
 * reason anyone asked for. Instead the periods are chosen to be mutually
 * awkward: 13 against 17 against 21 comes back into step once every 4600
 * seconds, which is long enough that the five of them never appear to be doing
 * the same thing. The negative delays start each one part-way through its own
 * cycle, so they are already scattered on the first frame rather than all
 * setting off together from the same place.
 */
const RAYS = [
  // By the logo: across the mark, across the word, and clear of its far end.
  {
    side: "left",
    at: -34,
    width: 34,
    tilt: 24,
    low: 0.38,
    peak: 1,
    sway: 13,
    throw: 8,
    pulse: 9,
    delay: -3.4,
  },
  {
    side: "left",
    at: 28,
    width: 17,
    tilt: 30,
    low: 0.3,
    peak: 0.82,
    sway: 17,
    throw: 5,
    pulse: 11,
    delay: -7.1,
  },
  {
    side: "left",
    at: 168,
    width: 25,
    tilt: 21,
    low: 0.35,
    peak: 0.92,
    sway: 21,
    throw: 10,
    pulse: 14,
    delay: -1.2,
  },
  // The far end.
  {
    side: "right",
    at: 52,
    width: 26,
    tilt: 27,
    low: 0.27,
    peak: 0.67,
    sway: 19,
    throw: 9,
    pulse: 12,
    delay: -5.6,
  },
  {
    side: "right",
    at: 118,
    width: 15,
    tilt: 33,
    low: 0.22,
    peak: 0.55,
    sway: 15,
    throw: 6,
    pulse: 10,
    delay: -9.3,
  },
] as const;

type Ray = (typeof RAYS)[number];

function shaft(r: Ray) {
  /* Built as a plain record and asserted once, rather than as a literal with a
     branch spread into it: custom properties are not part of CSSProperties, and
     a spread of two shapes leaves a union that the assertion will not take. */
  const style: Record<string, string | number> = {
    width: `${r.width}px`,
    "--ray-tilt": `${r.tilt}deg`,
    "--ray-low": r.low,
    "--ray-peak": r.peak,
    "--ray-sway": `${r.sway}s`,
    "--ray-throw": `${r.throw}px`,
    "--ray-pulse": `${r.pulse}s`,
    "--ray-delay": `${r.delay}s`,
  };

  /* The ones by the logo are placed by the stylesheet from a plain number, so
     that a narrow window can gather them without this file knowing what a
     breakpoint is. The two at the far end keep their own distance from an edge
     that does not move. */
  if (r.side === "left") style["--ray-at"] = r.at;
  else style.right = `${r.at}px`;

  return (
    <span
      key={`${r.side}-${r.at}`}
      className="header-ray"
      style={style as React.CSSProperties}
    />
  );
}

export default function HeaderRays() {
  return (
    <div aria-hidden className="header-rays">
      {RAYS.filter((r) => r.side === "right").map(shaft)}
      {/* Two boxes to reach the content edge: the outer one is the column, and
          an absolute child of it would be placed against its padding box —
          which is the outside of the inset, not the inside. The inner one is
          where the row's contents actually begin. */}
      <div className={TRACK}>
        <div className="header-rays-track relative h-full">
          {RAYS.filter((r) => r.side === "left").map(shaft)}
        </div>
      </div>
    </div>
  );
}
