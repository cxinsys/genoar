/** Whether this page has a side panel, and whether it is showing.
 *
 * The button that opens it is in the header, which the root layout renders once
 * for the whole site; the thing it opens belongs to a page, which the header
 * knows nothing about and outlives. So the two are joined by a store rather than
 * by props — the page mounts a panel and says so here, and the header reads it.
 *
 * `available` is a count and not a flag because mounting is not ordered: moving
 * between two pages that both have one renders the next before unmounting the
 * last, and a flag would be set true, set true again, then cleared by the page
 * that was leaving — leaving the button gone on a page that has a panel.
 *
 * Written like the other stores here: a value, a set of listeners, and a
 * snapshot rebuilt only when something changed, because useSyncExternalStore
 * compares snapshots by identity.
 */

export interface SidePanelState {
  open: boolean;
  available: number;
}

const CLOSED: SidePanelState = { open: false, available: 0 };

let current: SidePanelState = CLOSED;
const listeners = new Set<() => void>();

function publish(next: SidePanelState) {
  if (next.open === current.open && next.available === current.available)
    return;
  current = next;
  for (const listener of listeners) listener();
}

export function subscribeSidePanel(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getSidePanel(): SidePanelState {
  return current;
}

/** The server has no panel mounted and nothing open, and says so the same way
    every time, which is what hydration needs. */
export function getSidePanelOnServer(): SidePanelState {
  return CLOSED;
}

/** Called by a panel as it mounts. The returned function is its unmount.
 *
 * Closing on the way out is the part worth stating: a panel left open while the
 * page under it is replaced would either follow the reader to a page it does not
 * belong to, or — once the count reaches zero — stay open with no way back to
 * the button that closes it. */
export function registerSidePanel(): () => void {
  publish({ ...current, available: current.available + 1 });
  return () => {
    const available = Math.max(0, current.available - 1);
    publish({ open: available > 0 && current.open, available });
  };
}

export function toggleSidePanel(): void {
  publish({ ...current, open: !current.open });
}

export function closeSidePanel(): void {
  publish({ ...current, open: false });
}
