"use client";

import { useRef, useState, type RefObject } from "react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { searchParamsToFilters } from "@/hooks/useSearch";
import { useAnchoredPanel } from "@/hooks/useAnchoredPanel";
import { BUTTON, ICON, TIP } from "@/components/headerButton";
import { startDownload } from "@/components/DownloadToast";
import { buildExportUrl, buildSampleExportUrl } from "@/lib/api-client";
import {
  DASHBOARD_DATASET,
  NO_DATASET,
  parseDataset,
  type Dataset,
} from "@/lib/datasets";
import type { SearchFilters } from "@/types/api";

/** The accession a sample page is showing, or null anywhere else.
 *
 * On such a page the export is that sample and nothing else. The filters in the
 * URL are whatever search the reader arrived from, which is not what a Download
 * button means when there is one sample on screen. */
function sampleAccession(pathname: string | null): string | null {
  const match = pathname?.match(/^\/sample\/([^/]+)/);
  return match ? decodeURIComponent(match[1]) : null;
}

/** Whether the page has narrowed the catalogue at all.
 *
 * Decides whether the second, filtered download is offered. Paging and sorting
 * are not narrowing — they change the view of a set, not the set — and the
 * export drops them anyway. Nor is the dataset: it says which catalogue is being
 * described, not which part of one was kept. */
function isNarrowed(filters: SearchFilters): boolean {
  return Object.entries(filters).some(([key, value]) => {
    if (
      [
        "offset",
        "limit",
        "sort_by",
        "sort_order",
        "search_mode",
        "dataset",
      ].includes(key)
    )
      return false;
    return Array.isArray(value) ? value.length > 0 : value != null && value !== "";
  });
}

/** The second download's identity — the one that depends on the page. */
interface ContextExport {
  url: string;
  /** The row's heading. */
  label: string;
  /** The line under it — what the file holds, or why it is greyed out. */
  scope: string;
  icon: string;
  /** How the corner note names this file, plainly. */
  toast: string;
  /** False on a page that has narrowed nothing, where it has no set to offer. */
  enabled: boolean;
}

interface Props {
  open: boolean;
  /** The button this hangs from. Measured when the panel opens. */
  anchor: RefObject<HTMLElement | null>;
  /** The whole catalogue, always offered. */
  fullUrl: string;
  onClose: () => void;
  context: ContextExport;
}

/* The theme picker's row, so the two panels that hang off this header are the
   same object. Border and all: without it the rows read as text with a hover
   colour rather than as things to choose between. */
const ROW =
  "w-full flex items-start gap-3 rounded-xl px-3 py-3 text-left " +
  "border border-edge hover:bg-raised transition-colors cursor-pointer";

/* The same row with nothing to do: an offer the page cannot honour yet. Left in
   place rather than removed so the reader learns the filtered export exists and
   what turns it on, instead of it appearing from nowhere once they filter. */
const ROW_OFF =
  "w-full flex items-start gap-3 rounded-xl px-3 py-3 text-left " +
  "border border-edge opacity-50 cursor-not-allowed";

/** The ways to take the data away.
 *
 * Both downloads are shown at once rather than one control that changes with the
 * page: the whole catalogue is always there to take, and the filtered set joins
 * it when the page has one. Which of the two a reader wants is then their choice
 * to see, not the button's to guess.
 *
 * Built like the theme picker: a native <dialog> opened with showModal(), which
 * is where Esc, the focus trap, the inert page and a real ::backdrop come from
 * rather than being re-implemented.
 */
function DownloadMenu({ open, anchor, fullUrl, context, onClose }: Props) {
  const ref = useAnchoredPanel(open, anchor);

  return (
    <dialog
      ref={ref}
      onClose={onClose}
      onClick={(e) => {
        if (e.target === ref.current) onClose();
      }}
      aria-labelledby="download-menu-title"
      className="anchored-panel w-[min(26rem,calc(100vw-2rem))] rounded-2xl bg-surface text-ink-body p-0 shadow-2xl"
    >
      <div className="p-5 min-h-0 overflow-y-auto overscroll-contain">
        <h2 id="download-menu-title" className="text-base font-semibold text-ink">
          Download
        </h2>
        <p className="mt-1 text-sm text-ink-soft">
          Download the metadata, or use the API.
        </p>

        <ul className="mt-4 space-y-2">
          <li>
            {/* The whole catalogue, always. The href is a real download and
                works on its own; the click routes it through a fetch instead, so
                the corner note can follow it to completion (see DownloadToast).
                These curated CSVs are small enough to buffer; the large sample
                files are not sent this way. */}
            <a
              href={fullUrl}
              download
              onClick={(e) => {
                e.preventDefault();
                startDownload(fullUrl, "The full catalogue");
                onClose();
              }}
              className={ROW}
            >
              <span className="material-symbols-outlined shrink-0 text-[20px] leading-none text-brand mt-0.5">
                table_view
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-medium text-ink">
                  All metadata (CSV)
                </span>
                <span className="block text-xs text-ink-soft mt-0.5">
                  The whole catalogue. About 25 MB, so it takes a few seconds to start.
                </span>
              </span>
            </a>
          </li>

          <li>
            {/* The filtered set, when the page has one. A real anchor when it
                does; a greyed row that goes nowhere when it does not, so the
                offer is visible but plainly not yet available. */}
            {context.enabled ? (
              <a
                href={context.url}
                download
                onClick={(e) => {
                  e.preventDefault();
                  startDownload(context.url, context.toast);
                  onClose();
                }}
                className={ROW}
              >
                <span className="material-symbols-outlined shrink-0 text-[20px] leading-none text-brand mt-0.5">
                  {context.icon}
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-ink">
                    {context.label}
                  </span>
                  <span className="block text-xs text-ink-soft mt-0.5">
                    {context.scope}
                  </span>
                </span>
              </a>
            ) : (
              <div className={ROW_OFF} aria-disabled="true">
                <span className="material-symbols-outlined shrink-0 text-[20px] leading-none text-ink-faint mt-0.5">
                  {context.icon}
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium text-ink-soft">
                    {context.label}
                  </span>
                  <span className="block text-xs text-ink-soft mt-0.5">
                    {context.scope}
                  </span>
                </span>
              </div>
            )}
          </li>

          <li>
            <Link href="/api-docs" onClick={onClose} className={ROW}>
              <span className="material-symbols-outlined shrink-0 text-[20px] leading-none text-brand mt-0.5">
                code
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-medium text-ink">
                  API documentation
                </span>
                <span className="block text-xs text-ink-soft mt-0.5">
                  Documentation for the endpoints this website uses.
                </span>
              </span>
            </Link>
          </li>
        </ul>
      </div>
    </dialog>
  );
}

/** Which body of data the page at this address is showing.
 *
 * The dashboard's is whatever the deployment gave it; a sample page carries
 * the one it was opened from in its query string, because it is reachable from
 * every page; everything else is the search page and the default.
 */
function datasetOfPage(
  pathname: string,
  params: { get(name: string): string | null },
): Dataset {
  if (pathname === "/") return DASHBOARD_DATASET;
  if (pathname.startsWith("/sample/")) {
    return parseDataset(params.get("dataset"));
  }
  return NO_DATASET;
}

/** The header's Download control, and the menu it opens.
 *
 * An icon in the settings group rather than a word among the nav links: it is a
 * thing to do, not a place to go, and beside Dashboard and Search it read as a
 * third page. The menu offers two files at once — the whole catalogue always,
 * and the filtered set when the page has narrowed one — both read off the
 * address bar so this one control serves every page without being told anything:
 *
 *   /sample/SRR…   the whole catalogue, and that one sample
 *   /search?…      the whole catalogue, and the search being shown
 *   /              the whole catalogue; the filtered row waits for a filter
 *
 * Must be rendered inside a Suspense boundary: `useSearchParams` opts its tree
 * out of static rendering, and the header is in the root layout, so without one
 * it would take every page with it.
 */
export default function DownloadNav() {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const params = useSearchParams();
  const pathname = usePathname();
  const accession = sampleAccession(pathname);

  // The one part of the header that is not the search page's. Everything else
  // up there — the query box, the accession lookup — takes the reader to the
  // search page and so answers from the default dataset wherever it is
  // clicked. This button does not take them anywhere: it hands them the page
  // they are already on, so it has to be of that page's dataset, or a
  // dashboard showing another would offer a download of a set it never showed.
  const dataset = datasetOfPage(pathname, params);

  const filters = {
    ...searchParamsToFilters(new URLSearchParams(params.toString())),
    dataset,
  };

  // The whole catalogue, whatever the page has narrowed. Built from an empty
  // set of filters rather than the page's, so "All" means all even on a
  // filtered search — the same function makes it, so it is a valid filter set
  // and not a hand-built one that could drift from the type.
  const fullFilters = {
    ...searchParamsToFilters(new URLSearchParams()),
    dataset,
  };
  const fullUrl = buildExportUrl(fullFilters);

  const context: ContextExport = accession
    ? {
        url: buildSampleExportUrl(accession, "csv", dataset),
        label: "This sample (CSV)",
        scope: `Only ${accession}.`,
        icon: "description",
        toast: `Sample ${accession}`,
        enabled: true,
      }
    : isNarrowed(filters)
      ? {
          url: buildExportUrl(filters),
          label: "Filtered results (CSV)",
          scope: "Only the samples that match the filters on this page.",
          icon: "filter_alt",
          toast: "The filtered results",
          enabled: true,
        }
      : {
          url: "",
          label: "Filtered results (CSV)",
          scope: "Apply a filter or a search to enable this.",
          icon: "filter_alt",
          toast: "",
          enabled: false,
        };

  return (
    <>
      <button
        ref={button}
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label="Download"
        className={BUTTON}
      >
        <span className={ICON}>download</span>
        <span className={TIP}>Download</span>
      </button>
      <DownloadMenu
        open={open}
        anchor={button}
        fullUrl={fullUrl}
        context={context}
        onClose={() => setOpen(false)}
      />
    </>
  );
}
