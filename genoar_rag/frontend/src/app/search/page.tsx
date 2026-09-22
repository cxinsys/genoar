"use client";

import { displayLabel } from "@/lib/display";
import { useState, useEffect, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useSearch, DEFAULT_TOP_K } from "@/hooks/useSearch";
import { useFilters } from "@/hooks/useFilters";
import { useSemanticSpecies } from "@/hooks/useSemanticSpecies";
import Pagination from "@/components/Pagination";
import FilterDropdown from "@/components/FilterDropdown";
import ActiveFilterPanel from "@/components/ActiveFilterPanel";
import { SampleCardSkeleton } from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import SimilarityMap from "@/components/SimilarityMap";
import SampleCard from "@/components/SampleCard";
import SidePanel from "@/components/SidePanel";
import WaterFloor from "@/components/WaterFloor";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { buildExportUrl, resolveAccession } from "@/lib/api-client";
import { startDownload } from "@/components/DownloadToast";
// Shared with the header, which advertises one of these examples in its own field.
import { EXAMPLE_QUERIES } from "@/lib/examples";
import { looksLikeAccession } from "@/lib/accession";
import type { SampleSummary, SearchMode } from "@/types/api";
import { NO_DATASET } from "@/lib/datasets";
import DatasetNote from "@/components/DatasetNote";

/* "AI Librarian" rather than "semantic search", which named the mechanism and
   left the reader to work out what it does for them. A librarian is someone you
   describe a problem to and who comes back with what to read, which is the
   whole of it; the paper describes the feature the same way. `semantic` stays
   as the mode's value in the URL and the API — that is a contract, and renaming
   it would break every link anyone has saved. */
const SEARCH_MODE_OPTIONS: { mode: SearchMode; label: string; icon: string }[] =
  [
    { mode: "sql", label: "Keyword Search", icon: "database" },
    { mode: "semantic", label: "AI Librarian", icon: "psychology" },
  ];

const SEARCH_MODE_TITLES: Record<SearchMode, string> = {
  sql: "Keyword Search",
  semantic: "AI Librarian",
  hybrid: "Hybrid AI Librarian",
};

// Species toggle — maps to the organism filter. Human is served by the SapBERT
// index and mouse by the all-MiniLM-L6-v2 one; whether a given deployment has
// both is reported by /health, not assumed here.
type SpeciesKey = "all" | "human" | "mouse";
const SPECIES_OPTIONS: {
  key: SpeciesKey;
  label: string;
  icon: string;
  organism: string[];
}[] = [
  { key: "all", label: "All", icon: "public", organism: [] },
  {
    key: "human",
    label: "Homo sapiens",
    icon: "person",
    organism: ["Homo sapiens"],
  },
  {
    key: "mouse",
    label: "Mus musculus",
    icon: "pest_control_rodent",
    organism: ["Mus musculus"],
  },
];

// How many results a semantic search may be asked for. Capped at 100 because
// that is the most the API will put in one response, and the whole set is
// fetched at once so the similarity map can draw all of it.
const TOP_K_OPTIONS = [20, 50, 100];

const FILTER_CATEGORIES = [
  { key: "organism", label: "Organism" },
  { key: "tissue", label: "Tissue" },
  { key: "cell_type", label: "Cell Type" },
  { key: "disease", label: "Disease" },
  { key: "library_source", label: "Library Strategy" },
];

// The same names the selectors carry, for the sidebar card that reports what is
// selected. Derived rather than written twice so the two cannot drift apart.
const FILTER_LABELS: Record<string, string> = Object.fromEntries(
  FILTER_CATEGORIES.map(({ key, label }) => [key, label]),
);

/** Go to the sample a paper quotes, by its accession.
 *
 * Its own control rather than a mode of the search: it does not filter or rank
 * anything, it opens a record. The button stays disabled until the text is one
 * of the forms the sample route resolves, so a half-typed accession cannot
 * navigate to a page that will only report itself missing.
 *
 * One accession is not always one record, which is why this asks the server
 * before navigating — see `submit`.
 */
function AccessionLookup() {
  const router = useRouter();
  const [value, setValue] = useState("");
  const [choices, setChoices] = useState<SampleSummary[] | null>(null);
  const [isResolving, setIsResolving] = useState(false);
  const accession = looksLikeAccession(value);

  /** Open the sample, unless the accession names more than one.
   *
   * A GSM is a biological sample; a run is a sequencing run of it. 1,268 of the
   * 3,919 GSMs here name several runs, up to twelve, so opening the first by
   * run id would leave the rest unreachable, with nothing on screen to say they
   * exist. When there is a choice to make, it is shown.
   */
  async function submit() {
    if (!accession) return;
    setIsResolving(true);
    setChoices(null);
    try {
      const page = await resolveAccession(accession);
      if (page.items.length === 1) {
        router.push(`/sample/${page.items[0].run_id}`);
      } else {
        setChoices(page.items);
      }
    } catch {
      // Let the sample route report an accession this corpus does not hold,
      // rather than growing a second way of saying the same thing here.
      router.push(`/sample/${accession}`);
    } finally {
      setIsResolving(false);
    }
  }

  return (
    // The toggles' own track, and the same px-4 py-2 inside it, so all three
    // controls on this row are the same height. The field carries no border or
    // fill of its own: the track is the box.
    <form
      className="segment-track inline-flex items-center relative"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <label htmlFor="accession-lookup" className="sr-only">
        Open a sample by accession
      </label>
      {/* Says what the field takes without spending the width a word would.
          Always there rather than only while empty: a prefix that comes and
          goes as you type moves the text under the cursor. */}
      <span
        aria-hidden
        className="material-symbols-outlined text-ink-faint text-[18px] leading-none ml-3"
      >
        tag
      </span>
      <input
        id="accession-lookup"
        type="text"
        value={value}
        onChange={(e) => {
          setValue(e.target.value);
          setChoices(null);
        }}
        placeholder="Accession"
        className="w-28 bg-transparent border-none pl-2 pr-1 py-2 text-sm text-ink placeholder-ink-faint focus:outline-none"
      />
      <button
        type="submit"
        disabled={!accession}
        title={
          accession
            ? `Open ${accession}`
            : "Enter an SRR, ERR, DRR or GSM accession"
        }
        aria-label="Open this accession"
        className="segment flex items-center px-3 py-2 text-ink-soft hover:text-ink disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer"
      >
        <span className="material-symbols-outlined text-[18px] leading-none">
          arrow_forward
        </span>
      </button>

      {/* The runs this accession names, when it names more than one. Each is a
          sample in its own right, so the choice is the user's rather than the
          first-by-run-id the single-sample routes have to settle on. */}
      {choices && choices.length > 1 && (
        <div className="absolute top-full right-0 mt-2 w-72 bg-surface border border-edge rounded-card shadow-card-raised z-50 overflow-hidden">
          <div className="px-3 py-2 border-b border-edge bg-raised/50 text-xs text-ink-soft">
            {accession} covers {choices.length} runs
          </div>
          <div className="max-h-60 overflow-y-auto p-1">
            {choices.map((s) => (
              <Link
                key={s.run_id}
                href={`/sample/${s.run_id}`}
                className="flex items-baseline gap-2 px-3 py-1.5 rounded-md hover:bg-raised transition-colors"
              >
                <span className="font-mono text-sm text-ink flex-1 truncate">
                  {s.run_id}
                </span>
                <span className="text-xs text-ink-faint truncate">
                  {s.tissue ?? ""}
                </span>
              </Link>
            ))}
          </div>
        </div>
      )}
      {isResolving && (
        <span className="sr-only" role="status">
          Resolving {accession}
        </span>
      )}
    </form>
  );
}

function SearchPageContent() {
  const rawParams = useSearchParams();
  const router = useRouter();
  const {
    results,
    isLoading,
    error,
    filters,
    isSemanticUnavailable,
    setFilter,
    removeFilter,
    clearFilters,
    setKeyword,
    setPage,
    setTopK,
    setSort,
    setSearchMode,
  } = useSearch();
  const { filters: filterOptions } = useFilters();
  const [inputValue, setInputValue] = useState(filters.keyword ?? "");
  const activeMode: SearchMode = filters.search_mode ?? "sql";

  // Species toggle state, derived from the organism filter.
  const organismSelected = (filters.organism as string[] | undefined) ?? [];
  const activeSpecies: SpeciesKey | null =
    organismSelected.length === 0
      ? "all"
      : organismSelected.length === 1 && organismSelected[0] === "Homo sapiens"
        ? "human"
        : organismSelected.length === 1 &&
            organismSelected[0] === "Mus musculus"
          ? "mouse"
          : null;
  const isMouseSelected = organismSelected.includes("Mus musculus");

  // Which species semantic search works for is a property of the deployment's
  // loaded indexes, so ask the backend instead of assuming. "All" needs at least
  // one; a named species needs its own index.
  const { species: semanticSpecies, isLoading: isSpeciesLoading } =
    useSemanticSpecies();
  function speciesSupported(key: SpeciesKey): boolean {
    if (isSpeciesLoading) return true;
    return key === "all"
      ? semanticSpecies.length > 0
      : semanticSpecies.includes(key);
  }

  // Example queries follow the species toggle; "All" offers both sets.
  const exampleQueries =
    activeSpecies === "mouse"
      ? EXAMPLE_QUERIES.mouse
      : activeSpecies === "human"
        ? EXAMPLE_QUERIES.human
        : [...EXAMPLE_QUERIES.human, ...EXAMPLE_QUERIES.mouse];

  function handleSpecies(key: SpeciesKey) {
    const opt = SPECIES_OPTIONS.find((o) => o.key === key);
    if (opt) setFilter("organism", opt.organism);
  }

  function handleExampleQuery(query: string) {
    setInputValue(query);
    setKeyword(query);
  }

  // The empty-query hint answers "why did nothing happen?", so it waits until
  // the user has actually asked for a search. On arrival the field's placeholder
  // and the example queries below it already say what to type; an alert sitting
  // over an untouched form reads as a mistake the visitor has not made yet.
  const [emptyQueryAttempted, setEmptyQueryAttempted] = useState(false);
  // The runs an accession typed as a query turned out to name, when it named
  // more than one. Set by the effect below instead of navigating.
  const [accessionChoices, setAccessionChoices] = useState<
    SampleSummary[] | null
  >(null);

  // Whether the map has taken the results' place in the wide column. A view
  // preference, not part of the search, so it stays out of the URL: a link to a
  // search should open the search, not somebody else's arrangement of it.
  const [mapExpanded, setMapExpanded] = useState(false);

  function handleSearchMode(mode: SearchMode) {
    // Choosing a mode starts the search over, hint included.
    setEmptyQueryAttempted(false);
    setSearchMode(mode);
  }

  // An accession that arrives as a query is a lookup wearing a search's
  // clothes — typed here, or sent from the header's field, which posts
  // everything to the AI Librarian. Either way the vector search cannot answer
  // it, so the sample route does. Replace rather than push: there was never a
  // search to go back to.
  //
  // Except that one accession is not always one sample. A GSM names a
  // biological sample and a run names a sequencing run of it, and 1,268 of the
  // 3,919 GSMs here name several runs. Going straight to the first opened one
  // of twelve and said nothing about the other eleven, so the runs are counted
  // before deciding — the same question the Accession field asks.
  useEffect(() => {
    const accession = filters.keyword
      ? looksLikeAccession(filters.keyword)
      : null;
    if (!accession) return;
    let cancelled = false;
    resolveAccession(accession)
      .then((page) => {
        if (cancelled) return;
        if (page.items.length === 1) {
          router.replace(`/sample/${page.items[0].run_id}`);
        } else {
          setAccessionChoices(page.items);
        }
      })
      .catch(() => {
        // Unknown here: let the sample route say so, as it did before.
        if (!cancelled) router.replace(`/sample/${accession}`);
      });
    return () => {
      cancelled = true;
    };
  }, [filters.keyword, router]);

  // Handle legacy ?q= parameter from dashboard. It carried a natural-language
  // query, which only means something in semantic mode now — keyword mode narrows
  // by filter alone — so the rewrite names the mode as well. The user did not ask
  // for this rewrite, so it replaces rather than adding a history entry they would
  // have to click Back through.
  useEffect(() => {
    const q = rawParams.get("q");
    if (q && !rawParams.get("keyword")) {
      const params = new URLSearchParams(rawParams.toString());
      params.delete("q");
      params.set("keyword", q);
      params.set("search_mode", "semantic");
      router.replace(`?${params.toString()}`, { scroll: false });
    }
  }, []);

  // Sync input with URL keyword (derive during render — resets the field when
  // the URL keyword changes, e.g. on navigation).
  const [syncedKeyword, setSyncedKeyword] = useState(filters.keyword ?? "");
  if ((filters.keyword ?? "") !== syncedKeyword) {
    setSyncedKeyword(filters.keyword ?? "");
    setInputValue(filters.keyword ?? "");
    setEmptyQueryAttempted(false);
  }

  function handleSearchSubmit(e: React.FormEvent) {
    e.preventDefault();
    const query = inputValue.trim();
    setEmptyQueryAttempted(query === "");
    setKeyword(query);
  }

  // Build active filter chips data
  const activeFilterMap: Record<string, string[]> = {};
  for (const { key } of FILTER_CATEGORIES) {
    const vals = filters[key as keyof typeof filters];
    if (Array.isArray(vals) && vals.length > 0) {
      activeFilterMap[key] = vals as string[];
    }
  }

  // What the results are actually ordered by, which is not always what was
  // asked for. With no sort in the URL an AI search comes back in relevance
  // order, so the select has to say so rather than "Run ID (A→Z)": the one
  // control on the page that describes the list cannot disagree with it.
  const sortValue =
    filters.sort_by === "similarity_score"
      ? "similarity_desc"
      : filters.sort_by == null && activeMode !== "sql"
        ? "similarity_desc"
        : filters.sort_order === "desc"
          ? "run_id_desc"
          : "run_id_asc";

  // Only semantic and hybrid results carry scores, so the map appears with them
  // and stays out of the way otherwise. While a search is running the card stays
  // but the map does not: the items in hand are the previous search's, and the
  // mode is what says whether to expect one, since no score has arrived yet.
  const showMap =
    Boolean(filters.keyword) &&
    activeMode !== "sql" &&
    (isLoading ||
      Boolean(results?.items?.some((s) => s.similarity_score != null)));
  // Nothing to trade places with when there is no map, whatever was asked for
  // before the mode changed.
  const swapped = mapExpanded && showMap;

  // The width at which the side column stops having a column to be — the same
  // `lg` the grid uses, said in the units a media query takes.
  const narrow = useMediaQuery("(max-width: 63.99rem)");

  // The two movable halves of the page. Held as values rather than written into
  // both columns, because they are the same panels either way — only the column
  // they sit in changes.
  const resultsPanel = (
    <div className="flex flex-col gap-4">
      {/* An accession was typed as a query and named several samples. Shown
          above the results rather than instead of them: the vector search still
          ran on the text, and one of these is probably what was wanted. */}
      {accessionChoices && accessionChoices.length > 1 && (
        <div className="bg-surface border border-edge rounded-card shadow-card overflow-hidden">
          <div className="px-5 py-3 border-b border-edge bg-raised/40">
            <p className="text-sm text-ink-body">
              <span className="font-mono font-semibold">{filters.keyword}</span>{" "}
              covers {accessionChoices.length} runs. Open one:
            </p>
          </div>
          <div className="p-2 flex flex-wrap gap-2">
            {accessionChoices.map((s) => (
              <Link
                key={s.run_id}
                href={`/sample/${s.run_id}`}
                className="inline-flex items-baseline gap-2 px-3 py-1.5 rounded-md border border-edge hover:border-brand/40 hover:bg-raised transition-colors"
              >
                <span className="font-mono text-sm text-ink">{s.run_id}</span>
                {s.tissue && (
                  <span className="text-xs text-ink-faint">{displayLabel(s.tissue)}</span>
                )}
              </Link>
            ))}
          </div>
        </div>
      )}
      <div className="flex items-center justify-between pb-2">
        <h2 className="type-eyebrow">
          {!results
            ? "Results"
            : activeMode === "sql"
              ? `Results (${results.total.toLocaleString()})`
              : `Top ${results.total.toLocaleString()} Results`}
        </h2>
        <div className="flex items-center gap-2">
          <span className="text-sm text-ink-soft">Sort by:</span>
          <select
            value={sortValue}
            onChange={(e) => {
              const v = e.target.value;
              if (v === "similarity_desc") {
                setSort("similarity_score", "desc");
              } else {
                setSort("run_id", v === "run_id_desc" ? "desc" : "asc");
              }
            }}
            className="bg-transparent border-none text-sm font-medium text-brand focus:ring-0 cursor-pointer py-0 pl-0 pr-8 outline-none"
          >
            {activeMode !== "sql" && (
              <option value="similarity_desc">Similarity</option>
            )}
            <option value="run_id_asc">Run ID (A→Z)</option>
            <option value="run_id_desc">Run ID (Z→A)</option>
          </select>
        </div>
      </div>

      {error && (
        <ErrorState type="network" onRetry={() => window.location.reload()} />
      )}

      {isLoading && !error && (
        <div className="flex flex-col gap-4">
          {[1, 2, 3, 4].map((i) => (
            <SampleCardSkeleton key={i} />
          ))}
        </div>
      )}

      {!isLoading && !error && results && results.items.length === 0 && (
        <ErrorState type="empty" onRetry={clearFilters} />
      )}

      {!isLoading && !error && results && results.items.length > 0 && (
        <>
          {results.items.map((sample: SampleSummary) => (
            <SampleCard key={sample.run_id} sample={sample} showAccessions />
          ))}
          <div className="flex justify-center mt-6">
            <Pagination
              total={results.total}
              offset={filters.offset ?? 0}
              limit={filters.limit ?? 20}
              onPageChange={(newOffset) =>
                setPage(Math.floor(newOffset / (filters.limit ?? 20)) + 1)
              }
            />
          </div>
        </>
      )}
    </div>
  );

  // Export travels with the map: it is the other thing you do with a result set
  // rather than a member of the column, and leaving it behind would strand it
  // between the summary and a list of samples.
  const mapPanel = (
    <div className="flex flex-col gap-4">
      {showMap && filters.keyword ? (
        <SimilarityMap
          query={filters.keyword}
          items={results?.items ?? []}
          isLoading={isLoading}
          variant={swapped ? "expanded" : "compact"}
          onToggleVariant={() => setMapExpanded((v) => !v)}
        />
      ) : activeMode === "sql" || !filters.keyword ? (
        // The map's slot, kept whether or not there is a map to draw so the
        // layout does not shift. Structured search never has one; the AI
        // Librarian has none until a query is run. The note says which case.
        <div className="bg-surface border border-edge rounded-card shadow-card p-5">
          <h3 className="type-eyebrow flex items-center gap-1.5">
            <span className="material-symbols-outlined text-[16px] leading-none">
              hub
            </span>
            Similarity Map
          </h3>
          <p className="mt-2 text-sm text-ink-soft">
            {activeMode === "sql"
              ? "This map is only shown in the AI Librarian. Search with the AI Librarian to see how close the matches are."
              : "Enter a query and search to see how close the matches are."}
          </p>
        </div>
      ) : null}

      {/* Export card. The same shadow every other card carries, rather than one
          of its own tinted with a literal navy that would follow no palette.

          brand-deep and not brand. This card is a block of the colour rather
          than a surface wearing it, and brand goes pale in the dark so that it
          can be read against near-black — a card filled with that would be the
          brightest thing on the page. Deep in both themes, lifted in the dark
          only enough to sit off the canvas. */}
      <div
        className="rounded-card p-5 relative overflow-hidden shadow-card text-on-brand-deep"
        style={{
          background:
            "linear-gradient(to bottom right, var(--color-brand-deep), var(--color-brand-deep-soft))",
        }}
      >
        <div className="absolute -bottom-6 -right-6 w-24 h-24 bg-on-brand-deep opacity-10 rounded-full pointer-events-none" />
        <div className="relative z-10">
          {/* Everything here is worked out from the card's own two colours
              rather than named. A white heading, blue-200 body, blue-50 hover
              and a hard-coded navy on the button are five separate statements
              of "the card is navy", and under a palette whose deep is a sea
              green they would all still say navy. Mixed from brand-deep and
              on-brand-deep, the card is legible against itself whatever it is
              made of, and there is one place to change it.
              on-brand-deep and not on-brand: on a dark palette brand goes pale
              so that it can be read against near-black, which makes on-brand
              near-black — and near-black on this filled card is nothing at all.
              The two are the same colour everywhere brand has stayed dark. */}
          <h4 className="type-eyebrow text-on-brand-deep/75! mb-2">
            Export Data
          </h4>
          <p className="text-sm text-on-brand-deep/90 mb-4">
            Download metadata for {results?.total.toLocaleString() ?? "all"}{" "}
            search results.
          </p>
          <a
            href={buildExportUrl(filters)}
            download
            data-testid="export-csv"
            onClick={(e) => {
              e.preventDefault();
              startDownload(buildExportUrl(filters), "The search results");
            }}
            className="w-full bg-on-brand-deep text-brand-deep py-2 rounded-lg text-sm font-bold flex justify-center items-center gap-2 hover:bg-on-brand-deep/90 transition-colors"
          >
            <span className="material-symbols-outlined text-[18px]">
              download
            </span>
            Download CSV
          </a>
        </div>
      </div>
    </div>
  );

  // The side column, written once and put in one of two places — beside the
  // results, or inside a drawer when there is no beside to put it.
  const sidebar = (
    <>
      {/* The result count, and — where there is one to choose — the size of
              the set that produced it. */}
      <div className="bg-surface border border-edge rounded-card p-5 shadow-card">
        <div className="flex items-center justify-between mb-3">
          <h3 className="type-eyebrow flex items-center gap-1.5">
            <span className="material-symbols-outlined text-[16px] leading-none">
              analytics
            </span>
            Search Summary
          </h3>
        </div>
        <div className="flex flex-col items-center">
          <span className="type-figure text-4xl">
            {results?.total.toLocaleString() ?? "--"}
          </span>
          {/* Vector search has no count of matching samples — every sample
                  matches to some degree — so what comes back is a ranked pool.
                  Calling that a total would overstate it. */}
          <span className="type-eyebrow mt-1">
            {activeMode === "sql" ? "Total Results" : "Top Results"}
          </span>
        </div>

        {/* How many the vector search should return. Only semantic and
                hybrid have a cutoff to choose; a SQL count is exact.

                Directly under the number and at the same width, because the
                two are one statement: the buttons set what the number counts.
                A small control off to the side read as an unrelated setting. */}
        {activeMode !== "sql" && (
          <div className="mt-4">
            <div
              className="segment-track grid grid-cols-3 gap-1"
              role="group"
              aria-label="Number of results"
            >
              {TOP_K_OPTIONS.map((n) => {
                const isActive = (filters.top_k ?? DEFAULT_TOP_K) === n;
                return (
                  <button
                    key={n}
                    type="button"
                    onClick={() => setTopK(n)}
                    aria-pressed={isActive}
                    data-testid={`top-k-${n}`}
                    className={`segment py-2 text-sm font-medium cursor-pointer ${
                      isActive
                        ? "segment-on text-brand!"
                        : "text-ink-soft hover:text-ink"
                    }`}
                  >
                    {n}
                  </button>
                );
              })}
            </div>
            <p className="text-xs text-ink-faint mt-2 text-center leading-snug">
              Returns the {filters.top_k ?? DEFAULT_TOP_K} highest-scoring
              matches.
            </p>
          </div>
        )}
      </div>

      {/* What the search is narrowed to, directly under the count it
              explains. */}
      <ActiveFilterPanel
        filters={activeFilterMap}
        labels={FILTER_LABELS}
        onRemove={removeFilter}
        onClearAll={clearFilters}
      />

      {/* The narrow column: the map and its export, or the results once
              the two have traded places. */}
      {swapped ? resultsPanel : mapPanel}
    </>
  );

  return (
    // paper-room: the sheet this page's body is printed on. It lies outside the
    // 1600 cap below, which is the point of it being here — see globals.css.
    <div className="paper-room bg-canvas flex-1 min-h-0 flex flex-col font-sans">
      <WaterFloor />
      {/* Two scrollers side by side, not one page that scrolls under a pinned
          sidebar: the sidebar has grown past a screenful of its own, and sticky
          could only pin it — anything below the fold was unreachable. Each column
          now scrolls on its own, so reading the results does not move the summary
          and reading the summary does not move the results.

          Only from lg up, where the two are actually side by side. Below that the
          grid is a single column and two nested scrollers stacked in a fixed
          height would be a trap on a phone, so main takes the scroll back. */}
      {/* paper-scroller-sm: below lg this is the one box that scrolls, so the
          sheet follows it here and the results column there. */}
      <main className="paper-scroller-sm flex-1 min-h-0 overflow-y-auto lg:overflow-hidden scroll-stable">
        {/* py-6 and the 16px card gaps below are the dashboard's body rhythm, so
            the two pages read as the same surface. The horizontal padding stays
            at the header's own inset instead: with no sidebar on this page the
            content lines up directly under the wordmark, which the dashboard has
            nothing to line up with.

            The vertical padding moved inside each column: it belongs to the
            scrolled content, so the last card clears the bottom edge instead of
            ending flush against it. */}
        <div className="max-w-[1600px] w-full mx-auto px-4 sm:px-6 lg:px-8 lg:h-full">
          {/* Main grid */}
          {/* 16px between the results column and the sidebar — the same distance
            the result cards keep from each other and the dashboard's cards from
            theirs. It was 32px, so a sidebar card sat twice as far from the
            result beside it as from the card above it.

            items-stretch from lg up so both columns are the full height of the
            row and can scroll inside it; min-h-0 because a grid item's default
            minimum is its content, which would push the row past the viewport
            and give the scroll back to the page. */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start lg:items-stretch lg:h-full lg:min-h-0">
            {/* Results */}
            {/* 16px between blocks and between the things inside them — the same
              step the cards keep from each other, and what the dashboard puts
              between its chart block and the explorer below it. One step, not
              three: 32px from the results and 24px between the query controls
              would make the page read in three different rhythms. */}
            {/* paper-scroller-lg: from lg up this column is the reading and the
                sidebar is not, so the sheet behind the page follows this one.
                Below lg it is not a scroller at all and main takes it back —
                which is why the name is claimed at a breakpoint rather than
                outright. */}
            <div className="paper-scroller-lg lg:col-span-8 flex flex-col gap-4 pt-6 pb-6 lg:min-h-0 lg:overflow-y-auto scroll-stable overscroll-contain shadow-room">
              <section className="space-y-4 relative z-30">
                <div className="max-w-3xl">
                  {/* The same treatment as the dashboard's Metadata Explorer: this
                    heading names the panel below it, and it was the only text on
                    the site at 30px. */}
                  {/* The summary's figure treatment rather than a panel title: this
                    heading names the whole page, not a panel inside it, and it is
                    read against the count in the sidebar. Same role, one size
                    down — the number is the larger of the two because it is the
                    answer, and this is the question. */}
                  <h1 className="type-figure text-2xl">
                    {SEARCH_MODE_TITLES[activeMode]}
                  </h1>
                  <p className="type-panel-sub mt-1">
                    {activeMode === "semantic"
                      ? "Describe the samples you are after in plain language, and a retrieval model finds the SRA metadata that matches."
                      : "Filter SRA metadata by structured fields: organism, tissue, assay, disease, and more."}
                  </p>
                  <DatasetNote
                    dataset={NO_DATASET}
                    className="mt-1 block"
                  />
                </div>

                {/* Search mode + species toggles */}
                <div className="flex flex-wrap items-center gap-4">
                  <div
                    className="segment-track inline-flex"
                    role="group"
                    aria-label="Search mode"
                  >
                    {SEARCH_MODE_OPTIONS.map(({ mode, label, icon }) => {
                      const isActive = activeMode === mode;
                      const isDisabled =
                        mode !== "sql" && isSemanticUnavailable;
                      return (
                        <div key={mode} className="relative group/tooltip">
                          <button
                            type="button"
                            onClick={() => handleSearchMode(mode)}
                            disabled={isDisabled}
                            aria-pressed={isActive}
                            data-testid={`mode-${mode}`}
                            className={`segment flex items-center gap-2 px-4 py-2 text-sm font-medium cursor-pointer ${
                              isActive
                                ? "segment-on"
                                : isDisabled
                                  ? "text-ink-faint cursor-not-allowed"
                                  : "text-ink-soft hover:text-ink"
                            }`}
                          >
                            <span className="material-symbols-outlined text-[18px]">
                              {icon}
                            </span>
                            {label}
                          </button>
                          {isDisabled && (
                            <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2.5 py-1 bg-inverse text-on-inverse text-xs rounded-md whitespace-nowrap opacity-0 group-hover/tooltip:opacity-100 transition-opacity pointer-events-none z-50">
                              The AI Librarian is not available
                              <span className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-slate-800" />
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  {/* Species toggle — maps to the organism filter */}
                  <div
                    className="segment-track inline-flex"
                    role="group"
                    aria-label="Species"
                  >
                    {SPECIES_OPTIONS.map(({ key, label, icon }) => {
                      const isActive = activeSpecies === key;
                      const isDisabled =
                        activeMode === "semantic" && !speciesSupported(key);
                      return (
                        <div key={key} className="relative group/species">
                          <button
                            type="button"
                            onClick={() => handleSpecies(key)}
                            disabled={isDisabled}
                            aria-pressed={isActive}
                            data-testid={`species-${key}`}
                            className={`segment flex items-center gap-2 px-4 py-2 text-sm font-medium cursor-pointer ${
                              isActive
                                ? "segment-on"
                                : isDisabled
                                  ? "text-ink-faint cursor-not-allowed"
                                  : "text-ink-soft hover:text-ink"
                            }`}
                          >
                            <span className="material-symbols-outlined text-[18px]">
                              {icon}
                            </span>
                            {label}
                          </button>
                          {isDisabled && (
                            <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2.5 py-1 bg-inverse text-on-inverse text-xs rounded-md whitespace-nowrap opacity-0 group-hover/species:opacity-100 transition-opacity pointer-events-none z-50">
                              The AI Librarian is not available for this species
                              <span className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-slate-800" />
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  {/* On the row with the two toggles rather than down among the
                      filter selectors: like them it decides what you are
                      looking at rather than narrowing a set, and it belongs
                      with the controls that answer "which search is this".

                      In both modes. Looking a sample up by its accession is a
                      third way of asking, not a feature of one of the other
                      two, and a control that vanished when you changed mode
                      would be one more thing to go and find again. */}
                  <AccessionLookup />
                </div>

                {/* Selected species has no vector index in this deployment */}
                {activeMode === "semantic" &&
                  isMouseSelected &&
                  !speciesSupported("mouse") && (
                    <div
                      className="flex items-center gap-2 text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-4 py-2.5 text-sm"
                      data-testid="mouse-semantic-warning"
                    >
                      <span className="material-symbols-outlined text-[18px]">
                        info
                      </span>
                      The AI Librarian is not available for Mus musculus on this server.
                      Switch to Keyword Search, or select Homo sapiens.
                    </div>
                  )}

                {/* Keyword hint — only once an empty search has been asked for */}
                {activeMode === "semantic" &&
                  !filters.keyword &&
                  emptyQueryAttempted && (
                    <div
                      className="flex items-center gap-2 text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-4 py-2.5 text-sm"
                      data-testid="keyword-hint"
                    >
                      <span className="material-symbols-outlined text-[18px]">
                        info
                      </span>
                      Ask the AI Librarian for something. Describe what you are
                      looking for in plain language.
                    </div>
                  )}

                {/* Natural-language input — semantic mode only */}
                {activeMode === "semantic" && (
                  <form
                    onSubmit={handleSearchSubmit}
                    className="relative max-w-5xl"
                  >
                    <div className="absolute inset-y-0 left-0 pl-4 flex items-center pointer-events-none z-10">
                      <span className="material-symbols-outlined text-brand">
                        auto_awesome
                      </span>
                    </div>
                    <input
                      type="text"
                      value={inputValue}
                      onChange={(e) => setInputValue(e.target.value)}
                      placeholder={`Describe context (e.g., '${exampleQueries[0]}')...`}
                      className="block w-full pl-12 pr-36 py-4 bg-surface border border-edge rounded-xl shadow-control placeholder-ink-faint focus:outline-none focus:ring-2 focus:ring-brand/20 focus:border-edge-firm text-lg transition-shadow"
                    />
                    <div className="absolute inset-y-0 right-2 flex items-center">
                      <button
                        type="submit"
                        className="bg-brand hover:bg-brand-soft text-on-brand px-6 py-2 rounded-lg text-sm font-semibold cursor-pointer transition-colors flex items-center gap-2"
                      >
                        <span className="material-symbols-outlined text-[18px]">
                          search
                        </span>
                        <span>Search</span>
                      </button>
                    </div>
                  </form>
                )}

                {/* Example queries — one-click starting points for semantic search */}
                {activeMode === "semantic" && (
                  <div
                    className="flex flex-wrap items-center gap-2"
                    data-testid="example-queries"
                  >
                    <span className="text-sm text-ink-soft">Try:</span>
                    {exampleQueries.map((query) => (
                      <button
                        key={query}
                        type="button"
                        onClick={() => handleExampleQuery(query)}
                        className="px-3 py-1.5 bg-surface border border-edge rounded-full text-sm text-ink-body hover:border-edge-firm hover:text-brand cursor-pointer transition-colors"
                      >
                        {query}
                      </button>
                    ))}
                  </div>
                )}

                {/* What is currently filtered is reported by the sidebar's Active
                  Filters card, not here: a row of chips above the results grew
                  with every selection and pushed the results themselves off the
                  first screen. */}

                {/* Query selectors (keyword mode) / refine filters (semantic mode) */}
                <div className="flex flex-wrap gap-3 items-center pb-2 relative z-40">
                  {FILTER_CATEGORIES.map(({ key, label }) => {
                    const category = filterOptions?.categories.find(
                      (c) => c.name === key,
                    );
                    const selected =
                      (filters[key as keyof typeof filters] as
                        string[] | undefined) ?? [];
                    if (!category) {
                      return (
                        <button
                          key={key}
                          type="button"
                          className="flex items-center gap-2 px-3 py-2 bg-surface border border-edge rounded-lg text-sm font-medium text-ink-faint cursor-not-allowed"
                          disabled
                        >
                          {label}
                          <span className="material-symbols-outlined text-[18px]">
                            expand_more
                          </span>
                        </button>
                      );
                    }
                    return (
                      <FilterDropdown
                        key={key}
                        label={label}
                        category={category}
                        selected={selected}
                        onChange={(values) => setFilter(key, values)}
                      />
                    );
                  })}
                  {/* Left with the dropdowns it acts on, not pushed to the far edge
                    of the column. Out there it ended up hard against the sidebar
                    card, reading as though it belonged to that instead. */}
                  <button
                    type="button"
                    onClick={clearFilters}
                    className="ml-1 text-sm text-brand font-medium hover:underline cursor-pointer flex items-center gap-1 transition-colors"
                  >
                    <span className="material-symbols-outlined text-[18px]">
                      refresh
                    </span>
                    Reset All
                  </button>

                </div>
              </section>
              {/* The wide column: the results, or the map once the two have traded
                places. */}
              {swapped ? mapPanel : resultsPanel}
            </div>

            {/* Sidebar */}
            {/* space-y-4 to match the gap between the result cards. Its own
              scroller rather than a sticky block: pinning kept the top of the
              column in view but put its foot out of reach once the cards added
              up to more than a screen.

              Below lg it goes into a drawer instead of stacking under the
              results. Stacked, it was still reachable in the sense that it was
              on the page — under a full screen of result cards, which is not
              reachable in any sense that matters when what you wanted was to
              change how many results there are. */}
            {narrow ? (
              <SidePanel label="Search tools">
                <div className="space-y-4 p-4">{sidebar}</div>
              </SidePanel>
            ) : (
              <aside className="hidden lg:block lg:col-span-4 space-y-4 pb-6 lg:pt-6 lg:min-h-0 lg:overflow-y-auto scroll-stable overscroll-contain shadow-room">
                {sidebar}
              </aside>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

// Page export with Suspense (required for useSearchParams)
export default function SearchPage() {
  return (
    <Suspense
      fallback={
        <div className="bg-canvas flex-1 min-h-0 flex flex-col font-sans">
          <main className="flex-1 min-h-0 overflow-y-auto scroll-stable">
            {/* Same box as the loaded page, so nothing shifts when it arrives. */}
            <div className="max-w-[1600px] w-full mx-auto px-4 sm:px-6 lg:px-8 py-6">
              <div className="flex flex-col gap-4">
                {[1, 2, 3, 4].map((i) => (
                  <SampleCardSkeleton key={i} />
                ))}
              </div>
            </div>
          </main>
        </div>
      }
    >
      <SearchPageContent />
    </Suspense>
  );
}
