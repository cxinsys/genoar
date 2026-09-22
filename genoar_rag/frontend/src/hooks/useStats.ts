import { useEffect, useState } from "react";
import type { DashboardStats } from "@/types/api";
import { getStats, ApiError } from "@/lib/api-client";
import { NO_DATASET, type Dataset } from "@/lib/datasets";

export function useStats(dataset: Dataset = NO_DATASET) {
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    // No reset here: this effect runs once, and the initial state is
    // already loading-with-no-error. Setting it again synchronously in
    // the effect body only cost a render.
    getStats(controller.signal, dataset)
      .then(setStats)
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof ApiError ? err : new ApiError(String(err)));
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });

    return () => controller.abort();
  }, [dataset]);

  return { stats, isLoading, error };
}
