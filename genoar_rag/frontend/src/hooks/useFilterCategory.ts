"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getFilterCategory } from "@/lib/api-client";
import type { FilterValue } from "@/types/api";
import { NO_DATASET, type Dataset } from "@/lib/datasets";

// Must equal DEFAULT_PAGE_SIZE in the backend's filter_service: the combined
// /api/v1/filters payload carries that many values per category, and this hook
// treats them as page one. Let the two drift and it either refetches rows it
// already holds or skips rows it never got.
const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 250;

type UseFilterCategoryOptions = {
  /** Values already in hand from `/api/v1/filters`, used as the first page. */
  initialValues?: FilterValue[];
  initialTotal?: number;
  /** Which corpus's values these are. The two hold different ones. */
  dataset?: Dataset;
};

/**
 * One filter category's values, fetched a page at a time and searchable.
 *
 * `/api/v1/filters` supplies the first page for every category at once, so a
 * category that fits in that page never fetches anything: it starts complete
 * and stays that way unless the user searches.
 */
export function useFilterCategory(
  category: string,
  {
    initialValues = [],
    initialTotal = 0,
    dataset = NO_DATASET,
  }: UseFilterCategoryOptions = {},
) {
  const [values, setValues] = useState<FilterValue[]>(initialValues);
  const [total, setTotal] = useState(initialTotal);
  const [query, setQuery] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  // The page in flight, so a slow response cannot append to a list that has
  // since been replaced by a search or a reset.
  const requestRef = useRef<AbortController | null>(null);
  const seededRef = useRef(false);

  // The parent's first page arrives after this hook mounts, so adopt it once —
  // but never over the top of a search or of pages already fetched.
  useEffect(() => {
    if (seededRef.current || query || initialValues.length === 0) return;
    seededRef.current = true;
    setValues(initialValues);
    setTotal(initialTotal);
  }, [initialValues, initialTotal, query]);

  const fetchPage = useCallback(
    async (nextQuery: string, offset: number) => {
      requestRef.current?.abort();
      const controller = new AbortController();
      requestRef.current = controller;

      setIsLoading(true);
      setError(null);
      try {
        const page = await getFilterCategory(
          category,
          { offset, limit: PAGE_SIZE, q: nextQuery || undefined, dataset },
          controller.signal,
        );
        // Still the current request, or these values belong to a list that no
        // longer exists. Aborting relies on `fetch` rejecting, which it cannot
        // do for a response that had already arrived — so a search resolving in
        // the moment before the box is emptied would land on top of the list
        // just restored, leaving an empty box above a filtered list.
        if (requestRef.current !== controller) return;
        setTotal(page.total_distinct);
        setValues((prev) =>
          offset === 0 ? page.values : [...prev, ...page.values],
        );
      } catch (err) {
        if ((err as Error).name === "AbortError") return;
        // And the same check the success path makes, for the same reason. An
        // abort rejects with `AbortError` and leaves above, but a request that
        // had already failed before it was replaced rejects with whatever it
        // failed with — and that message would then sit over a search that has
        // not failed, or over the restored list.
        if (requestRef.current !== controller) return;
        setError(err as Error);
      } finally {
        if (requestRef.current === controller) setIsLoading(false);
      }
    },
    [category, dataset],
  );

  // The first page as the parent last supplied it, read by the effect below
  // without being a dependency of it. `category.values` keeps its identity once
  // the filters have loaded, but depending on it would make emptying the box
  // re-run on any render that did replace it — and this effect writes state.
  const initialRef = useRef({ values: initialValues, total: initialTotal });
  initialRef.current = { values: initialValues, total: initialTotal };

  // Searching restarts from the first page. Debounced so typing a word is one
  // request rather than one per keystroke.
  //
  // Emptying the box puts the whole list back, which it did not: this returned
  // early on a falsy query, so nothing refetched and whatever the last search
  // had matched stayed on screen. Delete a search that matched nothing and the
  // category was simply empty — no values, no way to get them back but the
  // clear button, which is the one path that did restore them. Backspacing to
  // empty is the same request as pressing that button, so it does the same
  // thing.
  useEffect(() => {
    // Whatever is in flight was asked for the query before this one, so it is
    // stale the moment this one differs — whether the box was emptied or typed
    // into further. Dropped here rather than only on the empty branch: a search
    // for "b" that resolves during the pause before "bl" is sent is still the
    // current request as far as `fetchPage` is concerned, and its results would
    // appear under a box that no longer says "b".
    //
    // Cleared as well as aborted, because the check in `fetchPage` is against
    // this reference and an aborted controller left in place still matches
    // itself.
    requestRef.current?.abort();
    requestRef.current = null;
    // A failure of the query that has just been replaced is not a failure of
    // this one.
    setError(null);

    if (!query) {
      // `fetchPage`'s own `finally` cannot lower this. It does so only while
      // its controller is still the current one, which the line above has just
      // stopped being — so emptying the box mid-search left the flag up on a
      // request nobody was waiting for, and `Show more` disabled with it.
      setIsLoading(false);
      setValues(initialRef.current.values);
      setTotal(initialRef.current.total);
      return;
    }

    // Loading from the moment a search is scheduled, not from when it is sent.
    // For the quarter second in between, the list on screen answers the
    // previous query while `hasMore` is still true of it — and `Show more` is
    // only disabled while this is set. Pressing it there asked for page two of
    // a list about to be replaced, and the new query's first page and the old
    // query's second page would both land.
    setIsLoading(true);
    const timer = setTimeout(() => fetchPage(query, 0), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [query, fetchPage]);

  // Just empties the box: the effect above restores the list, aborts anything
  // in flight and is the same path a backspace takes. Doing it here as well was
  // how the two came to disagree — this one put the values back and emptying
  // the box by hand did not.
  const clearSearch = useCallback(() => setQuery(""), []);

  const hasMore = values.length < total;

  const loadMore = useCallback(() => {
    if (isLoading || !hasMore) return;
    fetchPage(query, values.length);
  }, [fetchPage, hasMore, isLoading, query, values.length]);

  useEffect(() => () => requestRef.current?.abort(), []);

  return {
    values,
    total,
    query,
    setQuery,
    clearSearch,
    loadMore,
    hasMore,
    isLoading,
    error,
  };
}
