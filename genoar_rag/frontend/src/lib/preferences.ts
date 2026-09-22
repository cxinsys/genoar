/** The three things the reader gets to decide, and where they are kept.
 *
 * One store rather than three because they share everything that is awkward
 * about a preference: it lives in localStorage, which the server cannot see; it
 * has to be readable during render without tearing; and the answer can change
 * from outside React — another tab writing the same key, or the operating system
 * changing its mind about dark mode.
 *
 * Written in the same shape as the count of requests in flight: a value, a set
 * of listeners, and a snapshot that is only rebuilt when something has actually
 * changed. useSyncExternalStore compares snapshots by identity, so handing back
 * a fresh object each time would re-render on every check for ever.
 */

const KEY = "genoar:preferences";

export type Theme = "system" | "light" | "dark";
export type PaletteId = "plain" | "nautical" | "tropical" | "night";

/** A set of colours, and whether it lets the reader pick a brightness.
 *
 * The two settings are not the same question. Brightness is "how much light is
 * in the room"; a palette is "which colours the site is made of". Plain answers
 * both brightnesses and hands the first question back to the reader. A palette
 * written for one of them answers it itself, and `fixed` is how it says so —
 * which is what the brightness button reads to know it has nothing to offer.
 *
 * A list rather than a pair, because more of these are coming. Everything about
 * a palette that is not colour values lives in this one array. */
export interface Palette {
  id: PaletteId;
  name: string;
  blurb: string;
  fixed: "light" | "dark" | null;
}

export const PALETTES: readonly Palette[] = [
  {
    id: "plain",
    name: "Plain",
    blurb:
      "The navy and greys the site is built from. Follows your brightness.",
    fixed: null,
  },
  {
    id: "nautical",
    name: "Nautical chart",
    blurb: "Parchment, brown ink and the warm colours beside them. Light only.",
    fixed: "light",
  },
  {
    id: "tropical",
    name: "Tropical",
    blurb: "Shallow water, palm green and a sun over the header. Light only.",
    fixed: "light",
  },
  {
    id: "night",
    name: "Night sky",
    blurb: "Rose, violet and indigo over near-black. Dark only.",
    fixed: "dark",
  },
];

export function paletteOf(id: PaletteId): Palette {
  return PALETTES.find((p) => p.id === id) ?? PALETTES[0];
}

export interface Preferences {
  /** What brightness the reader asked for. Remembered even while a palette is
      overriding it, so turning the palette off gives them back their answer. */
  theme: Theme;
  /** Which set of colours. */
  palette: PaletteId;
  /** What is actually painted: the palette's own brightness if it has one, and
      otherwise the reader's, with "system" resolved against the OS. */
  resolved: "light" | "dark";
  /** Whether the character appears while the app is busy. */
  characterLoading: boolean;
}

/** What the server renders, and what the first client render must agree with.
 *
 * Light and on, because that is what the markup would look like with no
 * preference stored. The real answer arrives a moment later; the inline script
 * in the document head has already set the page's colours by then, so what
 * corrects itself here is the button's own icon and nothing the reader is
 * looking at. */
const DEFAULTS: Preferences = {
  theme: "system",
  palette: "plain",
  resolved: "light",
  characterLoading: true,
};

let current: Preferences = DEFAULTS;
const listeners = new Set<() => void>();

function systemPrefersDark(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-color-scheme: dark)").matches
  );
}

/** The palette has the last word on brightness, and only says anything when it
 *  was written for one. Otherwise the reader's answer stands, with "system"
 *  resolved against the OS. */
function resolve(theme: Theme, palette: PaletteId): "light" | "dark" {
  const fixed = paletteOf(palette).fixed;
  if (fixed) return fixed;
  if (theme !== "system") return theme;
  return systemPrefersDark() ? "dark" : "light";
}

/** Put the answer where CSS can see it. The same two attributes the inline
 *  script in the head sets, so the two never disagree about what the page looks
 *  like.
 *
 * Brightness is written even for a palette that fixed it, and that is the point
 * rather than a leftover: `dark:` utilities are pointed at data-theme, and a
 * light-only palette left sitting under data-theme="dark" would have every one
 * of them firing over colours that never expected them. The palette says which
 * brightness it is, and the attribute says it out loud. */
function paint(next: Preferences) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.dataset.theme = next.resolved;
  root.dataset.palette = next.palette;
}

function publish(next: Preferences) {
  current = next;
  paint(next);
  for (const listener of listeners) listener();
}

function read(): Preferences {
  if (typeof window === "undefined") return DEFAULTS;
  let stored: Partial<Preferences> = {};
  try {
    stored = JSON.parse(window.localStorage.getItem(KEY) ?? "{}");
  } catch {
    // A key someone else wrote, or a browser that refuses storage. Defaults.
  }
  const theme: Theme =
    stored.theme === "light" ||
    stored.theme === "dark" ||
    stored.theme === "system"
      ? stored.theme
      : DEFAULTS.theme;
  // Checked against the registry rather than a list written out again here, so
  // that a palette removed in a later version falls back instead of painting a
  // page out of tokens that no longer exist.
  const palette: PaletteId = PALETTES.some((p) => p.id === stored.palette)
    ? (stored.palette as PaletteId)
    : DEFAULTS.palette;
  return {
    theme,
    palette,
    resolved: resolve(theme, palette),
    characterLoading:
      typeof stored.characterLoading === "boolean"
        ? stored.characterLoading
        : DEFAULTS.characterLoading,
  };
}

function write(next: Preferences) {
  try {
    window.localStorage.setItem(
      KEY,
      JSON.stringify({
        theme: next.theme,
        palette: next.palette,
        characterLoading: next.characterLoading,
      }),
    );
  } catch {
    // Private browsing, or storage full. The setting still holds for this page.
  }
  publish(next);
}

let started = false;

/** Read what was stored and start watching for it to change underneath us.
 *
 * Called from the component that shows the buttons. Idempotent, because it is
 * the mounting of that component that triggers it and nothing guarantees the
 * component is mounted once. */
export function startPreferences(): void {
  if (started || typeof window === "undefined") return;
  started = true;
  publish(read());

  // The OS changing its mind matters only while the reader is following it, but
  // subscribing unconditionally is simpler than subscribing and unsubscribing
  // around a setting that can change at any moment.
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  media.addEventListener("change", () => {
    if (current.theme === "system") {
      publish({ ...current, resolved: resolve("system", current.palette) });
    }
  });

  // Another tab. Two windows of the same site disagreeing about the theme is
  // the kind of thing nobody reports and everybody notices.
  window.addEventListener("storage", (e) => {
    if (e.key === KEY) publish(read());
  });
}

export function subscribePreferences(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getPreferences(): Preferences {
  return current;
}

export function getPreferencesOnServer(): Preferences {
  return DEFAULTS;
}

/** system → light → dark → system.
 *
 * Following the system is the first of the three because it is the only one that
 * can be right without being chosen. */
const NEXT: Record<Theme, Theme> = {
  system: "light",
  light: "dark",
  dark: "system",
};

export function cycleTheme(): void {
  const theme = NEXT[current.theme];
  write({ ...current, theme, resolved: resolve(theme, current.palette) });
}

/** Switching palettes does not touch `theme`.
 *
 * The reader's brightness is theirs whether or not anything is currently
 * listening to it, so a light-only palette shelves the answer rather than
 * overwriting it — and leaving that palette gives them back the dark page they
 * had before, instead of the light one the palette happened to be. */
export function setPalette(palette: PaletteId): void {
  write({ ...current, palette, resolved: resolve(current.theme, palette) });
}

export function toggleCharacterLoading(): void {
  write({ ...current, characterLoading: !current.characterLoading });
}
