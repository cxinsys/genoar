/** Whether the indicator's drawings are in the browser and decoded.
 *
 * They are fetched once, when the app first runs, and never again: the layout
 * that holds the indicator survives every route change, so what is loaded here
 * stays loaded for the life of the tab.
 *
 * The point of asking is the moment before that. Until the frames have arrived
 * there is nothing to draw, and an indicator that rises anyway is a white
 * rectangle that becomes a griffin a few frames later — which is worse than not
 * rising at all, because the reader has already looked at it.
 *
 * `decode()` and not `onload`: a decoded image is one that can be painted this
 * frame. An image that has arrived but not been decoded still flashes.
 */

/* Per set, because there is a set of drawings per palette. A single flag says
   "ready" the moment any one set has loaded, so switching theme mid-visit raises
   the indicator over frames that have not arrived — the white rectangle this
   file exists to prevent. */

const ready = new Set<string>();
const started = new Set<string>();
const listeners = new Set<() => void>();

export function subscribeArt(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function isArtReady(id: string): boolean {
  return ready.has(id);
}

/** The server has no browser to load anything into, so it answers no — and
    answers it the same way every time, which is what hydration needs. */
export function isArtReadyOnServer(): boolean {
  return false;
}

/** Begin loading one set, once per page load however many times this is called.
 *
 * The same files are already in the markup as <img> elements, so this asks for
 * nothing the browser was not fetching anyway — one request each, served from
 * the same cache. What it adds is the telling: an <img> in a React tree has no
 * way to say "all of us are ready" without every frame reporting up through
 * props it otherwise has no use for.
 *
 * A set already asked for is not asked for again, so moving back and forth
 * between two themes costs one load each and nothing after that.
 */
export function primeArt(id: string, sources: readonly string[]): void {
  if (started.has(id) || typeof window === "undefined") return;
  started.add(id);

  Promise.all(
    sources.map((src) => {
      const img = new Image();
      img.src = src;
      // A failed decode resolves rather than rejects: one drawing that will not
      // load should not hold the others off the screen for ever.
      return img.decode().catch(() => undefined);
    }),
  ).then(() => {
    ready.add(id);
    for (const listener of listeners) listener();
  });
}
