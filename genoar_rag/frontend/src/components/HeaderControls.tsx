"use client";

import { Suspense, useEffect, useRef, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import DownloadNav from "@/components/DownloadMenu";
import { BUTTON, ICON, TIP } from "@/components/headerButton";
import type { Theme } from "@/lib/preferences";
import {
  cycleTheme,
  getPreferences,
  getPreferencesOnServer,
  paletteOf,
  startPreferences,
  subscribePreferences,
  toggleCharacterLoading,
} from "@/lib/preferences";
import ThemePicker from "@/components/ThemePicker";
import {
  getSidePanel,
  getSidePanelOnServer,
  subscribeSidePanel,
  toggleSidePanel,
} from "@/lib/sidePanel";

/** What the brightness button is for, said out loud.
 *
 * The step from following the system to light is the awkward one to name: on a
 * light machine the theme does not change at all, only whether it is being
 * decided by the reader or by the machine. "Fix" is what changes. */
function brightnessLabel(theme: Theme, resolved: "light" | "dark"): string {
  if (theme === "system") {
    return `Brightness follows the system, which is ${resolved}. Fix it to light.`;
  }
  if (theme === "light") return "Brightness is light. Switch to dark.";
  return "Brightness is dark. Follow the system instead.";
}

/** The header's three settings.
 *
 * All are the reader's, all are kept in their browser, and none asks the server
 * anything — so the first render has to be the one the server would have made,
 * and the real answer arrives with the effect below. What corrects itself is an
 * icon; the page's own colours are already right by then, set by the script in
 * the document head before anything was painted.
 */
export default function HeaderControls({
  settingsOpen,
  onToggleSettings,
}: {
  /** Whether the settings are showing on a phone. From md up they always are. */
  settingsOpen: boolean;
  onToggleSettings: () => void;
}) {
  const prefs = useSyncExternalStore(
    subscribePreferences,
    getPreferences,
    getPreferencesOnServer,
  );
  const [picking, setPicking] = useState(false);
  const paletteButton = useRef<HTMLButtonElement>(null);
  const panel = useSyncExternalStore(
    subscribeSidePanel,
    getSidePanel,
    getSidePanelOnServer,
  );

  useEffect(() => startPreferences(), []);

  const { theme, palette, resolved, characterLoading } = prefs;

  // A palette written for one brightness has already answered the question the
  // next button asks. It is left in place and struck through rather than
  // removed: a control that vanishes takes its explanation with it, and the
  // reader is left wondering where dark mode went.
  const fixed = paletteOf(palette).fixed;

  return (
    // Two groups, and the rule dividing them. On the left, going somewhere; on
    // the right, changing how the site looks. The rule is what says they are
    // different kinds of thing, so it belongs between them rather than in front
    // of both.
    // No gap on a phone: with the settings open the row is nine buttons wide,
    // and the 4px between each is what decides whether it fits 360px.
    <div className="flex items-center gap-1 max-md:gap-0 shrink-0">
      {/* The way to the search page, when there is no search bar.

          Below md the header drops both the field and the nav links, which left
          a phone with the dashboard and no way off it. This is the same shell as
          the settings beside it, so the row reads as one group of controls
          rather than as a stray button — and it goes away again as soon as the
          real field is back.

          The margin before the rule is carried by whichever of these is last
          at the width in question, rather than by the group after them, so that
          it leaves when they do: at a width with none of them there is nothing
          on this side of the rule and nothing should be holding room beside
          it. */}
      <Link
        href="/search"
        aria-label="Search"
        title="Search"
        className={`${BUTTON} md:hidden`}
      >
        <span className={ICON}>search</span>
      </Link>

      {/* The guide, for the same reason: the nav link to it goes with the
          others below md, and the dashboard's pointer to it is at the foot of a
          long page. */}
      <Link
        href="/guide"
        aria-label="Analysis Code Support"
        title="Analysis Code Support"
        className={`${BUTTON} md:hidden`}
      >
        <span className={ICON}>menu_book</span>
      </Link>

      {/* The page's side column, when the window is too narrow to show it beside
          anything. Whether there is one to open is not this component's to know
          — the header outlives every page and is rendered above all of them — so
          the page says so by mounting a panel, and this appears when one has.
          That also settles the breakpoint without naming it here: a page mounts
          its panel only at the widths where its column has nowhere else to go.

          One button rather than two. It is the same panel either way, and a
          close control that lives somewhere other than the thing that opened it
          is a second place to have to look.

          From md up only. Below it the menu button next to this does the job,
          and opens the panel along with the settings. */}
      {panel.available > 0 && (
        <button
          type="button"
          onClick={toggleSidePanel}
          aria-expanded={panel.open}
          aria-label={panel.open ? "Close the panel" : "Open the panel"}
          title={panel.open ? "Close" : "Filters and tools"}
          className={`${BUTTON} max-md:hidden mr-2`}
        >
          <span className={ICON}>{panel.open ? "close" : "menu"}</span>
        </button>
      )}

      {/* A phone's one menu. The row cannot hold the four settings beside the
          links and the wordmark, so they are shown on request and the wordmark
          makes way while they are (see Header) — and where the page has a side
          panel, the same press opens it below. One button for both, because on
          a phone they are the same request: show me the rest. Last before the
          rule below md, so it carries the margin — while there is a rule. */}
      <button
        type="button"
        onClick={() => {
          const opening = !settingsOpen;
          onToggleSettings();
          // Brought into line rather than toggled blind: the panel can be
          // closed from inside itself, and the next press should not open it
          // while closing the settings.
          if (panel.available > 0 && panel.open !== opening) toggleSidePanel();
        }}
        aria-expanded={settingsOpen}
        aria-controls="header-settings"
        aria-label={settingsOpen ? "Close the menu" : "Open the menu"}
        title={settingsOpen ? "Close" : "Menu"}
        className={`${BUTTON} md:hidden ${settingsOpen ? "mr-1" : ""}`}
      >
        <span className={ICON}>{settingsOpen ? "close" : "menu"}</span>
      </button>

      {/* starting: is where the group enters from when it is shown on a phone,
          a few pixels to the right and transparent, so it slides in rather than
          appearing. From md up it is always shown and never enters. */}
      <div
        id="header-settings"
        className={`flex items-center gap-1 pl-3 border-l border-edge max-md:gap-0 max-md:pl-1 transition-[opacity,translate] duration-200 starting:opacity-0 starting:translate-x-2 ${
          settingsOpen ? "" : "max-md:hidden"
        }`}
      >
        {/* Download sits with the settings, not with the nav links, because it
            is a thing to do rather than a place to go — a tab beside Dashboard
            and Search read as a third page. Suspended because it reads the URL's
            query to know what to export, and `useSearchParams` opts its tree out
            of static rendering; the boundary keeps that to this one button. */}
        <Suspense fallback={<span className="w-8 h-8" aria-hidden />}>
          <DownloadNav />
        </Suspense>

        {/* The character that comes up while the app is busy. Charming the first
          few times and not the hundredth, so it can be turned off — and stays
          off, which is the whole point of storing it. */}
        <button
          type="button"
          onClick={toggleCharacterLoading}
          aria-pressed={characterLoading}
          aria-label={
            characterLoading
              ? "Turn off the loading character"
              : "Turn on the loading character"
          }
          className={`${BUTTON} icon-slash`}
          data-on={characterLoading}
        >
          {/* A bird, because the thing being turned off is a bird. It also
            survives being struck through, which the obvious choice did not:
            `animation` is a row of circles running on the same diagonal as the
            slash, so the two became one shape and neither could be read. */}
          <span className={ICON}>raven</span>
          <span className={TIP}>Loading animation</span>
        </button>

        {/* Which colours the site is made of. A dialog rather than another cycle
          button: there are two palettes today and there will be more, and a
          button whose whole job is to show you the next one in a list is a poor
          way to choose from a list. */}
        <button
          ref={paletteButton}
          type="button"
          onClick={() => setPicking(true)}
          aria-haspopup="dialog"
          aria-expanded={picking}
          aria-label="Choose a theme"
          className={BUTTON}
        >
          <span className={ICON}>palette</span>
          <span className={TIP}>Theme</span>
        </button>

        {/* system → light → dark. The icon says what the page looks like now, not
          which of the three was chosen: a reader following the system wants to
          know it is dark, and only wants to know that it is *because of* the
          system when they go looking. Hovering is that looking, so hovering is
          when the monitor appears — and only in the mode where the question has
          an answer worth giving.

          Struck through by the same rule the character toggle uses when the
          palette has taken the question away. The slash already means "this is
          off" everywhere else in this row, and it costs nothing to mean it
          here. */}
        <button
          type="button"
          onClick={fixed ? undefined : cycleTheme}
          // aria-disabled rather than disabled. A disabled button is removed from
          // the tab order and, in several browsers, stops firing the mouse events
          // a title tooltip is shown from — which would take away the one thing
          // explaining why it has a line through it. This way it can still be
          // reached and still answers the question; it just does not do anything.
          aria-disabled={fixed !== null}
          aria-label={
            fixed
              ? `Brightness is set by the ${paletteOf(palette).name} theme, which is ${fixed}.`
              : brightnessLabel(theme, resolved)
          }
          className={`${BUTTON} theme-switch icon-slash`}
          data-on={fixed === null}
          data-mode={theme}
          data-resolved={resolved}
        >
          {/* All three drawn, all but one transparent. Which is showing is decided
            in the stylesheet from the two attributes above, so every change
            between them fades — including light to dark, which swapping the
            ligature inside one span could not do. The monitor is the hover face
            and only answers in the mode where the question means anything: a
            reader following the system wants to know it is dark, and only wants
            to know it is *because of* the system when they go looking. */}
          <span className={ICON} data-face="light">
            light_mode
          </span>
          <span className={ICON} data-face="dark">
            dark_mode
          </span>
          <span className={ICON} data-face="system">
            monitor
          </span>
          <span className={TIP}>Light / dark mode</span>
        </button>
      </div>

      <ThemePicker
        open={picking}
        current={palette}
        anchor={paletteButton}
        onClose={() => setPicking(false)}
      />
    </div>
  );
}
