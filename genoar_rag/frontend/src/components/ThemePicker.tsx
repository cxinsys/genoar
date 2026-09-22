"use client";

import { type RefObject } from "react";
import type { PaletteId } from "@/lib/preferences";
import { PALETTES, setPalette } from "@/lib/preferences";
import { useAnchoredPanel } from "@/hooks/useAnchoredPanel";

/** A picture of each palette, for the row that offers it.
 *
 * Written out rather than read from the tokens, and that is a real trade. The
 * tokens for one palette can be borrowed by hanging its data-palette on any
 * element — they are plain custom properties on an attribute selector, so they
 * cascade into whatever wears it. What that cannot do is show *plain*, because
 * plain is the root's own values and there is nothing to hang: a plain swatch
 * sitting on a coloured page would inherit that palette's and offer the
 * reader a picture of what they already have.
 *
 * So these are a likeness, not the thing. Three colours each — the page, the
 * colour it is built round, and the one beside it. */
const SWATCHES: Record<PaletteId, readonly string[]> = {
  plain: ["#f8fafc", "#1a365d", "#0d9488"],
  nautical: ["#fbf1de", "#8f5410", "#5d7a2b"],
  tropical: ["#cdeaf7", "#0e6f7e", "#e0a92a"],
  night: ["#08061a", "#a89bff", "#f27ab8"],
};

interface Props {
  open: boolean;
  current: PaletteId;
  /** The button this hangs from. Measured when the panel opens. */
  anchor: RefObject<HTMLElement | null>;
  onClose: () => void;
}

/** The theme chooser.
 *
 * A native <dialog> opened with showModal(), which is what buys the things a
 * hand-rolled overlay has to be told: focus moves inside and is kept there, Esc
 * closes, the rest of the page goes inert, and ::backdrop is a real element to
 * style rather than a div that has to be positioned over everything.
 *
 * Choosing applies at once and leaves the dialog open. The whole point of this
 * list is what the site looks like, and a chooser that closed on the first click
 * would be asking the reader to decide before they had seen anything.
 */
export default function ThemePicker({ open, current, anchor, onClose }: Props) {
  // The position is measured rather than declared, because only the running
  // page knows where the button ended up: the header is a flex row whose width
  // depends on the search field and the nav.
  const ref = useAnchoredPanel(open, anchor);

  return (
    <dialog
      ref={ref}
      // Esc, and the close button, both arrive here. Without this the dialog
      // would shut while the state that opened it still says it is open, and
      // the next click on the button would do nothing.
      onClose={onClose}
      // The backdrop is part of the dialog's own box, so a click that lands on
      // the element itself rather than on anything inside it is a click outside.
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      aria-labelledby="anchored-panel-title"
      className="anchored-panel w-[min(26rem,calc(100vw-2rem))] rounded-2xl bg-surface text-ink-body p-0 shadow-2xl"
    >
      {/* The scroller, not the dialog. The dialog is the column and the height
          cap; this is the part of it that gives way when there are more themes
          than there is room for.
 
          No `flex-1`, and that is the whole of a Safari bug. `flex: 1` sets
          `flex-basis: 0`, and the height of this dialog is auto — so the column's
          intrinsic height is worked out from its items' bases, which is zero.
          Chrome quietly uses the content size instead and Safari does not, and
          the panel came out one row tall with the rest of it clipped away.
 
          Left at the default `flex: 0 1 auto` the basis is the content, so the
          dialog is as tall as its list until the cap stops it, and then this
          shrinks — flex-shrink is already 1 — and scrolls. min-h-0 is what allows
          that shrink to go below the content's own height. */}
      <div className="p-5 min-h-0 overflow-y-auto overscroll-contain">
        <h2
          id="anchored-panel-title"
          className="text-base font-semibold text-ink"
        >
          Theme
        </h2>
        <p className="mt-1 text-sm text-ink-soft">
          Which colours the site is made of.
        </p>

        <ul className="mt-4 space-y-2">
          {PALETTES.map((palette) => {
            const chosen = palette.id === current;
            return (
              <li key={palette.id}>
                <button
                  type="button"
                  onClick={() => setPalette(palette.id)}
                  aria-pressed={chosen}
                  className={
                    "w-full flex items-center gap-3 rounded-xl px-3 py-3 text-left " +
                    "border transition-colors cursor-pointer " +
                    (chosen
                      ? "border-brand bg-brand/10"
                      : "border-edge hover:bg-raised")
                  }
                >
                  <span
                    aria-hidden
                    className="shrink-0 flex rounded-lg overflow-hidden border border-edge-firm"
                  >
                    {SWATCHES[palette.id].map((colour) => (
                      <span
                        key={colour}
                        className="w-4 h-9"
                        style={{ backgroundColor: colour }}
                      />
                    ))}
                  </span>
                  <span className="min-w-0">
                    <span className="block text-sm font-medium text-ink">
                      {palette.name}
                    </span>
                    <span className="block text-xs text-ink-soft">
                      {palette.blurb}
                    </span>
                  </span>
                  {/* Reserved whether or not it is showing, so that choosing a
                      palette does not shuffle the text of the row beside it. */}
                  <span
                    className={`material-symbols-outlined ml-auto shrink-0 text-[20px] text-brand ${
                      chosen ? "" : "opacity-0"
                    }`}
                  >
                    check
                  </span>
                </button>
              </li>
            );
          })}
        </ul>

        <div className="mt-5 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="h-8 px-4 rounded-full text-sm font-medium text-ink-body hover:bg-raised transition-colors cursor-pointer"
          >
            Done
          </button>
        </div>
      </div>
    </dialog>
  );
}
