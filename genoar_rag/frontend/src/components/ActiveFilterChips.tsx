import { displayLabel, displayCellType } from "@/lib/display";
interface ActiveFilterChipsProps {
  filters: Record<string, string[]>;
  onRemove: (category: string, value: string) => void;
  onClearAll: () => void;
  /** Spacing for this row. Belongs here rather than on a wrapper: with no
      filters the component renders nothing, and a wrapper's margin would stay
      behind as a gap above content that has nothing above it. */
  className?: string;
}

export default function ActiveFilterChips({
  filters,
  onRemove,
  onClearAll,
  className = "",
}: ActiveFilterChipsProps) {
  const hasAny = Object.values(filters).some((v) => v.length > 0);
  if (!hasAny) return null;

  return (
    <div className={`flex flex-wrap items-center gap-2 ${className}`}>
      <span className="text-sm text-ink-soft font-medium mr-1">
        Active Filters:
      </span>

      {Object.entries(filters).map(([category, values]) =>
        values.map((value) => (
          <div
            key={`${category}-${value}`}
            className="inline-flex items-center gap-1.5 px-3 py-1 bg-brand/10 text-brand rounded-full text-sm font-medium border border-edge-firm/20"
          >
            <span>
              {category === "cell_type"
                ? displayCellType(value)
                : displayLabel(value)}
            </span>
            <button
              type="button"
              onClick={() => onRemove(category, value)}
              className="hover:text-brand-soft cursor-pointer focus:outline-none"
              aria-label={`Remove filter: ${value}`}
            >
              <span className="material-symbols-outlined text-[16px] align-middle leading-none">
                close
              </span>
            </button>
          </div>
        )),
      )}

      <button
        type="button"
        onClick={onClearAll}
        className="text-sm text-ink-soft hover:text-brand underline ml-2 cursor-pointer transition-colors"
      >
        Clear All
      </button>
    </div>
  );
}
