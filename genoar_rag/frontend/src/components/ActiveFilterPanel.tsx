import { displayLabel, displayCellType } from "@/lib/display";
interface ActiveFilterPanelProps {
  /** Selected values by filter key, e.g. { tissue: ["Lung"] }. */
  filters: Record<string, string[]>;
  /** Display name per filter key. Keys the caller does not name fall back to the key. */
  labels: Record<string, string>;
  onRemove: (category: string, value: string) => void;
  onClearAll: () => void;
}

/** The sidebar's account of what the search is narrowed to.
 *
 * Beside the results, in the column that already answers "what did this search
 * do". The same information as a row of chips above them pushes the results down
 * the page when the list is long, and reads as part of the query form rather
 * than as its state.
 *
 * Grouped by category, which a row cannot do: values alone ("Lung",
 * "Homo sapiens") are only self-explaining while there are few of them, and the
 * narrow column has room for the heading a wide row does not.
 */
export default function ActiveFilterPanel({
  filters,
  labels,
  onRemove,
  onClearAll,
}: ActiveFilterPanelProps) {
  const groups = Object.entries(filters).filter(
    ([, values]) => values.length > 0,
  );
  const count = groups.reduce((n, [, values]) => n + values.length, 0);

  return (
    <div className="bg-surface border border-edge rounded-card p-5 shadow-card">
      <div className="flex items-center justify-between gap-2 mb-3">
        <h3 className="type-eyebrow flex items-center gap-1.5">
          <span className="material-symbols-outlined text-[16px] leading-none">
            filter_alt
          </span>
          Active Filters
        </h3>
        {/* The selectors above the results carry this same button, for the same
            action. One appearance, one name. */}
        {count > 0 && (
          <button
            type="button"
            onClick={onClearAll}
            data-testid="panel-clear-filters"
            className="text-sm text-brand font-medium hover:underline cursor-pointer flex items-center gap-1 transition-colors"
          >
            <span className="material-symbols-outlined text-[18px]">
              refresh
            </span>
            Reset All
          </button>
        )}
      </div>

      {/* Rendered even when empty. The card is the answer to "why this many
          results", and a box that disappears when the answer is "nothing is
          filtered" leaves that question unanswered — and moves everything
          under it each time a filter is added or dropped. */}
      {count === 0 ? (
        <p className="text-sm text-ink-faint">
          No filters applied. Narrow the search with the selectors above the
          results.
        </p>
      ) : (
        <div className="space-y-3">
          {groups.map(([category, values]) => (
            <div key={category}>
              <span className="type-eyebrow text-ink-faint">
                {labels[category] ?? category}
              </span>
              <div className="flex flex-wrap gap-1.5 mt-1.5">
                {values.map((value) => (
                  <span
                    key={value}
                    className="inline-flex max-w-full items-center gap-1 pl-2.5 pr-1.5 py-1 bg-brand/10 text-brand rounded-full text-xs font-medium border border-edge-firm/20"
                  >
                    {/* A cell type or a tissue name can be longer than the
                        column is wide, so the chip wraps rather than pushing
                        the card's own edge out. */}
                    <span className="min-w-0 break-words">
                      {category === "cell_type"
                        ? displayCellType(value)
                        : displayLabel(value)}
                    </span>
                    <button
                      type="button"
                      onClick={() => onRemove(category, value)}
                      aria-label={`Remove filter: ${value}`}
                      className="hover:text-brand-soft cursor-pointer focus:outline-none flex-shrink-0"
                    >
                      <span className="material-symbols-outlined text-[14px] align-middle leading-none">
                        close
                      </span>
                    </button>
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
