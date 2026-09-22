"use client";

import { useEffect, useRef, type RefObject } from "react";

/** How far under the button the panel hangs. */
const GAP = 8;

/** A panel that hangs off a button in the header.
 *
 * Two of these — the theme picker and the download menu — and they were the same
 * twenty lines twice.
 *
 * The offsets are written as real inline `top` and `right` rather than as custom
 * properties the stylesheet reads back through `var()`. The panel then holds its
 * position on its own: with the properties, a stylesheet that had not caught up
 * with the markup left the dialog with no rule at all and the browser's default
 * `inset: 0` put it in the top-left corner of the window. Positioning that only
 * works when two files agree is positioning with a way to be wrong.
 *
 * The appearing and disappearing stays in CSS — see `.anchored-panel` — because
 * a transition is what a stylesheet is for and both directions are already
 * there.
 *
 * `showModal()` is a call rather than the `open` attribute: a <dialog> rendered
 * with `open` set is a non-modal one, without the backdrop, the focus trap or
 * the inert page behind it.
 */
export function useAnchoredPanel(
  open: boolean,
  anchor: RefObject<HTMLElement | null>,
): RefObject<HTMLDialogElement | null> {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;

    if (open && !dialog.open) {
      const rect = anchor.current?.getBoundingClientRect();
      // Width and height, not just the rect: an element that is not laid out
      // measures all zeros, and the offsets worked out from that put the panel
      // in the corner. Below the header's md breakpoint the button that opens
      // this is display:none, so that is not hypothetical.
      if (rect && rect.width > 0 && rect.height > 0) {
        const top = Math.round(rect.bottom + GAP);
        // Never less than a hair off the window's own edge, whatever the rect
        // says. The panel is anchored by its right side, so this is the only
        // thing between it and the screen edge.
        //
        // clientWidth and not innerWidth: innerWidth counts a scrollbar that
        // would otherwise be subtracted twice.
        const right = Math.max(
          8,
          document.documentElement.clientWidth - rect.right,
        );
        dialog.style.top = `${top}px`;
        dialog.style.right = `${right}px`;
        // The cap counts down from wherever the panel starts, so it is given
        // the same number rather than working it out again.
        dialog.style.maxHeight = `calc(100dvh - ${top}px - 1rem)`;
      }
      dialog.showModal();
    }

    if (!open && dialog.open) dialog.close();
  }, [open, anchor]);

  return ref;
}
