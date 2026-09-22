/** How many requests are in the air, and a way to be told when that changes.
 *
 * Every hook in the app fetches through one function, so the count is kept there
 * rather than assembled from a dozen `isLoading` flags — a hook added later is
 * counted without anybody remembering to count it, and one that forgets to clear
 * its own flag cannot leave the indicator stuck on.
 *
 * Deliberately not a React context. A context would put the count in a provider
 * that api-client would then have to reach into from outside React, and the
 * count is not state that belongs to a tree: it belongs to the network.
 */

let count = 0;
const listeners = new Set<() => void>();

function announce() {
  for (const listener of listeners) listener();
}

/** Called around each request. Must be paired — the caller's `finally`. */
export function beginRequest() {
  count += 1;
  if (count === 1) announce();
}

export function endRequest() {
  count -= 1;
  if (count === 0) announce();
}

/** Only the transitions in and out of idle are announced, above: a second
 *  request starting while the first is still out changes nothing anyone is
 *  watching, and waking every subscriber for it would be a re-render per
 *  request on a page that fires a handful at once. */
export function subscribeInflight(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function isBusy(): boolean {
  return count > 0;
}

/** The server is never mid-request on behalf of a page it is rendering, so its
    answer is a constant — and a constant is what useSyncExternalStore needs it
    to be, or the first client render disagrees with the markup it hydrates. */
export function isBusyOnServer(): boolean {
  return false;
}
