import { useEffect, useState } from "react";
import { checkHealth } from "@/lib/api-client";

/** Species the backend can actually run semantic search for.
 *
 * Which indexes are loaded depends on the deployment, so the UI asks rather than
 * assuming. Until the answer arrives, `isLoading` is true and callers should not
 * disable anything: briefly offering a species that turns out to be unavailable
 * is better than greying out one that works.
 */
export function useSemanticSpecies() {
  const [species, setSpecies] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();

    checkHealth(controller.signal)
      .then((health) => setSpecies(health.semantic_species ?? []))
      .catch(() => setSpecies([]))
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false);
      });

    return () => controller.abort();
  }, []);

  return { species, isLoading };
}
