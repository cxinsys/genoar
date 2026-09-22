import { useEffect, useState } from "react";
import type { SampleDetail } from "@/types/api";
import { getSampleDetail, ApiError, NotFoundError } from "@/lib/api-client";
import { NO_DATASET, type Dataset } from "@/lib/datasets";

export function useSampleDetail(
  runId: string,
  dataset: Dataset = NO_DATASET,
) {
  const [sample, setSample] = useState<SampleDetail | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [isNotFound, setIsNotFound] = useState(false);

  // Reset while rendering, not in the effect. React's rule is that an effect
  // body should not set state synchronously — it schedules a second render
  // before the browser paints. Adjusting during render when the key changes
  // is the documented alternative, and it also drops the frame where the new
  // key was on screen beside the previous key's data.
  const key = `${runId}|${dataset}`;
  const [lastKey, setLastKey] = useState(key);
  if (lastKey !== key) {
    setLastKey(key);
    setIsLoading(true);
    setError(null);
    setIsNotFound(false);
  }

  useEffect(() => {
    const controller = new AbortController();

    getSampleDetail(runId, controller.signal, dataset)
      .then(setSample)
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        if (err instanceof NotFoundError) {
          setIsNotFound(true);
        }
        setError(err instanceof ApiError ? err : new ApiError(String(err)));
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });

    return () => controller.abort();
  }, [runId, dataset]);

  return { sample, isLoading, error, isNotFound };
}
