import { useEffect, useState } from "react";
import type { SimilarSamplesResponse } from "@/types/api";
import { getSimilarSamples, ApiError } from "@/lib/api-client";
import { NO_DATASET, type Dataset } from "@/lib/datasets";

export function useSimilarSamples(
  runId: string,
  limit: number = 10,
  dataset: Dataset = NO_DATASET,
) {
  const [data, setData] = useState<SimilarSamplesResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [isUnavailable, setIsUnavailable] = useState(false);

  // Reset while rendering, not in the effect. React's rule is that an effect
  // body should not set state synchronously — it schedules a second render
  // before the browser paints. Adjusting during render when the key changes
  // is the documented alternative, and it also drops the frame where the new
  // key was on screen beside the previous key's data.
  const [lastKey, setLastKey] = useState(`${runId}|${limit}|${dataset}`);
  if (lastKey !== `${runId}|${limit}|${dataset}`) {
    setLastKey(`${runId}|${limit}|${dataset}`);
    setIsLoading(true);
    setError(null);
    setIsUnavailable(false);
  }

  useEffect(() => {
    const controller = new AbortController();

    getSimilarSamples(runId, limit, controller.signal, dataset)
      .then(setData)
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof ApiError && err.status === 503) {
          setIsUnavailable(true);
          return;
        }
        setError(err instanceof ApiError ? err : new ApiError(String(err)));
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });

    return () => controller.abort();
  }, [runId, limit, dataset]);

  return { data, isLoading, error, isUnavailable };
}
