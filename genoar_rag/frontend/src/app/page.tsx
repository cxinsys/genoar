"use client";

import { Suspense, useRef } from "react";
import Link from "next/link";
import { useSearch } from "@/hooks/useSearch";
import { useFilters } from "@/hooks/useFilters";
import Pagination from "@/components/Pagination";
import ActiveFilterChips from "@/components/ActiveFilterChips";
import { SampleCardSkeleton } from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import ContactFooter from "@/components/ContactFooter";
import DashboardOverview from "@/components/DashboardOverview";
import ScrollFade from "@/components/ScrollFade";
import FilterCategoryList from "@/components/FilterCategoryList";
import SampleCard from "@/components/SampleCard";
import SidePanel from "@/components/SidePanel";
import WaterFloor from "@/components/WaterFloor";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import type { SampleSummary } from "@/types/api";
import { DASHBOARD_DATASET } from "@/lib/datasets";
import DatasetNote from "@/components/DatasetNote";

type TabKey = "run_id" | "tissue" | "series" | "date";

// Each tab is the sort it applies, so the two can never disagree. Run ID is the
// order the API returns without being asked, and it gets a tab of its own rather
// than being an unnamed state that no tab describes.
const TABS: {
  key: TabKey;
  icon: string;
  label: string;
  sortBy?: string;
  disabled?: boolean;
}[] = [
  { key: "run_id", icon: "tag", label: "By Run ID", sortBy: "run_id" },
  { key: "tissue", icon: "bubble_chart", label: "By Tissue", sortBy: "tissue" },
  { key: "series", icon: "folder_open", label: "By Series", sortBy: "series" },
  { key: "date", icon: "calendar_today", label: "By Date", disabled: true },
];

const DEFAULT_SORT = "run_id";

const SIDEBAR_FILTERS = [
  { key: "organism", label: "Organism" },
  { key: "tissue", label: "Tissue" },
  { key: "library_source", label: "Library Source" },
  { key: "disease", label: "Disease" },
];

function DashboardContent() {
  const mainRef = useRef<HTMLElement>(null);
  const explorerRef = useRef<HTMLElement>(null);

  const {
    results,
    isLoading,
    error,
    filters,
    setFilter,
    removeFilter,
    clearFilters,
    setPage,
    setSort,
  } = useSearch(DASHBOARD_DATASET);
  const { filters: filterOptions, isLoading: filtersLoading } =
    useFilters(DASHBOARD_DATASET);
  // The width at which the sidebar stops having a column to sit in — the same
  // `lg` the aside's own classes use, said in the units a media query takes.
  const narrow = useMediaQuery("(max-width: 63.99rem)");

  // Build active filter map
  const activeFilterMap: Record<string, string[]> = {};
  for (const { key } of SIDEBAR_FILTERS) {
    const vals = filters[key as keyof typeof filters];
    if (Array.isArray(vals) && vals.length > 0) {
      activeFilterMap[key] = vals as string[];
    }
  }

  // Which tab is lit is read from the sort in the URL rather than kept
  // separately: a reload, a shared link or the Back button would otherwise
  // restore the sort while resetting the tab, and the two would disagree.
  const activeSort = filters.sort_by ?? DEFAULT_SORT;
  const activeTab: TabKey =
    TABS.find((t) => t.sortBy === activeSort)?.key ?? "run_id";

  function handleTabChange(tab: TabKey) {
    const sortBy = TABS.find((t) => t.key === tab)?.sortBy;
    if (sortBy) setSort(sortBy, "asc");
  }

  // Changing page takes you to the top of the explorer, where the new results
  // start. On purpose, not by accident: the loading state is shorter than a
  // full page of cards, so the scroller clamps of its own accord, and scroll
  // anchoring then pulls the view back down once the results arrive. Scrolling
  // deliberately puts the landing point under our control, and
  // `overflow-anchor: none` on the scroller stops the rebound.
  function scrollToExplorer() {
    const main = mainRef.current;
    const explorer = explorerRef.current;
    if (!main || !explorer) return;
    const offsetWithin =
      explorer.getBoundingClientRect().top -
      main.getBoundingClientRect().top +
      main.scrollTop;
    // Less the gap the card keeps above itself, so it lands where it rests.
    main.scrollTo({ top: Math.max(0, offsetWithin - 16) });
  }

  function handlePageChange(newOffset: number) {
    setPage(Math.floor(newOffset / (filters.limit ?? 20)) + 1);
    scrollToExplorer();
  }

  function handleTissueSelect(tissue: string) {
    const current = (filters.tissue as string[] | undefined) ?? [];
    if (!current.includes(tissue)) {
      setFilter("tissue", [...current, tissue]);
    }
    // The charts are below the explorer, so the results this just narrowed are
    // off the top of the screen. Going to them is the whole point of the click.
    scrollToExplorer();
  }

  // The filters, written once and put in one of two places. Below lg the aside
  // is not narrowed but dropped — there is no column for it — so what the width
  // decides is not how it looks but where it lives.
  const filterPanel = (
    <>
      {/* relative z-10, like the footer: the scroll fades are positioned and
              would paint over the dividers that frame the list. */}
      <div className="relative z-10 p-4 border-b border-edge flex justify-between items-center shrink-0">
        <h3 className="font-semibold text-ink flex items-center gap-2">
          <span
            className="material-symbols-outlined text-[20px]"
            style={{ color: "var(--color-brand)" }}
          >
            filter_list
          </span>
          Filters
        </h3>
        <button
          type="button"
          onClick={clearFilters}
          className="text-xs font-medium hover:opacity-70 cursor-pointer transition-opacity"
          style={{ color: "var(--color-brand)" }}
        >
          Reset all
        </button>
      </div>
      {/* pr-2 rather than pr-4: the reserved scrollbar gutter supplies the
              other 8px, so the list sits evenly between the panel's edges. */}
      <ScrollFade
        wrapperClassName="flex-1 min-h-0"
        className="h-full p-4 scroll-stable"
        contentClassName="space-y-6"
        height="2rem"
      >
        {SIDEBAR_FILTERS.map(({ key, label }) => {
          const category = filterOptions?.categories.find(
            (c) => c.name === key,
          );
          const selected =
            (filters[key as keyof typeof filters] as string[] | undefined) ??
            [];
          if (filtersLoading || !category) {
            return (
              <div key={key}>
                <h4 className="type-eyebrow mb-3">{label}</h4>
                <div className="animate-pulse space-y-2">
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="h-4 bg-sunken rounded" />
                  ))}
                </div>
              </div>
            );
          }
          return (
            <FilterCategoryList
              dataset={DASHBOARD_DATASET}
              key={key}
              category={key}
              label={label}
              selected={selected}
              onToggle={(value) =>
                setFilter(
                  key,
                  selected.includes(value)
                    ? selected.filter((v) => v !== value)
                    : [...selected, value],
                )
              }
              initialValues={category.values}
              initialTotal={category.total_distinct}
            />
          );
        })}
      </ScrollFade>
      {/* relative z-10 so the divider stays visible: the scroll fade above
              is positioned, and would otherwise paint over this border. */}
      <div className="relative z-10 p-4 border-t border-edge bg-surface shrink-0">
        {/* Tinted from the brand rather than from a written-out navy at 8%. The
            literal was the navy, so it stayed navy under a palette whose brand
            is not — a blue-grey patch at the foot of a sand-coloured column. */}
        <div className="rounded-lg p-3 border border-brand/20 bg-brand/8">
          <div className="flex gap-2 items-start">
            <span
              className="material-symbols-outlined text-[18px] mt-0.5 shrink-0"
              style={{ color: "var(--color-brand)" }}
            >
              info
            </span>
            <div>
              <p className="text-xs text-ink-body font-medium">
                Need more specific data?
              </p>
              <Link
                href="/search"
                className="text-xs font-bold hover:underline mt-1 block"
                style={{ color: "var(--color-brand)" }}
              >
                Try Advanced Search →
              </Link>
            </div>
          </div>
        </div>
      </div>
    </>
  );

  return (
    // paper-room: the sheet this page's body is printed on, laid across the
    // whole width rather than behind the column being read. See globals.css.
    //
    // Named, because one palette wants no sheet on this page in particular. The
    // name says which page it is and the stylesheet decides what that means —
    // the alternative is a class called something like "no-sheet", which puts a
    // palette's opinion in the markup of a page that has none.
    <div
      data-room="dashboard"
      className="paper-room flex flex-col flex-1 min-h-0 bg-canvas"
    >
      <WaterFloor />
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* Sidebar */}
        {/* Only the filter list scrolls. With the heading and the footer outside
            the scroller, their divider and background still span the panel
            rather than stopping at the scrollbar's reserved gutter. */}
        {/* z-10 so the main column does not paint over the shadow's edge.
            The breakpoint is stated twice on purpose: the class is what the
            server sends and what holds until React has run, and the hook is what
            actually moves the filters. Without the class the wide layout would
            be rendered on a phone and then taken away in front of the reader. */}
        {narrow ? (
          <SidePanel label="Filters">{filterPanel}</SidePanel>
        ) : (
          <aside className="dashboard-sidebar relative z-10 bg-surface border-r border-edge shadow-card shrink-0 hidden lg:flex lg:flex-col">
            {filterPanel}
          </aside>
        )}

        {/* Main content */}
        {/* One scroller for the whole column. The charts and the explorer used to
            scroll separately, which left the explorer working in the bottom half
            of the window. Now the charts scroll away and the explorer, being a
            viewport-tall card, ends up filling the screen on its own. */}
        <main
          ref={mainRef}
          // paper-scroller: the reading, as opposed to the filter list beside
          // it, and so the one the sheet behind the page follows.
          className="paper-scroller flex-1 overflow-y-auto scroll-stable no-scroll-anchor"
        >
          {/* The explorer first, and the charts under it.

              What a visitor should see on arrival is that there is a great deal
              of data here, and the explorer is the thing that shows it: two
              hundred thousand samples, listed. The charts describe that corpus
              and are worth reading second, so they wait below the fold rather
              than standing between the reader and the data.

              The card is a viewport tall, so it fills the screen on its own and
              the charts are reached only by scrolling past it. */}
          <div className="px-6 pt-6 pb-6">
            <section
              ref={explorerRef}
              className="bg-surface rounded-card border border-edge shadow-card flex flex-col min-h-[calc(100vh-5.5rem)]"
            >
              {/* bg-surface and the top radius are the card's, repeated here: once this
              is stuck it is what the top of the card looks like, and it has to be
              opaque for the results to pass behind it. */}
              <div className="explorer-head bg-surface rounded-t-card px-6 pt-5 pb-0 border-b border-edge shrink-0">
                {/* Title and tabs share the row, bottom-aligned so the active tab's
                underline meets the header's own border. */}
                <div className="flex justify-between items-end gap-6 flex-wrap">
                  <div className="pb-3">
                    <h1 className="type-panel-title">Metadata Explorer</h1>
                    <p className="type-panel-sub mt-1">
                      Showing{" "}
                      <span className="font-semibold text-ink">
                        {results?.total.toLocaleString() ?? "..."}
                      </span>{" "}
                      samples matching criteria
                    </p>
                    <DatasetNote
                      dataset={DASHBOARD_DATASET}
                      className="mt-0.5 block"
                    />
                  </div>

                  {/* The tabs scroll sideways rather than pushing out of the
                      card. Five of them with their icons want about 580px, and
                      the row they are on wraps under the title well before the
                      card is that wide, so they were simply overflowing it.

                      The clipping is turned off again above md, and that is the
                      point of the breakpoint rather than a guess at where it is
                      needed: overflow-x on a box clips the other axis too, and
                      the last tab is disabled and carries a tooltip that hangs
                      below it. Wide, the tabs fit and the tooltip is what
                      matters; narrow, they do not fit and hover is not how the
                      page is being used anyway.

                      min-w-0 because a flex item will not shrink below the width
                      of its content without it, which is the whole reason a
                      scroller inside one so often does nothing. */}
                  <div className="min-w-0 max-w-full overflow-x-auto md:overflow-x-visible hide-scrollbar">
                    <div className="flex gap-8 -mb-px w-max md:w-auto">
                      {TABS.map(({ key, icon, label, disabled }) => {
                        const isActive = activeTab === key;
                        const btn = (
                          <button
                            key={key}
                            type="button"
                            disabled={disabled}
                            onClick={() => handleTabChange(key)}
                            className="pb-3 border-b-2 font-medium text-sm flex items-center gap-2 cursor-pointer transition-colors whitespace-nowrap disabled:opacity-40 disabled:cursor-not-allowed"
                            style={
                              isActive
                                ? {
                                    borderColor: "var(--color-brand)",
                                    color: "var(--color-brand)",
                                  }
                                : {
                                    borderColor: "transparent",
                                    color: "var(--color-ink-soft)",
                                  }
                            }
                          >
                            <span className="material-symbols-outlined text-[18px]">
                              {icon}
                            </span>
                            {label}
                          </button>
                        );
                        if (disabled) {
                          return (
                            <div key={key} className="relative group/tooltip">
                              {btn}
                              <div className="absolute top-full left-1/2 -translate-x-1/2 mt-2 px-2.5 py-1 bg-inverse text-on-inverse text-xs rounded-md whitespace-nowrap opacity-0 group-hover/tooltip:opacity-100 transition-opacity pointer-events-none z-50">
                                Coming soon
                                <div className="absolute bottom-full left-1/2 -translate-x-1/2 border-4 border-transparent border-b-inverse" />
                              </div>
                            </div>
                          );
                        }
                        return btn;
                      })}
                    </div>
                  </div>
                </div>
              </div>

              {/* Card body — no scroller of its own; the column above scrolls it. */}
              <div className="flex-1 p-6">
                {/* Applied filters */}
                <ActiveFilterChips
                  filters={activeFilterMap}
                  onRemove={removeFilter}
                  onClearAll={clearFilters}
                  className="mb-4"
                />

                {/* Error */}
                {error && (
                  <ErrorState
                    type="network"
                    onRetry={() => window.location.reload()}
                  />
                )}

                {/* Loading */}
                {isLoading && !error && (
                  <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5 pb-6">
                    {[1, 2, 3, 4, 5, 6].map((i) => (
                      <SampleCardSkeleton key={i} />
                    ))}
                  </div>
                )}

                {/* Results */}
                {!isLoading &&
                  !error &&
                  results &&
                  results.items.length === 0 && (
                    <ErrorState type="empty" onRetry={clearFilters} />
                  )}

                {!isLoading &&
                  !error &&
                  results &&
                  results.items.length > 0 && (
                    <>
                      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5 pb-6">
                        {results.items.map((sample: SampleSummary) => (
                          <SampleCard
                            key={sample.run_id}
                            sample={sample}
                            dataset={DASHBOARD_DATASET}
                          />
                        ))}
                      </div>

                      <div className="flex justify-center">
                        <Pagination
                          total={results.total}
                          offset={filters.offset ?? 0}
                          limit={filters.limit ?? 20}
                          onPageChange={handlePageChange}
                        />
                      </div>
                    </>
                  )}
              </div>
            </section>
          </div>

          {/* Charts, below the explorer. Within the main column so they do not
              push the filter sidebar down. */}
          <DashboardOverview onTissueSelect={handleTissueSelect} />

          {/* Who to reach about GENOAR — the last thing in the column. */}
          <ContactFooter />
        </main>
      </div>
    </div>
  );
}

export default function DashboardPage() {
  return (
    <Suspense
      fallback={
        <div className="flex flex-col flex-1 min-h-0 bg-canvas">
          <div className="flex-1 p-6">
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
              {[1, 2, 3, 4, 5, 6].map((i) => (
                <SampleCardSkeleton key={i} />
              ))}
            </div>
          </div>
        </div>
      }
    >
      <DashboardContent />
    </Suspense>
  );
}
