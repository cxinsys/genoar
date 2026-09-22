"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Whether a scroll container is resting against its top or bottom edge.
 *
 * A container with nothing to scroll is at both edges at once, which is the
 * state callers want: an edge treatment has nothing to soften there.
 */
export type ScrollEdges = {
  atTop: boolean;
  atBottom: boolean;
  /** False when everything fits, which is when both edges read as reached. */
  isScrollable: boolean;
};

// Sub-pixel scroll offsets are normal at fractional zoom and on trackpads, so
// an exact comparison against 0 would leave a container that looks parked at
// the top reporting otherwise.
const EDGE_TOLERANCE_PX = 1;

export function useScrollEdges<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [edges, setEdges] = useState<ScrollEdges>({
    atTop: true,
    atBottom: true,
    isScrollable: false,
  });

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const overflow = el.scrollHeight - el.clientHeight;
    setEdges((prev) => {
      const next = {
        atTop: el.scrollTop <= EDGE_TOLERANCE_PX,
        atBottom: overflow - el.scrollTop <= EDGE_TOLERANCE_PX,
        isScrollable: overflow > EDGE_TOLERANCE_PX,
      };
      // Scroll events fire far more often than the answer changes, and every
      // state write would re-render the subtree under the container.
      return prev.atTop === next.atTop &&
        prev.atBottom === next.atBottom &&
        prev.isScrollable === next.isScrollable
        ? prev
        : next;
    });
  }, []);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    measure();
    el.addEventListener("scroll", measure, { passive: true });

    // The answer also changes without anyone scrolling: the container is
    // resized, or its contents arrive from the API and it becomes scrollable.
    // Observing both the container and its content covers each case.
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    for (const child of Array.from(el.children)) observer.observe(child);

    return () => {
      el.removeEventListener("scroll", measure);
      observer.disconnect();
    };
  }, [measure]);

  return { ref, ...edges };
}
