"use client";

import { displayLabel, displayCellType } from "@/lib/display";
import { useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useSampleDetail } from "@/hooks/useSampleDetail";
import { useSeriesSamples } from "@/hooks/useSeriesSamples";
import { useSimilarSamples } from "@/hooks/useSimilarSamples";
import { useSampleDownload } from "@/hooks/useSampleDownload";
import { buildSampleExportUrl, type ApiError } from "@/lib/api-client";
import {
  parseDataset,
  sampleHref,
  type Dataset,
} from "@/lib/datasets";
import { DetailSkeleton, TableRowSkeleton } from "@/components/LoadingSkeleton";
import ErrorState from "@/components/ErrorState";
import Footer from "@/components/Footer";
import type {
  SampleDetail,
  SampleSummary,
  ExtendedField,
  SimilarSampleItem,
  DownloadSource,
  SampleDownloadResponse,
  ProcessingParameters,
} from "@/types/api";

// The shells this page draws with.
//
// Written once here rather than spelled out at each of the dozen places that
// want one, so that this page keeps saying the same thing as the rest of the
// site. Spelled out inline they drift: cards ask for their own hover border,
// headings pick their own size, and controls get hand-copied instead of taken
// from the stylesheet. Each of those is defensible alone and wrong together.

/** A card. The hover lifts and warms the border from the brand token rather
    than a fixed colour, so it follows the palette — hard-coding a blue here
    leaves this page navy while the brand around it is a sea green. */
const CARD =
  "bg-surface rounded-card border border-edge shadow-card " +
  "hover:border-brand/35 hover:shadow-card-raised transition-[box-shadow,border-color]";

/** The same, for a card nothing hovers. */
const CARD_STILL = "bg-surface rounded-card border border-edge shadow-card";

/** The two buttons this page has. Both are the height of a control elsewhere on
    the site, which is what lines them up with the header's own row. */
const BUTTON_QUIET =
  "inline-flex items-center gap-2 h-10 px-4 rounded-lg border border-edge-firm " +
  "bg-surface text-ink-body text-sm font-bold hover:bg-raised cursor-pointer " +
  "transition-colors shadow-control";
const BUTTON_BARE =
  "inline-flex items-center gap-1.5 h-10 px-3 rounded-lg text-ink-body text-sm " +
  "font-medium hover:text-brand hover:bg-raised transition-colors cursor-pointer";

/** What stands in a row whose action is that there is none.
 *
 * BUTTON_BARE's box and none of its affordances: no hover, no pointer, no
 * anchor. A row that simply left the column empty would read as one the page
 * had failed to finish, where this one is deliberately holding something back
 * and has to look deliberate. Keeping the height means the list's rows still
 * line up down their right-hand edge. */
const BUTTON_STANDIN =
  "inline-flex items-center gap-1.5 h-10 px-3 rounded-lg text-ink-soft text-sm " +
  "font-medium";

/** The rounded square an icon sits in.
 *
 * A fixed box and a centred glyph, rather than padding round the icon. An icon
 * font renders into a line box, and a line box is taller than the glyph it
 * carries — so `p-2` on its own gave every tile a couple of pixels of extra room
 * under the icon and a rectangle where a square was meant. Fixing both sides and
 * centring inside makes the tile the size it looks. */
const ICON_TILE =
  "w-9 h-9 shrink-0 rounded-lg inline-flex items-center justify-center leading-none";

/** A statement that something failed.
 *
 * The one place on the site that keeps a literal hue. Every other colour here is
 * a token so that a theme can answer it, and this one must not be answered: red
 * means this did not work in any palette, and a failure repainted in a page's own
 * accent is a failure that looks like part of the design. Alpha rather than a
 * fixed tint, so the same rule sits on white, on sand and on near-black. */
const ALERT =
  "rounded-card border border-red-500/25 bg-red-500/10 text-red-700 dark:text-red-300";

function val(v: string | null | undefined): string {
  // Empty as well as absent. `displayLabel` answers "" for a missing value,
  // and `??` alone would let that through as a blank row where the dash
  // belongs.
  return v == null || v === "" ? "--" : v;
}


function Breadcrumb({ runId }: { runId: string }) {
  return (
    <nav
      aria-label="Breadcrumb"
      className="flex flex-wrap gap-2 items-center mb-6 text-sm"
    >
      <Link
        href="/"
        className="text-ink-soft hover:text-brand font-medium flex items-center gap-1 transition-colors"
      >
        <span
          className="material-symbols-outlined"
          style={{ fontSize: "18px" }}
        >
          home
        </span>
        Home
      </Link>
      <span
        className="material-symbols-outlined text-ink-faint"
        style={{ fontSize: "16px" }}
      >
        chevron_right
      </span>
      <Link
        href="/search"
        className="text-ink-soft hover:text-brand font-medium transition-colors"
      >
        Search Results
      </Link>
      <span
        className="material-symbols-outlined text-ink-faint"
        style={{ fontSize: "16px" }}
      >
        chevron_right
      </span>
      <span className="text-brand font-semibold">{runId}</span>
    </nav>
  );
}

function SampleHero({ sample }: { sample: SampleDetail }) {
  const [copied, setCopied] = useState(false);

  function handleCopyLink() {
    if (typeof window !== "undefined") {
      navigator.clipboard.writeText(window.location.href).catch(() => {});
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-6 mb-8 pb-8 border-b border-edge">
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-3 flex-wrap">
          {/* type-figure is the role for a number or a code a card exists to
              show; the size stays here because it depends on the room. It was
              text-3xl font-black, which is the same intent said in a way nothing
              else on the site says it. */}
          <h1 className="type-figure text-3xl">{sample.run_id}</h1>
          {/* Access, in the teal the site uses for its second colour. Green-100
              on green-800 was a third scheme on a page that has two. */}
          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold bg-accent/12 text-accent border border-accent/25">
            Public
          </span>
        </div>
        <div className="flex items-center gap-2 text-ink-soft">
          <span
            className="material-symbols-outlined"
            style={{ fontSize: "18px" }}
          >
            biotech
          </span>
          <span className="text-sm font-medium">
            Sample:{" "}
            <span className="font-mono text-ink-body">
              {val(sample.biosample)}
            </span>
          </span>
        </div>
        {sample.series && (
          <div className="flex items-center gap-2 text-ink-soft">
            <span
              className="material-symbols-outlined"
              style={{ fontSize: "18px" }}
            >
              folder_open
            </span>
            <span className="text-sm font-medium">
              Series:{" "}
              <span className="font-mono text-ink-body">{sample.series}</span>
            </span>
          </div>
        )}
      </div>

      <div className="flex flex-wrap gap-3 items-center shrink-0">
        <button type="button" onClick={handleCopyLink} className={BUTTON_QUIET}>
          <span
            className="material-symbols-outlined"
            style={{ fontSize: "20px" }}
          >
            {copied ? "check" : "link"}
          </span>
          {copied ? "Copied!" : "Copy Link"}
        </button>
        <a
          href={`https://www.ncbi.nlm.nih.gov/sra/${sample.run_id}`}
          target="_blank"
          rel="noopener noreferrer"
          className={BUTTON_BARE}
        >
          View on SRA
          <span
            className="material-symbols-outlined"
            style={{ fontSize: "16px" }}
          >
            north_east
          </span>
        </a>
      </div>
    </div>
  );
}

function MetaRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className="grid gap-2 items-baseline"
      style={{ gridTemplateColumns: "120px 1fr" }}
    >
      <span className="text-sm text-ink-soft font-normal">{label}</span>
      <div>{children}</div>
    </div>
  );
}

/** How the sample's matrix was made, on one line under the cards that describe
 *  the sample itself.
 *
 * Here rather than in the download section, where it started: it is a fact
 * about the sample, and a reader deciding whether this sample is usable wants
 * it beside the tissue and the assay rather than further down among the files.
 *
 * Inside the same grid, spanning it, so the gap above matches the gaps between
 * the cards without a margin being written twice. */
function ProcessingLine({
  processing,
}: {
  processing: ProcessingParameters | null | undefined;
}) {
  const items = (
    [
      ["Software", processing?.software],
      ["Chemistry", processing?.chemistry],
      ["Reference", processing?.reference],
    ] as const
  ).filter(([, value]) => value);

  if (items.length === 0) return null;

  return (
    <div
      data-testid="processing-parameters"
      className={`${CARD_STILL} md:col-span-2 lg:col-span-3 px-5 py-3 flex flex-wrap items-center gap-x-8 gap-y-2`}
    >
      <h3 className="type-eyebrow">Preprocessing parameters</h3>
      {items.map(([label, value]) => (
        <span key={label} className="flex items-baseline gap-2">
          <span className="text-sm text-ink-soft">{label}</span>
          <span className="text-sm font-mono text-ink break-all">{value}</span>
        </span>
      ))}
    </div>
  );
}

function InfoCards({
  sample,
  processing,
}: {
  sample: SampleDetail;
  processing: ProcessingParameters | null | undefined;
}) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-10">
      {/* Core Metadata */}
      <div className={`${CARD} overflow-hidden flex flex-col`}>
        <div className="px-5 py-4 border-b border-edge bg-raised/50 flex items-center gap-2">
          <span
            className="material-symbols-outlined"
            style={{ color: "var(--color-brand)" }}
          >
            fingerprint
          </span>
          <h3 className="type-eyebrow">Core Metadata</h3>
        </div>
        <div className="p-5 flex flex-col gap-4 flex-1">
          <MetaRow label="Run ID">
            <div className="flex items-center gap-2">
              <span className="font-mono text-sm font-medium text-ink">
                {sample.run_id}
              </span>
              <button
                type="button"
                aria-label="Copy Run ID"
                className="text-ink-faint hover:text-brand cursor-pointer transition-colors"
                onClick={() =>
                  navigator.clipboard.writeText(sample.run_id).catch(() => {})
                }
              >
                <span
                  className="material-symbols-outlined"
                  style={{ fontSize: "16px" }}
                >
                  content_copy
                </span>
              </button>
            </div>
          </MetaRow>
          <MetaRow label="Sample ID">
            <span className="font-mono text-sm font-medium text-ink">
              {val(sample.biosample)}
            </span>
          </MetaRow>
          <MetaRow label="Sample Name">
            <span className="text-sm font-medium text-ink">
              {val(sample.sample_name)}
            </span>
          </MetaRow>
          {/* Plural, because 870 of the 6,199 runs belong to more than one study
              and the singular column only ever named one of them. Falls back to
              that column so the row still says something if the mapping is
              missing. */}
          <MetaRow label="Series">
            <span className="font-mono text-sm font-medium text-ink">
              {sample.all_series?.length
                ? sample.all_series.join(", ")
                : val(sample.series)}
            </span>
          </MetaRow>
        </div>
      </div>

      {/* Experimental Details */}
      <div className={`${CARD} overflow-hidden flex flex-col`}>
        <div className="px-5 py-4 border-b border-edge bg-raised/50 flex items-center gap-2">
          <span
            className="material-symbols-outlined"
            style={{ color: "var(--color-brand)" }}
          >
            science
          </span>
          <h3 className="type-eyebrow">Experimental Details</h3>
        </div>
        <div className="p-5 flex flex-col gap-4 flex-1">
          <MetaRow label="Library Source">
            <span className="text-sm font-bold text-ink">
              {val(displayLabel(sample.library_source))}
            </span>
          </MetaRow>
          <MetaRow label="Platform">
            <span className="text-sm font-medium text-ink">
              {val(displayLabel(sample.platform))}
            </span>
          </MetaRow>
          <MetaRow label="Instrument">
            <span className="text-sm font-medium text-ink">
              {val(displayLabel(sample.instrument))}
            </span>
          </MetaRow>
          {sample.size != null && (
            <MetaRow label="Size">
              <span className="text-sm font-medium text-ink">
                {formatBytes(sample.size)}
              </span>
            </MetaRow>
          )}
        </div>
      </div>

      {/* Biological Context */}
      <div className={`${CARD} overflow-hidden flex flex-col`}>
        <div className="px-5 py-4 border-b border-edge bg-raised/50 flex items-center gap-2">
          <span
            className="material-symbols-outlined"
            style={{ color: "var(--color-brand)" }}
          >
            biotech
          </span>
          <h3 className="type-eyebrow">Biological Context</h3>
        </div>
        <div className="p-5 flex flex-col gap-4 flex-1">
          <MetaRow label="Organism">
            <div>
              <span className="text-sm font-bold italic text-ink block">
                {val(displayLabel(sample.organism))}
              </span>
              {sample.cui_tis && (
                <span className="text-xs text-ink-faint block mt-0.5 font-mono">
                  CUI: {sample.cui_tis}
                </span>
              )}
            </div>
          </MetaRow>
          <MetaRow label="Tissue">
            <div className="flex flex-col">
              <span className="text-sm font-medium text-ink">
                {val(displayLabel(sample.tissue))}
              </span>
              {sample.str_tis && (
                <span className="text-xs text-ink-faint mt-0.5">
                  {displayLabel(sample.str_tis)}
                </span>
              )}
            </div>
          </MetaRow>
          <MetaRow label="Disease">
            <div className="flex flex-col">
              {sample.disease ? (
                <span className="chip-disease inline-flex items-center px-2 py-0.5 rounded border text-xs font-medium w-fit">
                  {displayLabel(sample.disease)}
                </span>
              ) : (
                <span className="text-sm text-ink">--</span>
              )}
              {/* The name the disease filter knows this sample by, shown when
                  it is not the words above. They differ for 455 of the 584
                  samples that have a disease — this page said "Idiopathic
                  Parkinson's disease (IPD)" while the filter had it under
                  "Parkinson Disease", and typing what was on screen into the
                  filter found nothing. The link is the way across. */}
              {sample.disease_category &&
                sample.disease_category !== sample.disease && (
                  <Link
                    href={`/search?disease=${encodeURIComponent(sample.disease_category)}`}
                    className="text-xs text-ink-soft mt-1 hover:text-brand transition-colors w-fit"
                  >
                    Filed under{" "}
                    <span className="font-medium underline decoration-dotted">
                      {displayLabel(sample.disease_category)}
                    </span>
                  </Link>
                )}
              {sample.cui_dis && (
                <span className="text-xs text-ink-faint mt-0.5 font-mono">
                  CUI: {sample.cui_dis}
                </span>
              )}
            </div>
          </MetaRow>
          <MetaRow label="Cell Type">
            <div className="flex flex-col">
              <span className="text-sm font-medium text-ink">
                {val(displayCellType(sample.cell_type))}
              </span>
              {sample.cui_cell && (
                <span className="text-xs text-ink-faint mt-0.5 font-mono">
                  CUI: {sample.cui_cell}
                </span>
              )}
            </div>
          </MetaRow>
          <MetaRow label="Sex">
            <span className="text-sm font-medium text-ink">
              {val(displayLabel(sample.sex))}
            </span>
          </MetaRow>
          {sample.age && (
            <MetaRow label="Age">
              <span className="text-sm font-medium text-ink">{sample.age}</span>
            </MetaRow>
          )}
          {sample.strain && (
            <MetaRow label="Strain">
              <span className="text-sm font-medium text-ink">
                {displayLabel(sample.strain)}
              </span>
            </MetaRow>
          )}
          {sample.genotype && (
            <MetaRow label="Genotype">
              <span className="text-sm font-medium text-ink">
                {displayLabel(sample.genotype)}
              </span>
            </MetaRow>
          )}
          {sample.treatment && (
            <MetaRow label="Treatment">
              <span className="text-sm font-medium text-ink">
                {displayLabel(sample.treatment)}
              </span>
            </MetaRow>
          )}
        </div>
      </div>

      <ProcessingLine processing={processing} />
    </div>
  );
}

function SeriesTable({
  runId,
  currentRunId,
  sharedSeries = [],
  dataset,
}: {
  runId: string;
  currentRunId: string;
  /** The studies of the sample being viewed, so each row can show which of
      them put it in this table. */
  sharedSeries?: string[];
  dataset: Dataset;
}) {
  // 100 is the API's ceiling, and it covers all but one of the 888 studies —
  // only GSE with 142 runs goes over. At 20 the table listed a fifth of a large
  // study under a heading that counted the whole of it, with nothing to say the
  // rest existed.
  const { data, isLoading, error } = useSeriesSamples(runId, {
    offset: 0,
    limit: 100,
    dataset,
  });

  if (error) return null;

  if (isLoading) {
    return (
      <div className={`${CARD_STILL} mb-10 overflow-hidden`}>
        <div className="border-b border-edge px-6 py-4 bg-raised/30">
          <h3 className="type-panel-title">Samples in This Series</h3>
        </div>
        <table className="w-full text-left">
          <tbody>
            <TableRowSkeleton rows={4} />
          </tbody>
        </table>
      </div>
    );
  }

  if (!data || data.total === 0) {
    return (
      <div
        className={`${CARD_STILL} mb-10 p-8 text-center text-ink-soft text-sm`}
      >
        This sample does not belong to a series.
      </div>
    );
  }

  return (
    <div className={`${CARD_STILL} mb-10 overflow-hidden`}>
      <div className="border-b border-edge px-6 py-4 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 bg-raised/30">
        <div className="flex items-center gap-3">
          <span
            className="material-symbols-outlined"
            style={{ color: "var(--color-accent)" }}
          >
            library_books
          </span>
          <div>
            <h3 className="type-panel-title">Samples in This Series</h3>
            {/* Says how many are on screen when that is not all of them. The
                count alone read as a promise the table did not keep. */}
            <span className="text-xs bg-sunken px-2 py-0.5 rounded-full">
              {data.items.length < data.total
                ? `${data.items.length} of ${data.total} Samples`
                : `${data.total} Samples`}
            </span>
          </div>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-left border-collapse">
          <thead className="bg-raised text-xs uppercase text-ink-soft border-b border-edge">
            <tr>
              <th className="px-6 py-3 font-semibold">Run ID</th>
              {/* Which study puts each row in this table. The panel is the union
                  of every study the sample belongs to, so without this column a
                  run appearing under a second study read as an unexplained
                  stranger. */}
              <th className="px-6 py-3 font-semibold">Series</th>
              <th className="px-6 py-3 font-semibold">Tissue</th>
              <th className="px-6 py-3 font-semibold">Cell Type</th>
              <th className="px-6 py-3 font-semibold text-right">Action</th>
            </tr>
          </thead>
          <tbody className="text-sm divide-y divide-edge">
            {data.items.map((s: SampleSummary) => {
              const isCurrent = s.run_id === currentRunId;
              return (
                <tr
                  key={s.run_id}
                  className={
                    isCurrent
                      ? "bg-brand/8 hover:bg-brand/12 transition-colors border-l-4"
                      : "hover:bg-raised transition-colors border-l-4 border-l-transparent"
                  }
                  style={
                    isCurrent
                      ? { borderLeftColor: "var(--color-brand)" }
                      : undefined
                  }
                >
                  <td
                    className={`px-6 py-3 font-mono ${isCurrent ? "font-bold" : "font-medium"} text-ink`}
                    style={
                      isCurrent ? { color: "var(--color-brand)" } : undefined
                    }
                  >
                    {s.run_id}
                  </td>
                  {/* Bold on the studies this sample shares with the one being
                      viewed, so a row's reason for being here is readable at a
                      glance rather than inferred. */}
                  <td className="px-6 py-3 font-mono text-xs text-ink-soft">
                    {(s.all_series?.length ? s.all_series : [s.series])
                      .filter(Boolean)
                      .map((gse, i) => (
                        <span key={gse}>
                          {i > 0 && ", "}
                          <span
                            className={
                              sharedSeries.includes(gse as string)
                                ? "font-semibold text-ink-body"
                                : undefined
                            }
                          >
                            {gse}
                          </span>
                        </span>
                      ))}
                  </td>
                  <td className="px-6 py-3 text-ink-body">{val(s.tissue)}</td>
                  <td className="px-6 py-3 text-ink-body">
                    {val(s.cell_type)}
                  </td>
                  <td className="px-6 py-3 text-right">
                    {isCurrent ? (
                      <span className="text-xs text-ink-soft font-semibold italic">
                        Current Selection
                      </span>
                    ) : (
                      <Link
                        href={sampleHref(s.run_id, dataset)}
                        className="hover:underline transition-colors font-medium"
                        style={{ color: "var(--color-brand)" }}
                      >
                        View
                      </Link>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ExtendedMetadata({ fields }: { fields: ExtendedField[] }) {
  const [view, setView] = useState<"table" | "json">("table");

  if (fields.length === 0) {
    return (
      <div
        className={`${CARD_STILL} mb-10 p-8 text-center text-ink-soft text-sm`}
      >
        No extended metadata available.
      </div>
    );
  }

  const jsonData = Object.fromEntries(
    fields.map((f) => [f.field_name, f.field_value]),
  );

  return (
    <div className={`${CARD_STILL} mb-10`}>
      <div className="border-b border-edge px-6 py-4 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div className="flex items-center gap-2">
          <span
            className="material-symbols-outlined"
            style={{ color: "var(--color-brand)" }}
          >
            dataset
          </span>
          <h3 className="type-panel-title">Extended Metadata</h3>
          <span className="text-xs bg-sunken px-2 py-0.5 rounded-full text-ink-body">
            {fields.length} attributes
          </span>
        </div>
        {/* The site's segmented control, not a second one built from the same
            parts. What was here was the same idea — a sunken track, the chosen
            face lifted onto a surface with a control shadow — written out by
            hand, which meant it missed the outline the real one grew when the
            segments turned out to be invisible against a dark card. */}
        <div
          className="segment-track inline-flex"
          role="group"
          aria-label="Extended metadata view"
        >
          {(
            [
              ["table", "Table View"],
              ["json", "JSON View"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setView(key)}
              aria-pressed={view === key}
              className={`segment px-3 py-1.5 text-sm font-medium cursor-pointer ${
                view === key
                  ? "segment-on text-ink!"
                  : "text-ink-soft hover:text-ink"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {view === "table" ? (
        <div className="p-6 overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead className="bg-raised text-xs uppercase text-ink-soft">
              <tr>
                <th className="px-4 py-3 font-semibold rounded-l-lg w-1/3">
                  Attribute
                </th>
                <th className="px-4 py-3 font-semibold w-1/3">Value</th>
                <th className="px-4 py-3 font-semibold rounded-r-lg w-1/3">
                  Type
                </th>
              </tr>
            </thead>
            <tbody className="text-sm divide-y divide-edge">
              {fields.map((f: ExtendedField) => (
                <tr
                  key={f.field_name}
                  className="hover:bg-raised transition-colors"
                >
                  <td className="px-4 py-3 font-medium text-ink-body">
                    {f.field_name}
                  </td>
                  <td className="px-4 py-3 text-ink font-mono">
                    {f.field_value ?? "--"}
                  </td>
                  <td className="px-4 py-3 text-ink-faint text-xs">
                    {f.data_type ?? "--"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="p-6">
          {/* Deliberately outside the token set: a code block is dark in both
              themes. Inverting it would make it the lightest thing on a dark page,
              and the one place a monospace green belongs is on black. */}
          {/* inverse is the token for a block that contradicts the page, which is
              what a code panel is. Green on near-black was a terminal quotation
              and the only place on the site that used either colour. */}
          <pre className="bg-inverse text-on-inverse rounded-lg p-5 text-sm font-mono overflow-x-auto leading-relaxed">
            {JSON.stringify(jsonData, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

const SOURCE_LABELS: Record<string, { title: string; icon: string }> = {
  metadata_csv: { title: "Metadata (CSV)", icon: "description" },
  // Named by what the pipeline did and by file extension. "Filtered matrix"
  // described a property of the contents and read as a selection GENOAR had
  // made.
  processed_h5: { title: "Preprocessed matrix (*.h5)", icon: "dataset" },
  raw_h5: { title: "Raw matrix (*.h5)", icon: "dataset" },
  molecule_info_h5: { title: "Molecule info (*.h5)", icon: "science" },
  geo_record: { title: "Raw data at GEO", icon: "open_in_new" },
  sra_record: { title: "Raw data at SRA", icon: "open_in_new" },
};

/** Bytes as a human-readable size. Files here run to tens of GB, so the unit matters.
 *
 * The Size row in the metadata panel cannot print a raw byte count under a fixed
 * unit: a 10.9 GB run reads as "10921218304.0 MB". Both species carry `size`, so
 * both reach that row. */
function formatBytes(bytes: number | null): string | null {
  if (bytes == null) return null;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

/** Whether GENOAR holds this source's file and is keeping it back.
 *
 * The service reports a source it will not hand over rather than dropping it,
 * and two unlike facts arrive wearing that same shape: the sample was never
 * processed, or it was processed and no run can account for the output.
 * `provenance` separates them. It is set only where the service found output on
 * disk to read a receipt beside, so an unavailable source carrying one is a
 * file that exists and is being withheld for a reason — and the reason names
 * something the reader can do. Asked of the data and not of `kind`, so a
 * withheld source of some other kind arrives on screen without another change
 * here. */
function isWithheld(source: DownloadSource): boolean {
  return !source.available && Boolean(source.provenance);
}

/** One source. Callers drop the sources the page has nothing to say about.
 *
 * Three forms, and the source decides which. A file served from here gets the
 * filled Download button; somebody else's page gets the quieter Open and the
 * outward arrow; output GENOAR is keeping back gets no control at all, and
 * says why in its place. */
function DownloadSourceRow({ source }: { source: DownloadSource }) {
  const label = SOURCE_LABELS[source.kind] ?? {
    title: source.kind,
    icon: "download",
  };
  const size = formatBytes(source.size_bytes);
  // Somebody else's page rather than a file from here, and it says so: the
  // quieter button and the outward arrow, the pair the header's "View on SRA"
  // already uses. A filled Download button on a link to an archive would be
  // claiming to hand over data that is not GENOAR's to hand over.
  const isVisit = source.action === "visit";
  const withheld = isWithheld(source);
  // The accent tile is the page saying "here is a file, take it". Only a row
  // that ends in a Download button has earned it; the other two forms take the
  // quiet tile, because neither of them hands anything over.
  const offers = !isVisit && !withheld;

  return (
    <div className="flex items-start gap-4 p-4 border border-edge rounded-card bg-surface">
      <div
        className={`${ICON_TILE} ${offers ? "bg-accent/12 text-accent" : "bg-raised text-ink-soft"}`}
      >
        <span className="material-symbols-outlined">{label.icon}</span>
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <h4 className="font-semibold text-ink">{label.title}</h4>
          {size && (
            <span className="text-xs text-ink-soft font-mono">{size}</span>
          )}
        </div>
        {source.description && (
          <p className="text-sm text-ink-soft mt-0.5">{source.description}</p>
        )}
        {withheld && source.note && (
          /* Below the description and not folded into it. The description says
             what is in the file and is true of every copy of it; the note is
             this deployment's account of one sample, and it ends with the thing
             to do about it. A reader who has just met a row with nothing to
             click is looking for exactly that sentence, so it sits in a well of
             its own rather than reading on from the line above — and in body
             ink rather than the description's softer grey, because it is the
             one part of this row that is worth acting on. */
          <p className="text-sm text-ink-body mt-2 px-3 py-2 rounded-lg bg-sunken">
            {source.note}
          </p>
        )}
      </div>

      {withheld ? (
        /* "Withheld" is the service's own word for this — its note says the
           file is named here and withheld — and the page gains nothing by
           inventing a second name for one decision. No alarm colour: nothing
           has failed, and a red row would send the reader looking for a fault
           where the answer is a pipeline run. */
        <span
          className={`${BUTTON_STANDIN} shrink-0`}
          data-testid={`withheld-${source.kind}`}
        >
          <span
            className="material-symbols-outlined"
            style={{ fontSize: "18px" }}
          >
            unpublished
          </span>
          Withheld
        </span>
      ) : (
        <a
          href={source.url ?? undefined}
          target="_blank"
          rel="noopener noreferrer"
          data-testid={`download-${source.kind}`}
          className={
            isVisit
              ? `${BUTTON_BARE} shrink-0 border border-edge`
              : "shrink-0 inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-brand text-on-brand text-sm font-semibold hover:bg-brand-soft transition-colors"
          }
        >
          {isVisit ? (
            <>
              Open
              <span
                className="material-symbols-outlined"
                style={{ fontSize: "16px" }}
              >
                north_east
              </span>
            </>
          ) : (
            <>
              <span
                className="material-symbols-outlined"
                style={{ fontSize: "18px" }}
              >
                download
              </span>
              Download
            </>
          )}
        </a>
      )}
    </div>
  );
}

/* Handed the response rather than fetching it, because the processing line
   under the cards above needs the same one. Two components each calling the
   hook is two requests for one answer. */
function DownloadSection({
  runId,
  data,
  isLoading,
  error,
  dataset,
}: {
  runId: string;
  data: SampleDownloadResponse | null;
  isLoading: boolean;
  error: ApiError | null;
  /** The corpus the metadata export should be taken from — this sample's row
      is written from whichever one the reader is looking at. */
  dataset: Dataset;
}) {
  // A source with a URL is on offer and is shown. A source without one was
  // left out entirely, on the reasoning that the page should only ever offer
  // things that work — which was right while a missing URL meant one thing.
  // It now means two. The sample was never processed, which is nothing to
  // report and stays out. Or it was processed and GENOAR will not vouch for
  // the output, which is a file the reader could have had, held back for a
  // reason they can act on; saying nothing there leaves them to conclude the
  // sample was never run. `isWithheld` is the difference, and it reads the
  // source rather than its kind.
  const shownSources = (data?.sources ?? []).filter(
    (s) => (s.available && s.url) || isWithheld(s),
  );

  return (
    <div className="flex flex-col gap-6 mb-16">
      <div className="flex items-center gap-3">
        <div className={`${ICON_TILE} bg-accent/12 text-accent`}>
          <span className="material-symbols-outlined">download</span>
        </div>
        <div>
          <h3 className="text-xl font-bold text-ink">Download Data</h3>
          <p className="text-sm text-ink-soft">
            The files GENOAR produced for this sample, and where its raw data is
            held. No account or API key is required.
          </p>
        </div>
      </div>

      {isLoading && (
        <div
          className="h-24 bg-sunken rounded-card animate-pulse"
          data-testid="download-skeleton"
        />
      )}

      {error && !isLoading && (
        <div className={`${ALERT} p-6 text-center text-sm`}>
          Failed to load download options.
        </div>
      )}

      {!isLoading && (
        <div className="flex flex-col gap-3">
          {/* Metadata needs no lookup: it is whatever the sample page is showing. */}
          <DownloadSourceRow
            source={{
              kind: "metadata_csv",
              available: true,
              action: "download",
              url: buildSampleExportUrl(runId, "csv", dataset),
              size_bytes: null,
              // "Curated" named one of the two corpora, and this row is
              // written from whichever the reader is in — so on a paper page
              // it called the paper's metadata the curated tables'. Neither
              // word is needed here: the page already says which corpus it is.
              description: "This sample's metadata, as CSV.",
              note: null,
            }}
          />
          {shownSources.map((source) => (
            <DownloadSourceRow key={source.kind} source={source} />
          ))}
          <p className="text-xs text-ink-faint">
            Raw reads are served by the public archive, not proxied through
            GENOAR.
            {data?.geo_accession && ` GEO accession: ${data.geo_accession}.`}
          </p>
        </div>
      )}
    </div>
  );
}

function SimilarSampleCard({
  item,
  dataset,
}: {
  item: SimilarSampleItem;
  dataset: Dataset;
}) {
  const pct = Math.round(item.similarity_score * 100);
  return (
    <Link href={sampleHref(item.run_id, dataset)}>
      <article className="bg-surface border border-edge rounded-card p-5 shadow-card hover:shadow-card-raised hover:border-highlight/40 transition-[box-shadow,border-color] group cursor-pointer h-full">
        <div className="flex justify-between items-start mb-3">
          <span className="font-mono text-sm font-bold text-brand group-hover:text-highlight transition-colors">
            {item.run_id}
          </span>
          <span className="text-sm font-semibold text-highlight">{pct}%</span>
        </div>

        <div className="w-full h-1.5 bg-sunken rounded-full mb-3">
          <div
            className="h-full bg-highlight rounded-full transition-all"
            style={{ width: `${pct}%` }}
          />
        </div>

        <div className="space-y-1 text-xs text-ink-body">
          {item.organism && (
            <div className="flex items-center gap-1.5">
              <span
                className="material-symbols-outlined"
                style={{ fontSize: "14px" }}
              >
                genetics
              </span>
              {displayLabel(item.organism)}
            </div>
          )}
          {item.tissue && (
            <div className="flex items-center gap-1.5">
              <span
                className="material-symbols-outlined"
                style={{ fontSize: "14px" }}
              >
                biotech
              </span>
              {displayLabel(item.tissue)}
            </div>
          )}
          {item.cell_type && (
            <div className="flex items-center gap-1.5">
              <span
                className="material-symbols-outlined"
                style={{ fontSize: "14px" }}
              >
                cell_tower
              </span>
              {displayCellType(item.cell_type)}
            </div>
          )}
          {item.disease && (
            <div className="flex items-center gap-1.5">
              <span
                className="material-symbols-outlined"
                style={{ fontSize: "14px" }}
              >
                medical_information
              </span>
              {displayLabel(item.disease)}
            </div>
          )}
        </div>
      </article>
    </Link>
  );
}

function SimilarSamplesSkeleton() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {[1, 2, 3].map((i) => (
        <div
          key={i}
          className="bg-surface border border-edge rounded-card p-5 shadow-card animate-pulse"
        >
          <div className="flex justify-between items-start mb-3">
            <div className="h-4 w-24 bg-placeholder rounded" />
            <div className="h-4 w-10 bg-placeholder rounded" />
          </div>
          <div className="w-full h-1.5 bg-sunken rounded-full mb-3" />
          <div className="space-y-2">
            <div className="h-3 w-32 bg-sunken rounded" />
            <div className="h-3 w-28 bg-sunken rounded" />
          </div>
        </div>
      ))}
    </div>
  );
}

function SimilarSamples({
  runId,
  dataset,
}: {
  runId: string;
  dataset: Dataset;
}) {
  const { data, isLoading, error, isUnavailable } = useSimilarSamples(
    runId,
    10,
    dataset,
  );

  return (
    <div className="flex flex-col gap-6 mb-16">
      <div className="flex items-center gap-3">
        <div className={`${ICON_TILE} bg-highlight/12 text-highlight`}>
          <span className="material-symbols-outlined">hub</span>
        </div>
        <div>
          <h3 className="text-xl font-bold text-ink">Similar Samples</h3>
          <p className="text-sm text-ink-soft">
            AI-powered recommendations based on semantic similarity.
          </p>
        </div>
      </div>

      {isUnavailable && (
        <div className="bg-raised rounded-card border border-dashed border-edge-firm p-8 text-center text-ink-faint text-sm">
          This feature is currently unavailable. The AI Librarian is not enabled
          on this server.
        </div>
      )}

      {isLoading && !isUnavailable && <SimilarSamplesSkeleton />}

      {error && !isUnavailable && (
        <div className={`${ALERT} p-8 text-center`}>
          <p className="text-sm mb-3">Failed to load similar samples.</p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-red-500/25 bg-red-500/12 text-sm font-medium hover:bg-red-500/20 transition-colors cursor-pointer"
          >
            <span
              className="material-symbols-outlined"
              style={{ fontSize: "16px" }}
            >
              refresh
            </span>
            Retry
          </button>
        </div>
      )}

      {data && data.items.length === 0 && !isUnavailable && (
        <div className="bg-raised rounded-card border border-edge p-8 text-center text-ink-faint text-sm">
          No similar samples found.
        </div>
      )}

      {data && data.items.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {data.items.map((item) => (
            <SimilarSampleCard
              key={item.run_id}
              item={item}
              dataset={dataset}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function SampleDetailPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const runId = params.id as string;
  // The one place a corpus has to be written down. This page is reachable from
  // both, and they are not nested, so which one the reader arrived from decides
  // whether this sample exists at all — and it has to survive a bookmark or a
  // link passed to somebody else, which only the address does.
  const dataset: Dataset = parseDataset(searchParams.get("dataset"));
  const { sample, isLoading, error, isNotFound } = useSampleDetail(
    runId,
    dataset,
  );
  // Fetched here rather than inside the download section, because the line of
  // processing parameters under the info cards reads from the same response.
  const {
    data: download,
    isLoading: downloadLoading,
    error: downloadError,
  } = useSampleDownload(runId, dataset);

  if (isNotFound) {
    return (
      <div className="flex-1 min-h-0 flex flex-col">
        <div className="flex-1 min-h-0 overflow-y-auto scroll-stable flex flex-col">
          <main className="flex-1 w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-8">
            <Breadcrumb runId={runId} />
            <ErrorState
              type="not-found"
              message={`Sample "${runId}" not found.`}
            />
          </main>
          <Footer />
        </div>
      </div>
    );
  }

  if (error && !isNotFound) {
    return (
      <div className="flex-1 min-h-0 flex flex-col">
        <div className="flex-1 min-h-0 overflow-y-auto scroll-stable flex flex-col">
          <main className="flex-1 w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-8">
            <Breadcrumb runId={runId} />
            <ErrorState
              type="network"
              onRetry={() => window.location.reload()}
            />
          </main>
          <Footer />
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="flex-1 min-h-0 overflow-y-auto scroll-stable flex flex-col">
        <main className="flex-1 w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-8">
          <Breadcrumb runId={runId} />
          {isLoading || !sample ? (
            <DetailSkeleton />
          ) : (
            <>
              <SampleHero sample={sample} />
              <InfoCards sample={sample} processing={download?.processing} />
              <DownloadSection
                runId={runId}
                data={download}
                isLoading={downloadLoading}
                error={downloadError}
                dataset={dataset}
              />
              {/* The series rows are keyed by run id, so the row to highlight has to
                be the resolved run rather than whatever accession the URL carried. */}
              <SeriesTable
                runId={sample.run_id}
                currentRunId={sample.run_id}
                dataset={dataset}
                sharedSeries={
                  sample.all_series?.length
                    ? sample.all_series
                    : sample.series
                      ? [sample.series]
                      : []
                }
              />
              <ExtendedMetadata fields={sample.extended_fields} />
              <SimilarSamples runId={runId} dataset={dataset} />
            </>
          )}
        </main>
        <Footer />
      </div>
    </div>
  );
}
