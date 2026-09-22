"use client";

import { useCallback, useSyncExternalStore } from "react";

/** Whether a CSS media query matches, as a value React can render from.
 *
 * For the cases where a layout does not just look different at another width but
 * *is* different — where the same content has to be somewhere else in the tree
 * rather than styled another way. A sidebar that becomes a drawer is one: two
 * copies hidden from each other by CSS would mean two of every control on the
 * page, two elements answering to the same label, and a screen reader offering
 * both.
 *
 * The server has no viewport and answers false, so what is rendered there is the
 * wide layout — and the narrow one, being the exception, is the one that has to
 * survive arriving a moment late. Both use sites keep their CSS breakpoint as
 * well as this, so the markup the server sends is already hidden at the width
 * where this will change its mind, and nothing is seen moving.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const media = window.matchMedia(query);
      media.addEventListener("change", onChange);
      return () => media.removeEventListener("change", onChange);
    },
    [query],
  );

  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => false,
  );
}
