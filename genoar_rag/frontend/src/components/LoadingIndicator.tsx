"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import type { ReactNode } from "react";
import {
  AFLOAT,
  FRAME_MS,
  LEAVING_FRAME_MS,
  FRAME_H,
  FRAME_W,
  SUNK,
  loadingArtFor,
} from "@/components/loadingFrames";
import { isBusy, isBusyOnServer, subscribeInflight } from "@/lib/inflight";
import {
  getPreferences,
  getPreferencesOnServer,
  startPreferences,
  subscribePreferences,
} from "@/lib/preferences";
import {
  isArtReady,
  isArtReadyOnServer,
  primeArt,
  subscribeArt,
} from "@/lib/loadingArt";

/** How long the app has to stay busy before the indicator is worth showing.
 *
 * A cached answer comes back inside this, and the reader is better served by
 * nothing happening than by something appearing and leaving before they can work
 * out what it was. */
const APPEAR_AFTER = 300;

/** And how long it stays once it has appeared, however quickly the rest
 *  finishes. Something that arrives and goes in fifty milliseconds is read as a
 *  glitch rather than as an answer, so having decided to show it, show it.
 *
 *  The floor is a taste; what it is measured against is not. The leaving frames
 *  play during this pause, so it has to outlast them with a beat to spare, and
 *  it is worked out from their number rather than set beside it — otherwise a
 *  fourth leaving frame added later is a fourth frame nobody ever sees. */
function stayFor(leavingFrames: number): number {
  return Math.max(650, leavingFrames * LEAVING_FRAME_MS + 240);
}

/** The character, come to say the app is busy.
 *
 * Fixed and pointer-transparent over everything, so no layout anywhere has to
 * make room for it and nothing underneath stops being clickable while it is up.
 * It knows nothing about pages: it counts requests, and every page's data goes
 * through the same door.
 *
 * Three states, and they are not the same three as the two booleans below —
 * `busy` is the network and `shown` is the indicator, and the gap between them
 * is the two waits above:
 *
 *   busy, shown      the loop plays
 *   idle, shown      the leaving frame, held and then lowered
 *   idle, not shown  off the screen
 */
export default function LoadingIndicator() {
  const busy = useSyncExternalStore(subscribeInflight, isBusy, isBusyOnServer);
  const [shown, setShown] = useState(false);
  const [frame, setFrame] = useState(0);

  // The reader can turn the character off, and choose which one it is. Both are
  // read here rather than passed down, because nothing in the tree above this
  // has any other use for either answer.
  const { characterLoading, palette } = useSyncExternalStore(
    subscribePreferences,
    getPreferences,
    getPreferencesOnServer,
  );
  useEffect(() => startPreferences(), []);

  // Which drawings, decided by the theme. Everything below is written against
  // this and not against a set of module constants, which is what lets the
  // indicator change with the page instead of being one character for ever.
  const art = loadingArtFor(palette);

  // Once per set for the life of the tab, and again when the theme changes to
  // one whose drawings have not been fetched. primeArt refuses a set it has
  // already asked for, so going back and forth between two themes costs one
  // load each.
  useEffect(() => {
    primeArt(art.id, art.sources);
  }, [art.id, art.sources]);

  // Readiness is asked for by set. A single flag would report the first set to
  // arrive as "ready" for all of them, and the indicator would rise over frames
  // that are not there yet — which is the white rectangle the wait exists to
  // avoid, arriving by another route.
  const ready = useSyncExternalStore(
    subscribeArt,
    () => isArtReady(art.id),
    isArtReadyOnServer,
  );

  useEffect(() => {
    // Both waits live in one effect because they are one rule with two halves:
    // wait before showing, then wait before hiding. Split across two effects
    // they would race on the same piece of state.
    // Nothing rises before there is something to draw. On a cold first
    // load the drawings can lose the race to the very request that called
    // for them, and an indicator that comes up empty is a white rectangle
    // that turns into a griffin a few frames later — worse than no
    // indicator, because the reader has already looked at it. `ready` is a
    // dependency, so the wait restarts the moment the art lands.
    if (busy && ready && characterLoading && !shown) {
      const timer = setTimeout(() => setShown(true), APPEAR_AFTER);
      return () => clearTimeout(timer);
    }
    if (!busy && shown) {
      const timer = setTimeout(
        () => setShown(false),
        stayFor(art.leaving.length),
      );
      return () => clearTimeout(timer);
    }
    // Busy again before the stay ran out: the cleanup above has already killed
    // the timer that would have hidden it, and there is nothing else to do.
  }, [busy, ready, characterLoading, shown, art.leaving.length]);

  // Turned off mid-load, it goes back down the way it came rather than
  // vanishing — and `shown` is left alone, so turning it on again while the app
  // is still busy brings it straight back up.
  const up = shown && characterLoading;
  const looping = up && busy;

  useEffect(() => {
    if (!looping) return;
    // Started at the frame after whichever one was up, rather than reset to the
    // first: the interval is torn down and rebuilt every time the loop pauses
    // and resumes, and restarting from frame one each time would show the
    // character twitching back to its first pose whenever a request landed.
    const timer = setInterval(
      () => setFrame((f) => (f + 1) % art.loop.length),
      FRAME_MS,
    );
    return () => clearInterval(timer);
  }, [looping, art.loop.length]);

  return (
    <div
      className="pointer-events-none fixed bottom-0 right-0 z-[100]"
      role="status"
      aria-live="polite"
    >
      {/* The box the moving layer is measured against. It does not move itself —
          the layer carries its own transform. */}
      <div
        className="relative"
        style={{ width: FRAME_W, height: FRAME_H }}
        aria-hidden
      >
        <div
          className="loading-glide loading-character absolute inset-0"
          data-afloat={up}
          style={{ transform: up ? AFLOAT : SUNK }}
        >
          {/* Every frame is mounted and all but one is transparent, rather than
              one frame rendered at a time. That is the difference between a loop
              that plays and one that stutters through its first turn while
              frames two to five are fetched; the browser has them all decoded
              before the first is on screen. */}
          {art.loop.map((frameArt, i) => (
            <div
              key={i}
              className="absolute inset-0"
              style={{ opacity: looping && i === frame ? 1 : 0 }}
            >
              {frameArt}
            </div>
          ))}
          {/* Keyed on whether the loop is running, so that every time it stops
              this remounts and its sequence starts again from the first frame.
              The alternative is resetting an index from inside an effect, which
              is a write to state during a render pass by another name. */}
          <LeavingSequence
            key={`${art.id}:${looping}`}
            frames={art.leaving}
            hidden={looping}
          />
        </div>
      </div>
      <span className="sr-only">{up ? "Loading" : ""}</span>
    </div>
  );
}

/** The leaving frames, played through once and then held.
 *
 * Its own component so that its position in the sequence is its own state,
 * mounted fresh for each departure. It steps and stops rather than wrapping: the
 * last frame is where it stays until the whole indicator is gone. */
function LeavingSequence({
  frames,
  hidden,
}: {
  frames: ReactNode[];
  hidden: boolean;
}) {
  const [frame, setFrame] = useState(0);

  useEffect(() => {
    if (frame >= frames.length - 1) return;
    const timer = setTimeout(() => setFrame((f) => f + 1), LEAVING_FRAME_MS);
    return () => clearTimeout(timer);
  }, [frame, frames.length]);

  return (
    <>
      {frames.map((frameArt, i) => (
        <div
          key={i}
          className="absolute inset-0"
          style={{ opacity: !hidden && i === frame ? 1 : 0 }}
        >
          {frameArt}
        </div>
      ))}
    </>
  );
}
