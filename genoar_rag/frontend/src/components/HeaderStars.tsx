/** A band of the Milky Way across the header, and the stars in it.
 *
 * It goes in the one part of the bar that carries nothing: the gap between the
 * wordmark and the search field. That gap is where it has to be rather than
 * where it looks good — a sky drawn under text is a texture behind a label, and
 * the whole point of this one is that it is the only thing there.
 *
 * Rendered on every palette and visible on one. Nothing is drawn unless the
 * stylesheet has been given --galaxy-ink and --star-ink, so no component has to
 * ask which theme is showing; see .header-stars in globals.css. The same
 * arrangement as the shafts of sun next door.
 */

/** The band, in the coordinates of the header's own content box.
 *
 * The box is capped at 1600 and centred, and .header-stars is given the same cap
 * and the same centring — so these numbers are measured from the left edge of
 * the content rather than of the window, and they mean the same thing at any
 * width past the one below. That is what lets the mark's edge be a constant: the
 * mark starts at the padding and the padding does not move.
 *
 * The centre is put at 360 and the length at 420, which reaches from about 150
 * to about 570. The right half of that is under the search field on a narrower
 * window, and it is the stylesheet that trims it — the band is cut to the gap by
 * a mask whose right edge is measured from the middle of the bar, where the
 * field is. Drawing it long and cutting it back is what lets one set of
 * coordinates serve every width.
 *
 * It crosses into the field, which is wanted: a galaxy that stopped dead at the
 * edge of a text box would be a shape with a straight side. If this moves, the
 * mask's right edge has to move with it by the same amount — otherwise the band
 * slides about inside its own window instead of the two travelling together.
 *
 * Nine degrees, rising to the right. It is a shallow angle because the bar is
 * 64px tall and 400 wide: anything steeper leaves through the top before it has
 * crossed, and what reads as a diagonal in a letterbox is not the same number
 * that reads as one in a square. */
const BAND_TILT = 9;
const BAND_CX = 360;
const BAND_CY = 32;
const BAND_LENGTH = 420;
const BAND_THICKNESS = 96;

/** The width at which there is a gap to put it in.
 *
 * Below this the search field has come left far enough to meet the wordmark and
 * there is no sky between them — the band would be a smear behind a text field.
 * Stated as a breakpoint rather than faded out by the mask because the mask's
 * two edges cross over at about this width, and a gradient whose stops are out
 * of order is not a subtle version of one whose stops are in order. */
const STARS_MIN_WIDTH = "min-[1100px]:block";

/** Where each star sits, and how it breathes.
 *
 * `d` is how far along the band it is from the middle, `off` how far off the
 * spine — both in the band's own frame, which is why they are turned into left
 * and top below rather than written as them. Written that way the field can be
 * tilted by changing one number, and the alternative is twenty-two pairs of
 * coordinates that all have to be recomputed by hand to move the band a degree.
 *
 * Generated once by a seeded run and pasted here as literals. Not generated at
 * render: a random number differs between the server's render and the browser's
 * and React would call that a mismatch, and it would also mean the sky was a
 * different sky on every page load, which nobody asked for.
 *
 * The periods are spread across three and eight seconds and share no factors
 * worth speaking of, so no two stars stay in step. The delays are negative,
 * which starts each one part-way through its own cycle — without them all
 * forty-four would be at full brightness on the first frame and dim together.
 *
 * They crowd the middle. The generator lays them out evenly and then raises each
 * position to a power, which pulls every one of them toward the core without
 * thinning either end to nothing — a hole at the tips would say the band stops
 * there, and it does not stop, it fades. The scatter off the spine widens
 * through the middle by the same idea, which is what a bulge is. */
const STARS = [
  { d: -179, off: 1, r: 0.81, peak: 0.47, pulse: 4.9, delay: -7.4 },
  { d: -166, off: -2, r: 1.09, peak: 0.53, pulse: 4.1, delay: -0.3 },
  { d: -151, off: 11, r: 0.91, peak: 0.67, pulse: 4.9, delay: -1.7 },
  { d: -138, off: -9, r: 1.09, peak: 0.81, pulse: 3.7, delay: -2.2 },
  { d: -129, off: 3, r: 1, peak: 0.58, pulse: 7.2, delay: -6.9 },
  { d: -113, off: -8, r: 1.16, peak: 0.77, pulse: 4.6, delay: -2.2 },
  { d: -110, off: 11, r: 0.89, peak: 0.73, pulse: 6.7, delay: -2.5 },
  { d: -91, off: -4, r: 1.22, peak: 0.8, pulse: 6.2, delay: -1.1 },
  { d: -80, off: -10, r: 1.59, peak: 0.58, pulse: 4.7, delay: -6.6 },
  { d: -71, off: 1, r: 1.38, peak: 0.38, pulse: 3.8, delay: -3.5 },
  { d: -65, off: 21, r: 0.97, peak: 0.34, pulse: 7.6, delay: -7.1 },
  { d: -58, off: -4, r: 1.44, peak: 0.57, pulse: 6.9, delay: -1.4 },
  { d: -45, off: 10, r: 0.76, peak: 0.75, pulse: 7.1, delay: -2.2 },
  { d: -41, off: -20, r: 1.6, peak: 0.48, pulse: 7.1, delay: -1.1 },
  { d: -30, off: -21, r: 1.35, peak: 0.45, pulse: 4.1, delay: -5.5 },
  { d: -26, off: 12, r: 0.68, peak: 0.34, pulse: 5.2, delay: -2.7 },
  { d: -17, off: 19, r: 1.1, peak: 0.52, pulse: 7.3, delay: -7.2 },
  { d: -13, off: 8, r: 1.02, peak: 0.55, pulse: 7.7, delay: -0.8 },
  { d: -7, off: -8, r: 0.63, peak: 0.58, pulse: 6.7, delay: -1.8 },
  { d: -6, off: 19, r: 1.25, peak: 0.57, pulse: 7.7, delay: -0.7 },
  { d: -3, off: 0, r: 1.28, peak: 0.8, pulse: 5.3, delay: -0.1 },
  { d: 0, off: 19, r: 1.29, peak: 0.38, pulse: 6.8, delay: -3 },
  { d: 0, off: 0, r: 0.88, peak: 0.31, pulse: 4.7, delay: -2.8 },
  { d: 1, off: -8, r: 1.18, peak: 0.75, pulse: 7.6, delay: -7.3 },
  { d: 4, off: -2, r: 0.74, peak: 0.43, pulse: 4.8, delay: -7.6 },
  { d: 9, off: 6, r: 1.61, peak: 0.82, pulse: 6.2, delay: -5.1 },
  { d: 12, off: -12, r: 1.43, peak: 0.53, pulse: 7.7, delay: -6.7 },
  { d: 19, off: -3, r: 1.42, peak: 0.34, pulse: 6.4, delay: -0.7 },
  { d: 27, off: 2, r: 0.71, peak: 0.52, pulse: 6.6, delay: -5.2 },
  { d: 31, off: -2, r: 0.83, peak: 0.42, pulse: 5.6, delay: -7.2 },
  { d: 41, off: 3, r: 1.54, peak: 0.66, pulse: 3.7, delay: -3.8 },
  { d: 49, off: -13, r: 1.17, peak: 0.48, pulse: 6.5, delay: -2.4 },
  { d: 54, off: -10, r: 1.11, peak: 0.32, pulse: 4.6, delay: -1.7 },
  { d: 67, off: 6, r: 1.42, peak: 0.72, pulse: 7.7, delay: -5.8 },
  { d: 73, off: 8, r: 1.08, peak: 0.51, pulse: 4.7, delay: -3.1 },
  { d: 87, off: 16, r: 1.59, peak: 0.63, pulse: 6.3, delay: -4.1 },
  { d: 97, off: 1, r: 0.87, peak: 0.5, pulse: 4.3, delay: -7.6 },
  { d: 110, off: 1, r: 1.44, peak: 0.7, pulse: 6.8, delay: -6.6 },
  { d: 114, off: 8, r: 1.55, peak: 0.74, pulse: 7.5, delay: -4 },
  { d: 132, off: -10, r: 1.38, peak: 0.42, pulse: 5.2, delay: -1.2 },
  { d: 148, off: 2, r: 1.18, peak: 0.66, pulse: 4.3, delay: -6.2 },
  { d: 162, off: -2, r: 1.02, peak: 0.66, pulse: 3.5, delay: -6.8 },
  { d: 174, off: 4, r: 1.09, peak: 0.78, pulse: 3.7, delay: -7 },
  { d: 189, off: 1, r: 1.11, peak: 0.61, pulse: 6.6, delay: -0.3 },
] as const;

/** The rest of the sky.
 *
 * Nothing to do with the band. These are spread across the whole bar, edge to
 * edge, and they stay when the galaxy is hidden — a narrow window has no room
 * for a galaxy but it is still night.
 *
 * x is a percentage because this layer is the width of the window rather than of
 * the content, and there is no fixed point in it to measure from. The positions
 * are laid out one to a slice with a small wobble, which is what makes them even
 * without making them a row: forty at random would have left two touching and a
 * bare stretch beside them, and the eye finds both.
 *
 * Both axes, not just x. Leaving y to chance puts thirteen of the twenty in the
 * upper half and nearly all of the right-hand ones along the top. So the bar is
 * cut into twenty horizontal bands as well, the bands are shuffled, and
 * each slice takes one — every height gets exactly one star and no two are at
 * the same one, while which slice gets which height stays arbitrary.
 *
 * Still lighter than the band's, but not by much. Half a pixel of radius at a
 * fifth of white does not survive the draw: at that size a dot does not land on
 * a pixel — the antialiaser spreads it across four of them at a quarter of the
 * weight each, so 0.2 arrives as 0.05 and the sky reads as empty. Size is the
 * greater part of it, so these are about double the area and about double the
 * weight — one step and not a nudge, because what is being avoided is not
 * subtlety but absence.
 *
 * They still give way to the band, and they have to: they are the sky the galaxy
 * is in, and at equal weight there is no galaxy — just stars, some of which
 * happen to be in a line. */
const SKY = [
  { x: 1.4, y: 42.3, r: 1.51, peak: 0.66, pulse: 7.3, delay: -8.1 },
  { x: 2.9, y: 9.7, r: 1.4, peak: 0.35, pulse: 4.6, delay: -7.5 },
  { x: 7, y: 24.4, r: 1.54, peak: 0.7, pulse: 6.7, delay: -0.1 },
  { x: 8.8, y: 14.8, r: 1.58, peak: 0.67, pulse: 6.5, delay: -1.4 },
  { x: 11.4, y: 5.8, r: 1.54, peak: 0.38, pulse: 7.7, delay: -1.5 },
  { x: 14.3, y: 44, r: 1.01, peak: 0.6, pulse: 6.3, delay: -5.7 },
  { x: 16.5, y: 39.1, r: 1.02, peak: 0.55, pulse: 6.1, delay: -1.4 },
  { x: 18.2, y: 13.8, r: 1.02, peak: 0.5, pulse: 7.3, delay: -3.9 },
  { x: 21.1, y: 55.6, r: 1.22, peak: 0.49, pulse: 9.1, delay: -3.2 },
  { x: 24.3, y: 20, r: 1.53, peak: 0.46, pulse: 7.7, delay: -3.8 },
  { x: 27.1, y: 12.6, r: 1.16, peak: 0.64, pulse: 7.1, delay: -7.7 },
  { x: 29.4, y: 49.4, r: 1.05, peak: 0.5, pulse: 6.5, delay: -1.7 },
  { x: 32.1, y: 31.7, r: 1.47, peak: 0.4, pulse: 6.6, delay: -0.6 },
  { x: 34, y: 33.9, r: 1.47, peak: 0.5, pulse: 6.6, delay: -8.9 },
  { x: 37, y: 35.9, r: 1.06, peak: 0.54, pulse: 4.7, delay: -4.6 },
  { x: 38.2, y: 7.3, r: 1.15, peak: 0.66, pulse: 7.2, delay: -5 },
  { x: 40.9, y: 41.3, r: 1.5, peak: 0.48, pulse: 8.2, delay: -0.3 },
  { x: 43.8, y: 46.8, r: 1.08, peak: 0.58, pulse: 6.4, delay: -1.6 },
  { x: 46.2, y: 10.7, r: 1.66, peak: 0.39, pulse: 6.5, delay: -3.8 },
  { x: 48.8, y: 45.4, r: 1.51, peak: 0.39, pulse: 8.1, delay: -0.3 },
  { x: 50.6, y: 16.6, r: 1.33, peak: 0.47, pulse: 8.3, delay: -0.9 },
  { x: 53.5, y: 25.1, r: 0.97, peak: 0.68, pulse: 7.6, delay: -1.8 },
  { x: 55.5, y: 40.2, r: 1.64, peak: 0.37, pulse: 4.4, delay: -4.9 },
  { x: 58.9, y: 51.2, r: 0.94, peak: 0.71, pulse: 5.8, delay: -0.6 },
  { x: 60.5, y: 26.9, r: 1.31, peak: 0.48, pulse: 6.4, delay: -1 },
  { x: 63.9, y: 54.7, r: 1.4, peak: 0.54, pulse: 9.3, delay: -0.7 },
  { x: 66.2, y: 19.1, r: 1.28, peak: 0.49, pulse: 6.1, delay: -6 },
  { x: 68.9, y: 22.7, r: 1.45, peak: 0.45, pulse: 6.5, delay: -0.8 },
  { x: 71, y: 47.8, r: 1.4, peak: 0.41, pulse: 5.2, delay: -4.6 },
  { x: 74.3, y: 21.1, r: 1.07, peak: 0.5, pulse: 6.6, delay: -5.9 },
  { x: 76.5, y: 29.7, r: 1.45, peak: 0.71, pulse: 7.1, delay: -2.1 },
  { x: 78.6, y: 37.1, r: 0.95, peak: 0.67, pulse: 8.3, delay: -2.3 },
  { x: 80.8, y: 57.1, r: 0.96, peak: 0.52, pulse: 4.4, delay: -4.4 },
  { x: 83, y: 31.3, r: 0.91, peak: 0.51, pulse: 6.9, delay: -3.1 },
  { x: 85.4, y: 8.3, r: 1.69, peak: 0.46, pulse: 5.4, delay: -1.9 },
  { x: 88, y: 34.9, r: 1.42, peak: 0.52, pulse: 8.2, delay: -8.3 },
  { x: 91.5, y: 17.7, r: 1.49, peak: 0.36, pulse: 7.7, delay: 0 },
  { x: 94.2, y: 28, r: 1.43, peak: 0.67, pulse: 9.1, delay: -4.1 },
  { x: 96.7, y: 51.8, r: 1.61, peak: 0.61, pulse: 8, delay: -1.7 },
  { x: 99.3, y: 53, r: 0.94, peak: 0.52, pulse: 4.2, delay: -0.4 },
] as const;

const RAD = (BAND_TILT * Math.PI) / 180;
const COS = Math.cos(RAD);
const SIN = Math.sin(RAD);

/** One star, wherever it is. Position comes in already worked out, because the
 *  two fields measure from different things and neither should have to know
 *  that the other exists. */
function Star({
  left,
  top,
  s,
}: {
  left: string;
  top: string;
  s: { r: number; peak: number; pulse: number; delay: number };
}) {
  return (
    <span
      className="header-star"
      style={
        {
          left,
          top,
          width: `${s.r * 2}px`,
          height: `${s.r * 2}px`,
          "--star-peak": s.peak,
          "--star-pulse": `${s.pulse}s`,
          "--star-delay": `${s.delay}s`,
        } as React.CSSProperties
      }
    />
  );
}

export default function HeaderStars() {
  return (
    <>
      {/* The whole sky, at every width. Separate from the band and not gated
          with it: the galaxy needs a gap to sit in and these do not. */}
      <div aria-hidden className="header-sky">
        {SKY.map((s, i) => (
          <Star key={i} left={`${s.x}%`} top={`${s.y - s.r}px`} s={s} />
        ))}
      </div>
      <Galaxy />
    </>
  );
}

function Galaxy() {
  return (
    <div
      aria-hidden
      className={`header-stars hidden ${STARS_MIN_WIDTH}`}
      style={
        {
          "--band-x": `${BAND_CX - BAND_LENGTH / 2}px`,
          "--band-y": `${BAND_CY - BAND_THICKNESS / 2}px`,
          "--band-w": `${BAND_LENGTH}px`,
          "--band-h": `${BAND_THICKNESS}px`,
          "--band-tilt": `${-BAND_TILT}deg`,
        } as React.CSSProperties
      }
    >
      {STARS.map((s, i) => (
        // Turned out of the band's frame and into the bar's. y falls as d rises
        // because the band climbs to the right and the screen's y does not.
        <Star
          key={i}
          left={`${BAND_CX + s.d * COS - s.off * SIN - s.r}px`}
          top={`${BAND_CY - s.d * SIN - s.off * COS - s.r}px`}
          s={s}
        />
      ))}
    </div>
  );
}
