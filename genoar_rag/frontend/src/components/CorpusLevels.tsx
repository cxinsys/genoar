"use client";

import { useStats } from "@/hooks/useStats";
import { DASHBOARD_DATASET } from "@/lib/datasets";
import type { SpeciesCorpus } from "@/types/api";

/** The corpus at each level it exists at, read live from the stats endpoint.
 *
 * Read rather than written into the page so that the scope page and the
 * dashboard cannot disagree — both are the same numbers from the same
 * query, and a database update moves both at once. The row reads from the
 * coarsest unit to the finest, as the dashboard's cards do. */
export default function CorpusLevels() {
  const { stats, isLoading } = useStats(DASHBOARD_DATASET);

  if (isLoading || !stats) {
    return <div className="h-24 bg-sunken rounded-lg animate-pulse" />;
  }

  const levels: {
    label: string;
    total: string;
    perSpecies: (species: SpeciesCorpus) => number | null;
    note?: string;
  }[] = [
    {
      label: "Studies",
      total: stats.total_series.toLocaleString(),
      perSpecies: (c) => c.series,
      note:
        stats.shared_series > 0
          ? `${stats.shared_series} studies have runs of both species and are counted under each, so the species figures sum to ${(stats.total_series + stats.shared_series).toLocaleString()}.`
          : undefined,
    },
    {
      label: "GEO samples",
      total: stats.total_geo_samples.toLocaleString(),
      perSpecies: (c) => c.geo_samples,
    },
    {
      label: "Runs",
      total: stats.total_samples.toLocaleString(),
      perSpecies: (c) => c.runs,
      note: "Each run has its own processed matrix. A sample sequenced across several lanes has several runs; see the merge guide.",
    },
    {
      label: "Cells",
      total: stats.total_cells != null ? stats.total_cells.toLocaleString() : "—",
      perSpecies: (c) => c.cells,
      note:
        stats.total_cells == null
          ? "No cell census has been taken on this deployment."
          : "Cells called by Cell Ranger in each run's filtered matrix, summed.",
    },
  ];

  return (
    <div className="overflow-x-auto border border-edge rounded-lg">
      <table className="w-full text-left border-collapse">
        <thead className="bg-raised text-xs uppercase text-ink-soft border-b border-edge">
          <tr>
            <th className="px-4 py-3 font-semibold">Level</th>
            <th className="px-4 py-3 font-semibold text-right">Total</th>
            {stats.corpus.map((c) => (
              <th key={c.species} className="px-4 py-3 font-semibold text-right">
                <span className="italic normal-case">{c.organism}</span>
              </th>
            ))}
            <th className="px-4 py-3 font-semibold">Note</th>
          </tr>
        </thead>
        <tbody className="text-sm divide-y divide-edge">
          {levels.map((l) => (
            <tr key={l.label}>
              <td className="px-4 py-3 text-ink-body">{l.label}</td>
              <td className="px-4 py-3 text-right font-mono text-ink whitespace-nowrap">
                {l.total}
              </td>
              {stats.corpus.map((c) => {
                const n = l.perSpecies(c);
                return (
                  <td
                    key={c.species}
                    className="px-4 py-3 text-right font-mono text-ink whitespace-nowrap"
                  >
                    {n != null ? n.toLocaleString() : "—"}
                  </td>
                );
              })}
              <td className="px-4 py-3 text-xs text-ink-soft">{l.note ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
