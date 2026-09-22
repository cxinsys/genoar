"use client";

import { useEffect, useRef, useSyncExternalStore } from "react";
import {
  closeSidePanel,
  getSidePanel,
  getSidePanelOnServer,
  registerSidePanel,
  subscribeSidePanel,
} from "@/lib/sidePanel";

interface Props {
  /** What the panel is, for anyone who cannot see it. */
  label: string;
  children: React.ReactNode;
}

/** A page's side column, as a drawer, for windows too narrow to put it beside
 *  anything.
 *
 * It hangs from the header rather than covering it, and that one decision
 * settles most of the rest. The button that opened it is up there, so the header
 * has to stay both visible and usable — which rules out showModal(). A modal
 * dialog makes every other element on the page inert, and the first thing that
 * goes inert is the control the reader is about to reach for to close this. It
 * looked like a close button and was a picture of one.
 *
 * So this is a plain open dialog, and the three things showModal() would have
 * given are done by hand, because each of them is now a choice rather than a
 * default:
 *
 *   the dim behind it   a layer of its own, which is what lets it fade. As a
 *                       ::backdrop it could not: the pseudo-element is created
 *                       and destroyed with the dialog, so it blinked in and out
 *                       at full strength while the panel beside it slid.
 *   a click outside     that same layer, which stops at the header's edge — so
 *                       clicking the header is not "outside", it is the header
 *   Escape              a listener, for as long as this is open
 *
 * Focus is moved in on opening and put back where it was on closing. Not
 * trapped, deliberately: the header stays available, and being unable to tab to
 * it would be the inert problem again in another form.
 */
export default function SidePanel({ label, children }: Props) {
  const { open } = useSyncExternalStore(
    subscribeSidePanel,
    getSidePanel,
    getSidePanelOnServer,
  );
  const ref = useRef<HTMLDialogElement>(null);
  const returnTo = useRef<HTMLElement | null>(null);

  // Telling the header there is something to open, for exactly as long as this
  // is mounted. Pages mount this only at the widths where their column has
  // nowhere to be, so this is also what keeps the button off a window wide
  // enough not to need it.
  useEffect(() => registerSidePanel(), []);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;

    if (open && !dialog.open) {
      // Measured on the way open rather than held in a constant: the header is
      // one row of a sticky bar and its height belongs to that bar.
      //
      // Written straight onto the document rather than kept in state. It is a
      // measurement of the page taken to hand to CSS, and putting it through a
      // render only to arrive back at a style property would mean drawing the
      // panel once at the wrong offset and then again at the right one.
      const header = document.querySelector("[data-site-header]");
      if (header) {
        document.documentElement.style.setProperty(
          "--panel-top",
          `${Math.round(header.getBoundingClientRect().bottom)}px`,
        );
      }
      returnTo.current = document.activeElement as HTMLElement | null;
      dialog.show();
      dialog.focus();
    }

    if (!open && dialog.open) {
      dialog.close();
      // Back to the button that opened it, so that closing does not drop the
      // reader at the top of the document.
      returnTo.current?.focus?.();
      returnTo.current = null;
    }
  }, [open]);

  // Escape, which an open — as opposed to modal — dialog does not handle.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") closeSidePanel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <>
      {/* Always rendered, and faded rather than added and removed, so that there
          is something to transition. Not focusable and not read aloud: it is a
          shade with a click handler, and the same click is on Escape and on the
          header's own button. */}
      <div
        aria-hidden
        data-open={open}
        onClick={closeSidePanel}
        className="side-panel-veil"
      />
      <dialog
        ref={ref}
        onClose={closeSidePanel}
        aria-label={label}
        className="side-panel bg-surface text-ink-body"
      >
        {/* The panel is the flex column its contents expect to be in — the same
            shape as the aside they come from, so a block that meant to fill the
            space left over still does. */}
        <div className="flex flex-col h-full overflow-y-auto overscroll-contain">
          {children}
        </div>
      </dialog>
    </>
  );
}
