import { useEffect, useState } from "react";
import type { PaginatedResponse, SampleSummary } from "@/types/api";
import { getSeriesSamples, ApiError } from "@/lib/api-client";
import { type Dataset } from "@/lib/datasets";

export function useSeriesSamples(
  runId: string,
  params: { offset?: number; limit?: number; dataset?: Dataset },
) {
  const [data, setData] = useState<PaginatedResponse<SampleSummary> | null>(
    null,
  );
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  // Reset while rendering, not in the effect. React's rule is that an effect
  // body should not set state synchronously — it schedules a second render
  // before the browser paints. Adjusting during render when the key changes
  // is the documented alternative, and it also drops the frame where the new
  // key was on screen beside the previous key's data.
  const [lastKey, setLastKey] = useState(
    `${runId}|${params.offset}|${params.limit}|${params.dataset}`,
  );
  const key = `${runId}|${params.offset}|${params.limit}|${params.dataset}`;
  if (lastKey !== key) {
    setLastKey(key);
    setIsLoading(true);
    setError(null);
  }

  useEffect(() => {
    const controller = new AbortController();

    getSeriesSamples(runId, params, controller.signal)
      .then(setData)
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof ApiError ? err : new ApiError(String(err)));
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });

    return () => controller.abort();
  }, [runId, params.offset, params.limit, params.dataset]);

  return { data, isLoading, error };
}
