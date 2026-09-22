"use client";

import { useState } from "react";
import Link from "next/link";
import HeaderControls from "@/components/HeaderControls";
import HeaderRays from "@/components/HeaderRays";
import HeaderStars from "@/components/HeaderStars";
import { useRouter, usePathname } from "next/navigation";
import {
  HeaderSea,
  BoatMark,
  SEA_PERIOD,
  bobDelay,
  MARK_SIZE,
  MARK_GAP,
} from "@/components/HeaderSea";
import { DEFAULT_EXAMPLE_QUERY } from "@/lib/examples";

type ActivePage = "dashboard" | "search";

const NAV_ITEMS: { label: string; href: string; page: ActivePage }[] = [
  { label: "Dashboard", href: "/", page: "dashboard" },
  { label: "Search", href: "/search", page: "search" },
];

/** Where each letter of the wordmark sits, measured from the left edge of the
 *  mark, so that each can be given the phase of the water actually under it.
 *
 *  Measured rather than computed: the letters are laid out by the font, and
 *  asking the browser where they landed to feed a value back into their own
 *  style is a layout read on every render for a number that decides nothing but
 *  a fraction of a second of delay. The mark and the gap after it come from the
 *  drawing itself; 17.5 is about what a cap of Inter at this weight and size
 *  advances by. */
const WORDMARK_X = MARK_SIZE + MARK_GAP;
const LETTER_ADVANCE = 17.5;

/** How the wordmark's two colours are shared out across its letters.
 *
 * Not evenly, and not by a plain curve either. Two things are wanted at once and
 * they pull against each other: the cool end should have most of the word, which
 * wants the change to happen early, and no part of it should stand apart from
 * what is beside it, which wants it not to happen all at once.
 *
 * A bare exponent gives the first and loses the second. At 0.55 the run went 0,
 * 41, 60, 76, 88, 100 — the step from the first letter to the second was twice
 * any other, so the G sat on its own in yellow and the rest of the word was a
 * separate teal thing beside it.
 *
 * So the exponent is softened and then smoothed: the bias moves the middle of
 * the run forward and the smoothstep flattens both ends, which is what takes the
 * first step down to about the size of the others. Then the whole run is shifted
 * a letter along, because pure yellow turned out to be a colour the word could
 * start near but not at.
 *
 * This is measured at the *edges* between letters rather than at their middles —
 * there are seven of those for six letters, and each letter is painted with the
 * run between its own two. Which is what makes it continuous: neighbours meet at
 * a shared edge and so at a shared colour, whatever their widths turn out to be.
 * Nothing here needs to know how wide a G is. */
const WORDMARK_BIAS = 0.75;
const WORDMARK_SHIFT = 1;

function wordmarkMix(edge: number, letters: number): number {
  const t = Math.pow(
    Math.min(edge + WORDMARK_SHIFT, letters) / letters,
    WORDMARK_BIAS,
  );
  return t * t * (3 - 2 * t);
}

/** The colour at one of those edges.
 *
 * Both ends come from the stylesheet so that a palette can answer them, and they
 * are real tokens rather than `currentColor`: a letter painted this way has its
 * own colour set to transparent, so currentColor inside its background would
 * resolve to transparent and the word would disappear. */
function wordmarkEdge(edge: number, letters: number): string {
  const pct = Math.round(wordmarkMix(edge, letters) * 100);
  return `color-mix(in oklab, var(--wordmark-from), var(--wordmark-to) ${pct}%)`;
}

/** The site's header.
 *
 * Rendered once by the root layout rather than by each page, so that a route
 * change re-renders it instead of replacing it. Replaced, its DOM nodes are new
 * ones, and a new node starts its CSS animations from the beginning — the sea
 * would jolt back to its starting position on every navigation.
 *
 * Which is why it reads the path itself instead of being told: a page that
 * passed `activePage` would have to render the header to pass it to.
 */
export default function Header() {
  const [searchValue, setSearchValue] = useState("");
  const router = useRouter();
  const pathname = usePathname();
  const activePage = NAV_ITEMS.find((item) => item.href === pathname)?.page;

  function handleSearch(e: React.FormEvent) {
    e.preventDefault();
    if (searchValue.trim()) {
      // Semantic, as the field says. The other mode narrows by filter alone and
      // offers no query field at all, so a natural-language phrase sent there
      // would be matched as a literal substring and find nothing.
      const params = new URLSearchParams({
        keyword: searchValue.trim(),
        search_mode: "semantic",
      });
      router.push(`/search?${params.toString()}`);
      setSearchValue("");
    }
  }

  return (
    <header
      // Named so the side panel can measure it. The panel hangs from the
      // header's lower edge, and the one thing that must not be guessed is where
      // that edge is — a constant here would be a number to keep in step with
      // the row's height for ever.
      data-site-header
      className="site-header border-b border-edge sticky top-0 z-50 shadow-card"
    >
      {/* The row is between the two bands of water rather than on top of them,
          which is what puts the wordmark and the boat in the sea instead of
          beside it. All three are positioned and none carries a z-index:
          document order is what decides. It is not the header itself that clips
          the waves either — the header must stay unclipped for the tooltips that
          hang out of its bottom edge. */}
      {/* First, so it is under the water and under the row — see the note above
          about document order. A palette that wants nothing here sets nothing
          and the box paints nothing. */}
      <div aria-hidden className="header-weave" />
      {/* Over the sky and under the water. Light falling on the sea would have
          to light the sea, and a shaft laid over the waves lies on top of them
          instead — which reads as a scratch on the picture. */}
      <HeaderRays />
      {/* Beside the rays and for the same reason: sky, and so under the water.
          One palette lights the bar and one darkens it, and neither has to know
          about the other — each draws nothing until its own tokens are set. */}
      <HeaderStars />
      <HeaderSea layer="behind" />
      <div className="relative max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between h-16 gap-6">
          {/* Logo */}
          <Link href="/" className="flex items-center gap-2 flex-shrink-0">
            {/* Sized from the number the drawing's waterline was fitted to
                rather than from a class, so the two cannot drift apart — see
                MARK_SIZE. No nudge: at this size the boat's own waterline is
                already the sea's, to within a third of a pixel. */}
            <div style={{ width: MARK_SIZE, height: MARK_SIZE }}>
              <BoatMark />
            </div>
            {/* leading-none so the line box hugs the glyphs; with the default
                line height the extra leading is split unevenly against the mark.
                The 3px is not a centring fix — it sinks the word's feet to the
                waterline, so the near wave passing in front laps over them.

                One span per letter because each floats on its own, and each is
                given the phase of the water at its own position rather than a
                step chosen to look like a ripple. That is the difference between
                the word moving in the sea and the word moving near it. */}
            <span
              aria-label="GENOAR"
              className="text-[26px] font-extrabold text-brand dark:text-white leading-none translate-y-[3px]"
            >
              {"GENOAR".split("").map((letter, i, all) => (
                <span
                  key={i}
                  aria-hidden
                  className="letter-float bg-clip-text text-transparent"
                  style={{
                    animationDuration: SEA_PERIOD,
                    animationDelay: bobDelay(
                      WORDMARK_X + (i + 0.5) * LETTER_ADVANCE,
                      0.15,
                    ),
                    // A real gradient, and one per letter rather than one behind
                    // the word.
                    //
                    // Clipping a single background to the whole word is the
                    // usual way and does not work here: every letter carries its
                    // own bob, and a transformed child is painted apart from the
                    // ancestor whose background is being clipped — rendered, the
                    // two letters that happened to be mid-bob were simply
                    // missing, and the word read "G N AR".
                    //
                    // So each letter clips its own slice of the run. Because the
                    // slices are cut at the edges between letters, each one
                    // starts where the last ended and the seams are invisible
                    // without anything having to know how wide a letter is.
                    backgroundImage: `linear-gradient(to right, ${wordmarkEdge(
                      i,
                      all.length,
                    )}, ${wordmarkEdge(i + 1, all.length)})`,
                  }}
                >
                  {letter}
                </span>
              ))}
            </span>
          </Link>

          {/* Center: global search bar */}
          <form
            onSubmit={handleSearch}
            className="flex-1 max-w-xl mx-auto hidden md:block"
          >
            <div className="relative">
              <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                <span className="material-symbols-outlined text-ink-faint text-[20px] leading-none">
                  search
                </span>
              </div>
              <input
                className="block w-full pl-10 pr-3 py-1.5 bg-header-well border-none rounded-lg text-sm text-ink placeholder-ink-soft focus:ring-2 focus:ring-brand focus:bg-surface transition-all outline-none"
                placeholder={`Ask the AI Librarian (e.g. ${DEFAULT_EXAMPLE_QUERY})`}
                type="text"
                value={searchValue}
                onChange={(e) => setSearchValue(e.target.value)}
              />
            </div>
          </form>

          {/* Navigation links */}
          {/* No gap: each link carries its own px-3, which reads as one group
              of tabs rather than two separate buttons. */}
          <nav className="hidden md:flex items-center">
            {NAV_ITEMS.map(({ label, href, page }) => {
              const isActive = activePage === page;
              return (
                <Link
                  key={page}
                  href={href}
                  aria-current={isActive ? "page" : undefined}
                  // Same box in both states — only the colours change. Padding on
                  // the active item alone made every link move as you navigated.
                  // py-1.5 with text-sm is the search field's height, so the row
                  // lines up across the header.
                  className={`text-sm font-medium px-3 py-1.5 rounded-md transition-colors ${
                    isActive
                      ? "text-brand bg-brand/10 dark:bg-brand/20"
                      : "text-ink-body hover:text-brand"
                  }`}
                >
                  {label}
                </Link>
              );
            })}
          </nav>

          <HeaderControls />
        </div>
      </div>
      <HeaderSea layer="front" />
    </header>
  );
}
