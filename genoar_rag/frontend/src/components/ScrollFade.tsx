"use client";

import type { ReactNode } from "react";
import { useScrollEdges } from "@/hooks/useScrollEdges";

type ScrollFadeProps = {
  children: ReactNode;
  /** Classes for the scrolling element itself: height limits, padding, spacing. */
  className?: string;
  /** Classes for the outer box, for making it a flex child or giving it a size. */
  wrapperClassName?: string;
  /** Classes for the content inside the scroller, when it needs its own layout. */
  contentClassName?: string;
  /** How tall each fade is. */
  height?: string;
  /** The colour the content fades into — whatever is behind the container.
      Defaults to the card it is presumably in, which is the answer in both
      themes; pass something else where it is not. */
  color?: string;
};

/**
 * A scroll container whose clipped edges fade into the background instead of
 * ending in a hard cut.
 *
 * The fade is shown on the side that still has content and hidden on the side
 * that has run out, so scrolled to the top only the bottom edge is softened and
 * nothing covers the first row. A container short enough not to scroll shows
 * neither.
 *
 * The overlays sit in a wrapper rather than in the scroller, so they stay put
 * at the container's edges while the content moves under them.
 */
export default function ScrollFade({
  children,
  className = "",
  wrapperClassName = "",
  contentClassName = "",
  height = "1.5rem",
  color = "var(--color-surface)",
}: ScrollFadeProps) {
  const { ref, atTop, atBottom } = useScrollEdges<HTMLDivElement>();

  return (
    // min-w-0 because a flex item will not shrink below its content's minimum
    // width by default: long rows would push the whole container past its
    // parent instead of scrolling or ellipsising inside it.
    <div className={`relative min-w-0 ${wrapperClassName}`}>
      <div ref={ref} className={`overflow-y-auto ${className}`}>
        <div className={contentClassName}>{children}</div>
      </div>

      {/* Opaque against the edge it belongs to, clear where the content carries
          on: the top fade is solid at the top, the bottom fade at the bottom.

          Each starts a pixel outside its edge. The overlay and the text under it
          are laid out independently, so at fractional offsets they round to
          different device pixels and a hairline of unfaded text shows through at
          the boundary. That extra pixel is the fully opaque end of the gradient,
          so covering it changes nothing else. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 transition-opacity duration-200"
        style={{
          top: "-1px",
          height: `calc(${height} + 1px)`,
          background: `linear-gradient(to bottom, ${color}, transparent)`,
          opacity: atTop ? 0 : 1,
        }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 transition-opacity duration-200"
        style={{
          bottom: "-1px",
          height: `calc(${height} + 1px)`,
          background: `linear-gradient(to top, ${color}, transparent)`,
          opacity: atBottom ? 0 : 1,
        }}
      />
    </div>
  );
}
