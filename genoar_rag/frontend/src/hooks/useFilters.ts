import { useEffect, useState, useCallback } from "react";
import type { FilterOptionsResponse, FilterCategory } from "@/types/api";
import { getFilters, ApiError } from "@/lib/api-client";
import { NO_DATASET, type Dataset } from "@/lib/datasets";

export function useFilters(dataset: Dataset = NO_DATASET) {
  const [filters, setFilters] = useState<FilterOptionsResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    // No reset here: this effect runs once, and the initial state is
    // already loading-with-no-error. Setting it again synchronously in
    // the effect body only cost a render.
    getFilters(controller.signal, dataset)
      .then(setFilters)
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof ApiError ? err : new ApiError(String(err)));
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });

    return () => controller.abort();
  }, [dataset]);

  const getCategory = useCallback(
    (name: string): FilterCategory | undefined => {
      return filters?.categories.find((c) => c.name === name);
    },
    [filters],
  );

  return { filters, isLoading, error, getCategory };
}
