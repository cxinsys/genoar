"use client";

import { useState, useRef, useEffect } from "react";
import { useFilterCategory } from "@/hooks/useFilterCategory";
import { displayLabel, displayCellType } from "@/lib/display";
import type { FilterCategory } from "@/types/api";

interface FilterDropdownProps {
  label: string;
  category: FilterCategory;
  selected: string[];
  onChange: (values: string[]) => void;
}

/** Width of the popover below — Tailwind's w-72, needed as a number to work out
    whether it fits where it is about to open. */
const POPOVER_WIDTH = 288;

/** How far right the popover may extend before something clips it.
 *
 * The results column is a scroll container of its own, and a scroll container
 * clips on both axes however only one was asked to scroll. So the edge that
 * matters is the nearest scrolling ancestor's, not the window's — a popover
 * opening past it would be cut off, or drag a horizontal scrollbar into a column
 * that has no business scrolling sideways.
 */
function clippingRight(el: HTMLElement): number {
  for (let node = el.parentElement; node; node = node.parentElement) {
    if (getComputedStyle(node).overflowX !== "visible") {
      return node.getBoundingClientRect().right;
    }
  }
  return window.innerWidth;
}

export default function FilterDropdown({
  label,
  category,
  selected,
  onChange,
}: FilterDropdownProps) {
  const [isOpen, setIsOpen] = useState(false);
  // Values, and the search over them, from the server.
  //
  // The search must not be run over `category.values` in the browser.
  // `/api/v1/filters` sends only the first fifty of each category — the fifty
  // with the most samples — so filtering that list locally leaves 245 of the 295
  // tissues and 260 of the 310 cell types not merely off the list but impossible
  // to type into it: "midbrain" finds nothing while a sample page beside it
  // shows Midbrain as its tissue. The hook asks the server, which is what the
  // same box on the home page does.
  const {
    values,
    total,
    query: search,
    setQuery,
    clearSearch,
    loadMore,
    hasMore,
    isLoading,
  } = useFilterCategory(category.name, {
    initialValues: category.values,
    initialTotal: category.total_distinct,
  });
  // Which edge the popover hangs from. Decided when it opens, from where the
  // button actually is: the last selectors in the row sit close enough to the
  // column's edge that a left-aligned popover would not fit.
  const [alignRight, setAlignRight] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    setAlignRight(
      rect.left + POPOVER_WIDTH > clippingRight(containerRef.current),
    );
  }, [isOpen]);

  // Close on outside click
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(e.target as Node)
      ) {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      return () =>
        document.removeEventListener("mousedown", handleClickOutside);
    }
  }, [isOpen]);

  // A value already ticked but not on the page in view — because the list has
  // been searched, or because it sits below the fifty loaded — still has to be
  // shown with its box ticked, or unticking it would be impossible.
  // `count: null` rather than 0 — the number is not known here, and a literal
  // 0 beside a value that is currently filtering the results reads as "no
  // samples have this", which is the opposite of true.
  const shown = values.map((v) => v.value);
  const missingSelected: { value: string; count: number | null }[] = selected
    .filter((v) => !shown.includes(v))
    .map((value) => ({ value, count: null }));
  const filteredValues: { value: string; count: number | null }[] = [
    ...missingSelected,
    ...values,
  ];

  function toggleValue(value: string) {
    if (selected.includes(value)) {
      onChange(selected.filter((v) => v !== value));
    } else {
      onChange([...selected, value]);
    }
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex items-center gap-2 px-3 py-2 bg-surface border border-edge rounded-lg text-sm font-medium text-ink-body hover:bg-raised cursor-pointer transition-colors group"
      >
        <span>{label}</span>
        {selected.length > 0 && (
          <span className="bg-brand text-on-brand px-1.5 rounded text-xs font-semibold">
            {selected.length}
          </span>
        )}
        <span className="material-symbols-outlined text-[18px] text-ink-faint group-hover:text-brand">
          {isOpen ? "expand_less" : "expand_more"}
        </span>
      </button>

      {isOpen && (
        <div
          className={`absolute top-full mt-1 w-72 bg-surface border border-edge rounded-card shadow-overlay z-50 overflow-hidden ${
            alignRight ? "right-0" : "left-0"
          }`}
        >
          {/* Search input */}
          <div className="p-2 border-b border-edge">
            <div className="relative">
              <span className="absolute inset-y-0 left-2 flex items-center">
                <span className="material-symbols-outlined text-ink-faint text-[16px]">
                  search
                </span>
              </span>
              <input
                type="text"
                value={search}
                // Plain, because the hook restores the opening list when the
                // box goes empty. Routing a cleared box through `clearSearch`
                // instead is a workaround only the call site that has it is
                // fixed by — the dashboard's list, without it, empties and
                // stays empty.
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  // Only when there is something to clear. An empty box lets
                  // Escape through to whatever is listening above — the side
                  // panel closes on it — so the key does not become a dead
                  // stroke inside this field.
                  if (e.key === "Escape" && search) {
                    e.preventDefault();
                    e.stopPropagation();
                    clearSearch();
                  }
                }}
                placeholder={`Search ${label}...`}
                className="w-full pl-7 pr-3 py-1.5 text-sm bg-sunken border border-edge rounded-md focus:outline-none focus:ring-1 focus:ring-brand"
              />
            </div>
          </div>

          {/* Options list */}
          <div className="max-h-60 overflow-y-auto scroll-stable p-1">
            {filteredValues.length === 0 ? (
              <div className="px-3 py-4 text-sm text-ink-faint text-center">
                {isLoading ? "Searching…" : "No matches found"}
              </div>
            ) : (
              filteredValues.map(({ value, count }) => (
                <label
                  key={value}
                  className="flex items-center gap-2 px-3 py-1.5 rounded-md cursor-pointer hover:bg-raised transition-colors"
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(value)}
                    onChange={() => toggleValue(value)}
                    className="rounded border-edge-firm h-4 w-4"
                    style={{ accentColor: "var(--color-brand)" }}
                  />
                  <span className="text-sm text-ink-body flex-1 truncate">
                    {/* Written in sentence case; the value sent to the API is
                        still the one the curated table holds. */}
                    {category.name === "cell_type"
                      ? displayCellType(value)
                      : displayLabel(value)}
                  </span>
                  <span className="text-xs text-ink-faint tabular-nums">
                    {count == null ? "" : count.toLocaleString()}
                  </span>
                </label>
              ))
            )}
            {/* The rest of the category. Without this the list stops at fifty
                and gives no sign that it has, which is how 510 values across
                tissue, cell type and disease became invisible. */}
            {hasMore && (
              <button
                type="button"
                onClick={loadMore}
                disabled={isLoading}
                className="w-full px-3 py-2 text-sm font-medium text-brand hover:bg-raised rounded-md transition-colors disabled:opacity-50 cursor-pointer"
              >
                {isLoading
                  ? "Loading…"
                  : `Show more (${values.length} of ${total})`}
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
