"use client";

import { displayLabel, displayCellType } from "@/lib/display";
import ScrollFade from "@/components/ScrollFade";
import { useFilterCategory } from "@/hooks/useFilterCategory";
import type { FilterValue } from "@/types/api";
import { NO_DATASET, type Dataset } from "@/lib/datasets";

// Below this a category is small enough to read at a glance, and a search box
// would be more chrome than help.
const SEARCH_THRESHOLD = 20;

type FilterCategoryListProps = {
  category: string;
  label: string;
  selected: string[];
  onToggle: (value: string) => void;
  /** The category's first page, already fetched with the rest of the filters. */
  initialValues: FilterValue[];
  initialTotal: number;
  dataset?: Dataset;
};

/**
 * One category in the filter sidebar: its values, a search box when there are
 * enough of them to need one, and the rest of the list fetched as it is reached.
 */
export default function FilterCategoryList({
  category,
  label,
  selected,
  onToggle,
  initialValues,
  initialTotal,
  dataset = NO_DATASET,
}: FilterCategoryListProps) {
  const {
    values,
    total,
    query,
    setQuery,
    clearSearch,
    loadMore,
    hasMore,
    isLoading,
  } = useFilterCategory(category, { initialValues, initialTotal, dataset });

  const showSearch = initialTotal > SEARCH_THRESHOLD;
  const isEmpty = values.length === 0 && !isLoading;

  return (
    <div>
      <div className="flex items-baseline justify-between mb-2 gap-2">
        <h4 className="type-eyebrow">{label}</h4>
        <span className="text-[11px] text-ink-faint tabular-nums shrink-0">
          {values.length < total
            ? `${values.length} / ${total.toLocaleString()}`
            : total.toLocaleString()}
        </span>
      </div>

      {showSearch && (
        <div className="relative mb-2">
          {/* The size carries ! because the icon font's own stylesheet declares
              font-size: 24px outside any cascade layer, and an unlayered rule
              outranks every Tailwind utility however specific. */}
          <span className="material-symbols-outlined text-[14px]! leading-none text-ink-faint absolute left-2 top-1/2 -translate-y-1/2 pointer-events-none">
            search
          </span>
          {/* type=text, not search: a search input draws the browser's own clear
              button, which would sit beside the one below doing the same job. */}
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              // Only when there is something to clear. An empty box lets
              // Escape through to whatever is listening above — the side
              // panel closes on it — so the key does not become a dead
              // stroke inside this field.
              if (e.key === "Escape" && query) {
                e.preventDefault();
                e.stopPropagation();
                clearSearch();
              }
            }}
            placeholder={`Search ${label.toLowerCase()}`}
            aria-label={`Search ${label}`}
            data-testid={`filter-search-${category}`}
            className="w-full bg-sunken border border-edge rounded-md py-1 pl-7 pr-6 text-xs text-ink-body placeholder-ink-faint focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
          />
          {query && (
            <button
              type="button"
              onClick={clearSearch}
              aria-label={`Clear ${label} search`}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 flex items-center justify-center text-ink-faint hover:text-ink-body cursor-pointer"
            >
              {/* leading-none as well as the size: the icon font's 24px line box
                  is what left the glyph sitting above the field's centre. */}
              <span className="material-symbols-outlined text-[14px]! leading-none">
                close
              </span>
            </button>
          )}
        </div>
      )}

      <ScrollFade className="space-y-1 max-h-40 scroll-stable" height="2.5rem">
        {values.map((fv) => (
          <label
            key={fv.value}
            className="flex items-center gap-2 px-2 py-1.5 rounded-md hover:bg-raised cursor-pointer"
          >
            <input
              type="checkbox"
              checked={selected.includes(fv.value)}
              onChange={() => onToggle(fv.value)}
              className="rounded border-edge-firm size-4 cursor-pointer"
              style={{ accentColor: "var(--color-brand)" }}
            />
            {/* min-w-0 so the name can actually shrink: a flex item defaults to
                min-width auto and would push the row wider than the sidebar
                rather than ellipsise. */}
            {/* Written in sentence case, with the curated table's own
                spelling kept on the tooltip and in what the filter sends. */}
            <span
              className="text-sm text-ink-body flex-1 min-w-0 truncate"
              title={fv.value}
            >
              {category === "cell_type"
                ? displayCellType(fv.value)
                : displayLabel(fv.value)}
            </span>
            <span className="ml-auto text-xs text-ink-faint tabular-nums shrink-0">
              {fv.count.toLocaleString()}
            </span>
          </label>
        ))}

        {isEmpty && (
          <p className="text-xs text-ink-faint px-2 py-1.5">
            {query ? `No match for "${query}"` : "No values"}
          </p>
        )}

        {/* Fetching the next page is the button's job alone. Loading on reaching
            the end instead meant the list grew whenever it was scrolled through,
            which is not what someone scanning a list is asking for. */}
        {hasMore && (
          <button
            type="button"
            onClick={loadMore}
            disabled={isLoading}
            data-testid={`filter-more-${category}`}
            className="w-full text-left text-xs font-medium text-brand px-2 py-1.5 rounded-md hover:bg-raised cursor-pointer disabled:cursor-default"
          >
            {isLoading
              ? "Loading…"
              : `Show more (${(total - values.length).toLocaleString()} left)`}
          </button>
        )}
      </ScrollFade>
    </div>
  );
}
