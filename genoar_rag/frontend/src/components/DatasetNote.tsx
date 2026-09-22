"use client";

import { useEffect, useState } from "react";

import { getDatasets } from "@/lib/api-client";
import type { Dataset, DatasetsResponse } from "@/lib/datasets";

/** Which body of data this page is showing, said out loud — when it matters.
 *
 * A deployment serving one dataset renders nothing here. Naming the corpus on
 * every page when there is only one tells a reader nothing and teaches them to
 * stop reading the line, so it appears at the moment it starts carrying
 * information: when the pages can differ.
 *
 * The names and labels come from the server rather than from a table compiled
 * in here. Which bodies of data a deployment holds is that deployment's
 * configuration, and a client holding its own copy would have to be rebuilt to
 * learn of a change and could disagree about what exists.
 */
export default function DatasetNote({
  dataset,
  className = "",
}: {
  dataset: Dataset;
  className?: string;
}) {
  const [config, setConfig] = useState<DatasetsResponse | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    getDatasets(controller.signal)
      .then(setConfig)
      // Silent: this is a caption. A deployment whose config cannot be read has
      // a louder problem than a missing line, and every other panel on the page
      // will be saying so.
      .catch(() => {})
      .finally(() => {});
    return () => controller.abort();
  }, []);

  if (!config?.is_split) return null;

  const name = dataset ?? config.default;
  const label = config.items.find((d) => d.name === name)?.label ?? name;

  return (
    <span className={`type-panel-sub ${className}`.trim()}>{label}</span>
  );
}
