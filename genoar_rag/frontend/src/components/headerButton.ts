/** The shell every header icon button wears, and the hover label they share.
 *
 * Pulled out of HeaderControls so the Download button can wear the same shell
 * without HeaderControls and DownloadMenu having to import each other. Kept in
 * one place so that a fifth button cannot arrive looking almost the same.
 * 32px is the height of the nav links and the search field, which is what lines
 * the header's row up.
 */
export const BUTTON =
  "group/tip relative w-8 h-8 inline-flex items-center justify-center rounded-full " +
  "text-ink-soft hover:text-brand hover:bg-header-raised transition-colors cursor-pointer";

export const ICON = "material-symbols-outlined text-[21px] leading-none";

/** The hover label the icon buttons share, the same chip the page tabs and the
 *  footer use. It hangs below the button — the header is left unclipped for
 *  exactly this — and appears at once rather than after the browser's own title
 *  delay. `group/tip` lives on BUTTON so any button wearing the shell can carry
 *  one just by rendering a <span className={TIP}> child. */
export const TIP =
  "pointer-events-none absolute top-full left-1/2 -translate-x-1/2 mt-2 " +
  "px-2.5 py-1 bg-inverse text-on-inverse text-xs rounded-md whitespace-nowrap " +
  "opacity-0 group-hover/tip:opacity-100 transition-opacity z-50";
