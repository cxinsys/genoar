import type { ReactNode } from "react";
import type { PaletteId } from "@/lib/preferences";

/* The frames the global indicator plays, and the only thing that has to change
   if the drawings are ever redone. Everything around them — the cycling, the
   waits, the slide — is written against these lists and not against what is in
   them.
 *
 * The files in public/loading/ were made from the green-screen frames the
 * generator produced. What was done to them, in case it has to be done again:
 *
 *   keyed on how much greener a pixel is than its own red and blue, rather than
 *   on its distance from one particular green. The screen came out seven and a
 *   half times greener than either; nothing in the drawing passed one and a
 *   fifth, mint wingtips included, so there was never a chance of confusing the
 *   two.
 *
 *   despilled with a ceiling rather than a subtraction — green pulled down to at
 *   most a quarter above the other channels — which takes the fringe off the
 *   outline without touching those same wingtips.
 *
 *   cropped with one box for all eight. Not eight boxes fitted to eight
 *   drawings: the box does not move between frames, so a character that sits a
 *   few pixels differently inside its own crop is a character that jitters. The
 *   box is tall enough for the lightbulb, which is why the loop frames have so
 *   much empty air above them.
 *
 *   the desk faded out on the left. It runs off both sides of the drawing, so
 *   the crop leaves it ending in mid-air; the right-hand cut lands on the edge
 *   of the window and is never seen, and the left one dissolves instead.
 */

/** The box every frame is drawn into. The files are exported at twice this, so
 *  they hold up on a retina screen. */
export const FRAME_W = 192;
export const FRAME_H = 248;

function Frame({ src }: { src: string }) {
  return (
    // A plain img rather than next/image: these are fixed-size decorations that
    // all have to be in the browser before the first one is shown, which is the
    // opposite of what next/image is for.
    //
    // At ordinary priority, not low. Low was the polite choice — do not compete
    // with the page's own data, the very thing whose slowness these report — but
    // it meant the drawings routinely lost the race to the request that called
    // for them, and the indicator rose as a white rectangle. 300KB fetched once
    // per tab is a smaller cost than that.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      width={FRAME_W}
      height={FRAME_H}
      draggable={false}
      className="h-full w-full select-none"
    />
  );
}

/** A set of drawings, and which palette asks for it.
 *
 * One set per idea of what the character is doing while you wait. The reader's
 * theme chooses between them, which is the whole point: a page made of
 * parchment and brown ink and a page made of night sky do not want the same
 * character doing the same thing in the corner of them.
 *
 * A set is a directory under public/loading and nothing more. Everything around
 * these — the cycling, the waits, the slide, the preloading — is written against
 * the shape below and never against a filename, so a set that exists is a set
 * that works.
 *
 * To add one:
 *
 *   1. put the frames in public/loading/<id>/ as 1..N.webp and a1..aN.webp —
 *      the same names, because the names are what `frames` and `leaving` below
 *      count out
 *   2. add its entry to SETS
 *   3. point a palette at it in FOR_PALETTE
 *
 * The counts are declared rather than discovered: this runs in the browser,
 * which cannot list a directory. A count that is too high is a 404 and a frame
 * that never paints; too low and the last drawings are simply never shown.
 */
interface ArtSet {
  id: string;
  /** How many frames the loop has, numbered from 1. */
  frames: number;
  /** How many the completion sequence has, numbered a1 up. */
  leaving: number;
}

const SETS = {
  /** Glasses on, one page turned, and the idea arriving as a lightbulb. */
  book: { id: "book", frames: 5, leaving: 3 },
  /** A scroll map held open in both paws, looked over left and right, and then
      a place on it found. Six frames because that is how the sweep came out —
      two of them are the same pose, so it holds for a beat in the middle. */
  chart: { id: "chart", frames: 6, leaving: 3 },
  /** A spyglass raised, swept left and then round to the front, and lowered on
      something spotted. */
  spyglass: { id: "spyglass", frames: 6, leaving: 3 },
  /** The same sweep under a night sky: the character lit in violet and rose,
      with stars across its spectacles and the lens. */
  stars: { id: "stars", frames: 5, leaving: 3 },
} as const satisfies Record<string, ArtSet>;

type ArtSetId = keyof typeof SETS;

/** Which set each palette asks for.
 *
 * Written out for every palette rather than defaulted, so that adding a palette
 * is a compile error here instead of a silent fallback nobody notices.
 *
 * Named for what the character is doing and not for the palette that asks, which
 * is why `stars` is not `night`: it is the spyglass sweep again in another light,
 * and a second palette wanting the same drawings should be able to say so. */
const FOR_PALETTE: Record<PaletteId, ArtSetId> = {
  plain: "book",
  nautical: "chart",
  tropical: "spyglass",
  night: "stars",
};

/** Everything about one set that the indicator needs, worked out from its entry.
 *
 * Rebuilt on each call rather than memoised. It is two arrays of strings and a
 * handful of elements, asked for once per render of a component that renders
 * when something starts or stops loading — and a cache keyed by palette would be
 * more code than the work it saves. */
export interface LoadingArt {
  id: string;
  /** The loop, played over and over for as long as anything is still loading:
      one page turned, ending where it began so that the last runs back into the
      first. */
  loop: ReactNode[];
  /** Played once, from the moment the work finishes until the indicator is off
      the screen: the idea arriving. Not a loop — it runs through and holds on
      the last frame for the rest of the descent, because a light that keeps
      flashing on the way out is still asking for attention it no longer needs.

      Add or remove frames freely; the pause before the descent grows to fit
      them, so a sequence can never be cut off half-played. */
  leaving: ReactNode[];
  /** Everything this set draws, in one list, so that what is waited for and what
      is shown cannot fall out of step. */
  sources: readonly string[];
}

export function loadingArtFor(palette: PaletteId): LoadingArt {
  const set = SETS[FOR_PALETTE[palette]];
  const at = (name: string) => `/loading/${set.id}/${name}.webp`;
  const loopSrc = Array.from({ length: set.frames }, (_, i) =>
    at(String(i + 1)),
  );
  const leavingSrc = Array.from({ length: set.leaving }, (_, i) =>
    at(`a${i + 1}`),
  );
  return {
    id: set.id,
    loop: loopSrc.map((src) => <Frame key={src} src={src} />),
    leaving: leavingSrc.map((src) => <Frame key={src} src={src} />),
    sources: [...loopSrc, ...leavingSrc],
  };
}

/** How long each frame of the loop is held. Twelve or so a second reads as drawn
    animation; much faster and five frames become a blur, much slower and the
    character looks like it is thinking rather than working. */
export const FRAME_MS = 140;

/** The ending runs at the loop's pace. Kept as its own number rather than
    reusing the loop's, because the two are the same by choice and not by
    nature — the ending plays once and could want its own timing again. */
export const LEAVING_FRAME_MS = FRAME_MS;

/** Where the frames sit when the indicator is up.
 *
 * Nothing at all: the box is pinned flush into the corner of the window, and the
 * drawings decide their own margin by where the character sits inside them
 * rather than by a number here. */
export const AFLOAT = "translateY(0)";

/** Far enough down that the drawing is off the screen.
 *
 *  FRAME_H clears the box. The extra 48px clears the shadow, which is a filter
 *  and so falls outside the box — `drop-shadow(0 8px 18px)` on
 *  .loading-character reaches 26px past the silhouette. Without that slack a
 *  smudge of navy stays at the window edge after the frames have gone. */
export const SUNK = `translateY(${FRAME_H + 48}px)`;
