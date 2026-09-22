import { useEffect, useState } from "react";
import type { SampleDownloadResponse } from "@/types/api";
import { getSampleDownload, ApiError } from "@/lib/api-client";
import { NO_DATASET, type Dataset } from "@/lib/datasets";

export function useSampleDownload(
  accession: string,
  dataset: Dataset = NO_DATASET,
) {
  const [data, setData] = useState<SampleDownloadResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  // Reset while rendering, not in the effect. React's rule is that an effect
  // body should not set state synchronously — it schedules a second render
  // before the browser paints. Adjusting during render when the key changes
  // is the documented alternative, and it also drops the frame where the new
  // key was on screen beside the previous key's data.
  const [lastKey, setLastKey] = useState(accession);
  if (lastKey !== accession) {
    setLastKey(accession);
    setIsLoading(true);
    setError(null);
  }

  useEffect(() => {
    const controller = new AbortController();

    getSampleDownload(accession, controller.signal, dataset)
      .then(setData)
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof ApiError ? err : new ApiError(String(err)));
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });

    return () => controller.abort();
  }, [accession, dataset]);

  return { data, isLoading, error };
}
