"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import type {
  SearchFilters,
  SearchResponse,
  SearchMode,
  SortOrder,
} from "@/types/api";
import { searchSamples, ApiError } from "@/lib/api-client";
import { NO_DATASET, type Dataset } from "@/lib/datasets";

/** How a URL change is recorded in browser history. */
export type HistoryMode = "push" | "replace";

/** Results a semantic search returns when the user has not chosen a size. */
export const DEFAULT_TOP_K = 20;

const ARRAY_FILTER_KEYS = [
  "organism",
  "tissue",
  "cell_type",
  "assay_type",
  "library_source",
  "disease",
  "platform",
] as const;

/** The filters a URL is asking for.
 *
 * Exported because the header offers an export of whatever the page is
 * currently showing, and both pages keep their filters here. Reading the URL
 * costs nothing and cannot disagree with the page; calling `useSearch` from the
 * header would run the search a second time to learn what it had already asked
 * for. */
export function searchParamsToFilters(sp: URLSearchParams): SearchFilters {
  const filters: SearchFilters = {};

  for (const key of ARRAY_FILTER_KEYS) {
    const values = sp.getAll(key);
    if (values.length > 0) {
      filters[key] = values;
    }
  }

  // A keyword only means something in the vector modes. Keyword mode narrows by
  // filter alone and deliberately offers no query field, so a keyword arriving
  // from the header or left behind by a mode switch would AND itself against
  // every filter — returning nothing, with nowhere in the UI to clear it. Read
  // the mode first so the keyword can be judged against it.
  const searchMode = sp.get("search_mode");
  const isVectorMode = searchMode === "semantic" || searchMode === "hybrid";
  if (isVectorMode) {
    filters.search_mode = searchMode as SearchMode;

    const keyword = sp.get("keyword");
    if (keyword) filters.keyword = keyword;
  }

  const series = sp.get("series");
  if (series) filters.series = series;

  const offset = sp.get("offset");
  filters.offset = offset ? parseInt(offset, 10) : 0;

  const limit = sp.get("limit");
  filters.limit = limit ? parseInt(limit, 10) : 20;

  const topK = sp.get("top_k");
  if (topK) filters.top_k = parseInt(topK, 10);

  const sortBy = sp.get("sort_by");
  if (sortBy) filters.sort_by = sortBy;

  const sortOrder = sp.get("sort_order");
  if (sortOrder === "asc" || sortOrder === "desc")
    filters.sort_order = sortOrder;

  // Ask for the default explicitly. Left unset, the server falls back to its own
  // much larger pool, and the UI would show a count the controls say it did not
  // ask for. The URL still omits it, so a default search has a clean address;
  // this is only what gets requested.
  if (isVectorMode && filters.top_k == null) filters.top_k = DEFAULT_TOP_K;

  return filters;
}

function filtersToSearchParams(filters: SearchFilters): URLSearchParams {
  const params = new URLSearchParams();

  for (const key of ARRAY_FILTER_KEYS) {
    const values = filters[key];
    if (Array.isArray(values)) {
      for (const v of values) {
        if (v) params.append(key, v);
      }
    }
  }

  // The same rule on the way out, so the first thing the user touches in keyword
  // mode also cleans a stale keyword out of the address rather than leaving one
  // sitting there being ignored.
  const isVectorMode =
    filters.search_mode === "semantic" || filters.search_mode === "hybrid";
  if (filters.keyword && isVectorMode) params.set("keyword", filters.keyword);
  if (filters.series) params.set("series", filters.series);
  if (filters.offset && filters.offset > 0)
    params.set("offset", String(filters.offset));
  if (filters.limit && filters.limit !== 20)
    params.set("limit", String(filters.limit));
  if (filters.top_k && filters.top_k !== DEFAULT_TOP_K)
    params.set("top_k", String(filters.top_k));
  // `run_id` is the default only for a structured search. An AI search comes
  // back in relevance order, so dropping `sort_by=run_id` from its URL threw
  // away the request: picking "Run ID (Z→A)" left `sort_order=desc` behind with
  // nothing to sort, the select snapped back to Similarity, and the list did not
  // move. Which value is redundant depends on the mode, so the test is against
  // that mode's own default.
  const defaultSort = isVectorMode ? "similarity_score" : "run_id";
  if (filters.sort_by && filters.sort_by !== defaultSort)
    params.set("sort_by", filters.sort_by);
  if (filters.sort_order && filters.sort_order !== "asc")
    params.set("sort_order", filters.sort_order);
  if (filters.search_mode && filters.search_mode !== "sql")
    params.set("search_mode", filters.search_mode);

  return params;
}

/** A page's search.
 *
 * The dataset is the page's own, not the reader's: which corpus is being
 * described is what the two pages differ in, so it comes from the route rather
 * than the query string. It is therefore injected into what gets requested and
 * never written back into the URL — a dashboard address stays the address it
 * always was, and there is no way to put the dashboard on the wrong corpus by
 * editing it.
 */
export function useSearch(dataset: Dataset = NO_DATASET) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const abortRef = useRef<AbortController | null>(null);

  const [results, setResults] = useState<SearchResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [isSemanticUnavailable, setIsSemanticUnavailable] = useState(false);

  // Derive filters from URL, then say which corpus they are of.
  const filters = { ...searchParamsToFilters(searchParams), dataset };

  // Reset while rendering, not in the effect. React's rule is that an effect
  // body should not set state synchronously — it schedules a second render
  // before the browser paints. Adjusting during render when the key changes is
  // the documented alternative.
  //
  // The key is the query string rather than the `searchParams` object: Next
  // hands back a new object on every navigation, so comparing identity would
  // reset on renders that changed nothing.
  const searchKey = `${searchParams.toString()}|${dataset}`;
  const [lastSearchKey, setLastSearchKey] = useState(searchKey);
  if (lastSearchKey !== searchKey) {
    setLastSearchKey(searchKey);
    setIsLoading(true);
    setError(null);
    setIsSemanticUnavailable(false);
  }

  // Fetch on URL change
  useEffect(() => {
    // Cancel previous request
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const currentFilters = {
      ...searchParamsToFilters(searchParams),
      dataset,
    };

    searchSamples(currentFilters, controller.signal)
      .then(setResults)
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError && err.status === 503) {
          setIsSemanticUnavailable(true);
          return;
        }
        setError(err instanceof ApiError ? err : new ApiError(String(err)));
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });

    return () => controller.abort();
  }, [searchParams, dataset]);

  /** Whether a URL change should become its own history entry.
   *
   * Anything the user chose (a filter, a query, a page, a mode) is a step they can
   * expect Back to undo, so it pushes. Rewrites the user did not ask for, like
   * migrating a legacy `?q=` parameter, replace instead: leaving those in the
   * history would make Back appear to do nothing.
   */
  const updateUrl = useCallback(
    (newFilters: SearchFilters, history: HistoryMode = "push") => {
      const params = filtersToSearchParams(newFilters);
      const url = `?${params.toString()}`;
      if (history === "replace") {
        router.replace(url, { scroll: false });
      } else {
        router.push(url, { scroll: false });
      }
    },
    [router],
  );

  const setFilter = useCallback(
    (name: string, values: string[]) => {
      const current = searchParamsToFilters(searchParams);
      const updated = { ...current, [name]: values, offset: 0 };
      updateUrl(updated);
    },
    [searchParams, updateUrl],
  );

  const removeFilter = useCallback(
    (name: string, value: string) => {
      const current = searchParamsToFilters(searchParams);
      const existing =
        (current[name as keyof SearchFilters] as string[] | undefined) ?? [];
      const updated = {
        ...current,
        [name]: Array.isArray(existing)
          ? existing.filter((v) => v !== value)
          : existing,
        offset: 0,
      };
      updateUrl(updated);
    },
    [searchParams, updateUrl],
  );

  const clearFilters = useCallback(() => {
    const current = searchParamsToFilters(searchParams);
    const cleared: SearchFilters = {
      keyword: current.keyword,
      offset: 0,
      limit: current.limit,
      sort_by: current.sort_by,
      sort_order: current.sort_order,
      search_mode: current.search_mode,
    };
    updateUrl(cleared);
  }, [searchParams, updateUrl]);

  const setKeyword = useCallback(
    (keyword: string, history: HistoryMode = "push") => {
      const current = searchParamsToFilters(searchParams);
      updateUrl(
        { ...current, keyword: keyword || undefined, offset: 0 },
        history,
      );
    },
    [searchParams, updateUrl],
  );

  const setPage = useCallback(
    (page: number) => {
      const current = searchParamsToFilters(searchParams);
      const limit = current.limit ?? 20;
      updateUrl({ ...current, offset: (page - 1) * limit });
    },
    [searchParams, updateUrl],
  );

  /** Change how many results a semantic search returns in total.
   *
   * The page is sized to match so the whole set arrives at once: the number is
   * small enough to read in one go, and the similarity map draws the set it is
   * given rather than a slice of it. Also returns to the first page, since the
   * old offset would point into the middle of a differently sized set.
   */
  const setTopK = useCallback(
    (n: number) => {
      const current = searchParamsToFilters(searchParams);
      updateUrl({ ...current, top_k: n, limit: n, offset: 0 });
    },
    [searchParams, updateUrl],
  );

  const setSort = useCallback(
    (sortBy: string, sortOrder: SortOrder) => {
      const current = searchParamsToFilters(searchParams);
      updateUrl({
        ...current,
        sort_by: sortBy,
        sort_order: sortOrder,
        offset: 0,
      });
    },
    [searchParams, updateUrl],
  );

  const setSearchMode = useCallback(
    (mode: SearchMode) => {
      const current = searchParamsToFilters(searchParams);
      const updated: SearchFilters = {
        ...current,
        search_mode: mode,
        offset: 0,
      };
      // Reset similarity_score sort when switching to sql
      if (mode === "sql" && current.sort_by === "similarity_score") {
        updated.sort_by = undefined;
        updated.sort_order = undefined;
      }
      // Everything the vector modes carry that keyword mode cannot use. top_k
      // sizes the candidate pool, and leaving it would also hold the page at
      // whatever the semantic set was sized to. The query goes with it: keyword
      // mode narrows by filter alone, so a query left behind would just be an
      // invisible AND that empties every result.
      if (mode === "sql") {
        updated.top_k = undefined;
        updated.limit = undefined;
        updated.keyword = undefined;
      }
      updateUrl(updated);
    },
    [searchParams, updateUrl],
  );

  const currentPage =
    Math.floor((filters.offset ?? 0) / (filters.limit ?? 20)) + 1;

  return {
    results,
    isLoading,
    error,
    filters,
    currentPage,
    isSemanticUnavailable,
    setFilter,
    removeFilter,
    clearFilters,
    setKeyword,
    setPage,
    setTopK,
    setSort,
    setSearchMode,
  };
}
