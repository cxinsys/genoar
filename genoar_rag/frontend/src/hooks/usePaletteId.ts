"use client";

import { useSyncExternalStore } from "react";
import type { PaletteId } from "@/lib/preferences";
import { getPreferences, subscribePreferences } from "@/lib/preferences";

/** Which palette is showing, for the few things CSS cannot be told.
 *
 * Nearly every colour on the site is a custom property, and a palette answers
 * those without any component knowing it exists. The exceptions are the colours
 * that are computed from rather than used: a tissue system declares one hue and
 * its pale wash and its ink are derived from that hue in JavaScript, which
 * cannot be handed a var().
 *
 * The snapshot is one field and not the whole preferences object, so a change of
 * brightness or of the loading character does not re-render a chart. */
export function usePaletteId(): PaletteId {
  return useSyncExternalStore(
    subscribePreferences,
    () => getPreferences().palette,
    () => "plain" as const,
  );
}
