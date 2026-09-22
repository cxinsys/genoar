"use client";

import { displayLabel, displayCellType } from "@/lib/display";
import Link from "next/link";
import type { CSSProperties } from "react";
import { familyColor, inkOf, tintOf, tissueFamily } from "@/lib/tissueFamily";
import { usePaletteId } from "@/hooks/usePaletteId";
import type { SampleSummary } from "@/types/api";
import { sampleHref, type Dataset } from "@/lib/datasets";

// A chip's colour says which field it is. Tissue and cell type were the same
// blue, so a card with both showed two identical chips and the colour told you
// nothing you could not already read; a hue per field means the same chip is
// the same colour on every card, and a card scanned quickly still separates
// "where it came from" from "what the cells are" from "what was wrong".
//
// Classes rather than utilities, because there are three themes to answer and a
// utility can only be given a dark variant. Written out in globals.css beside
// everything else that has to be said three times.
const CHIP_TONE = {
  tissue: "chip-tissue",
  cellType: "chip-cell",
  disease: "chip-disease",
} as const;

function MetaChip({
  tone,
  title,
  children,
}: {
  tone: string;
  title: string;
  children?: string | null;
}) {
  if (!children) return null;
  return (
    <span
      title={`${title}: ${children}`}
      className={`inline-flex items-center px-2 py-0.5 rounded border text-[11px] font-medium ${tone}`}
    >
      {children}
    </span>
  );
}

/** A small grey fact in the card's footer: an icon and a value. */
function FootNote({
  icon,
  children,
}: {
  icon: string;
  children?: string | null;
}) {
  if (!children) return null;
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-ink-soft">
      <span className="material-symbols-outlined text-[14px]! leading-none text-ink-faint">
        {icon}
      </span>
      {children}
    </span>
  );
}

type SampleCardProps = {
  sample: SampleSummary;
  /** Show the sample's other accessions — its series and its biosample.
   *
   *  Off by default. Browsing everything, they are two more codes in a grid of
   *  cards already carrying one; reading a result list, the series is how you
   *  tell whether five hits came from five experiments or from one, and the
   *  biosample is what a run is looked up by outside the SRA. */
  showAccessions?: boolean;
  /** The corpus this card was listed from, carried to the sample it opens. */
  dataset?: Dataset;
};

/**
 * One sample, as a card.
 *
 * Both the dashboard's explorer grid and the search results render this, so the
 * same sample looks the same wherever it is met. Two components written
 * separately from the same fields drift into two layouts of identical
 * information.
 */
export default function SampleCard({
  sample,
  showAccessions = false,
  dataset,
}: SampleCardProps) {
  // The sample page is reachable from both corpora and they are not nested, so
  // the way in has to say which one it came from. Without it a card listed on
  // the dashboard opens a page that asks the search page's corpus for a sample
  // only the dashboard's holds, and is told there is no such sample.
  const href = sampleHref(sample.run_id, dataset);
  const family = tissueFamily(sample.tissue);
  const palette = usePaletteId();
  const familyHue = familyColor(family, palette);
  const pct =
    sample.similarity_score != null
      ? Math.round(sample.similarity_score * 100)
      : null;

  return (
    // @container, and not a screen breakpoint: these are laid out one, two or
    // three to a row depending on the window, so a card 400px wide can be on a
    // 1600px screen and a card 700px wide on a 900px one. What decides whether
    // the title block and the chips fit beside each other is this card's width
    // and nothing else, so this card's width is what is asked.
    // The whole card is the link, because hover cannot be the way in. A "View
    // Details" control in the corner that appears on hover never appears on a
    // touch screen, which leaves the cards on a phone unopenable. A card that
    // is entirely a link is reachable by tap, by click anywhere on it, and by
    // keyboard, and the corner control stays as the thing that says so on a
    // pointer.
    <Link
      href={href}
      aria-label={`${sample.run_id}: view details`}
      className="@container group bg-surface border border-edge rounded-card shadow-card hover:shadow-card-raised transition-shadow relative flex flex-col overflow-hidden"
    >
      {/* The title sits on a block of the sample's tissue-system colour, held
          into the card's top-left corner: flush with both edges, rounded only
          where it leaves them. The card clips its own corner, so only the inner
          one is stated here. It covers the title and the two identifiers and
          stops — a heading with its labels, not a banner across the card.

          The right padding is 18 against 16 elsewhere, and is meant to be: the
          block is as wide as its widest row, so on the right the colour is a
          narrow strip that the corner cut then curves away from, while on the
          left it runs the block's full height. Equal padding measures equal and
          looks tight, so the right side is given the 2px back.

          Narrow, the two stop sharing a row. Side by side in 380px the block is
          held under 62% and the chips are crushed into what is left, so a value
          like "heparinized blood" breaks across three lines and the card ends
          up taller than the one that gave the chips a row of their own. So they
          get one: the block widens to most of the card — stretched, rather than
          sized by its text, which is what makes the corner cut a real curve
          instead of a nick — and the chips sit under it, above the footer. */}
      <div className="flex flex-col @md:flex-row @md:items-start @md:gap-4">
        <div
          className="tissue-island max-w-[88%] @md:max-w-[62%] shrink-0 rounded-br-3xl pl-4 pr-4.5 pt-4 pb-3.5"
          style={
            {
              "--island-wash": tintOf(familyHue),
              "--island-ink": inkOf(familyHue),
            } as CSSProperties
          }
          title={family.label}
        >
          <div className="flex items-center gap-1.5 mb-1.5 flex-wrap">
            {/* The run id alone. The assay label sat beside it and said
                "RNA-Seq" on every card but 214, where the archive's own
                LibraryStrategy field read "OTHER" — the same single-cell
                transcriptomics under a label its submitter left unset. A
                badge that is constant where it is right and misleading where
                it is not was worth neither the width nor the reading. */}
            <span className="text-[11px] font-mono bg-surface/70 text-ink-body px-1.5 py-0.5 rounded">
              {sample.run_id}
            </span>
          </div>
          <h3 className="font-bold text-base leading-tight line-clamp-2">
            {sample.tissue ? displayLabel(sample.tissue) : sample.run_id}
          </h3>
        </div>

        {/* Off the island and against the card's own edge, level with the
            identifiers inside it: the row reads across as what the sample is,
            then what it contains. flex-1 is what carries them to the right —
            justify-end alone only aligns within a box the chips themselves
            size, which left them leaning against the island. */}
        {/* pr matches pt so the chips sit the same distance from the card's top
            and right edges; the row's gap keeps them off the island when a long
            value grows the block towards them.

            On their own row they go back to the left, where everything else on
            the card starts. Pushed right they would line up with nothing, and
            the eye would have to cross the card to find them. */}
        <div className="flex flex-wrap gap-1.5 px-4 pt-3 @md:flex-1 @md:min-w-0 @md:justify-end @md:px-0 @md:pr-4 @md:pt-4">
          <MetaChip tone={CHIP_TONE.tissue} title="Tissue">
            {displayLabel(sample.tissue)}
          </MetaChip>
          <MetaChip tone={CHIP_TONE.cellType} title="Cell type">
            {displayCellType(sample.cell_type)}
          </MetaChip>
          <MetaChip tone={CHIP_TONE.disease} title="Disease">
            {displayLabel(sample.disease)}
          </MetaChip>
          {/* The name the disease filter knows it by, when that is not the words
              on the chip above. Without it, filtering by "Parkinson Disease"
              returns cards reading "Idiopathic Parkinson's disease (IPD)" with
              nothing to connect the two. */}
          {sample.disease_category &&
            sample.disease_category !== sample.disease && (
              <MetaChip
                tone={CHIP_TONE.disease}
                title="Disease category (what the filter uses)"
              >
                {displayLabel(sample.disease_category)}
              </MetaChip>
            )}
        </div>
      </div>

      {/* One inset for the whole card — 16px on every side, matching the island's
          own padding and the chips' — so the title, the chips and the footer all
          start the same distance from the edge they sit against. */}
      <div className="px-4 pb-4 pt-3 @md:pt-4 flex flex-col flex-1">
        <div className="mt-auto flex flex-wrap items-center gap-x-3 gap-y-2">
          <FootNote icon="science">{displayLabel(sample.organism)}</FootNote>
          <FootNote icon="biotech">{displayLabel(sample.platform)}</FootNote>
          {showAccessions && (
            <>
              {/* Every study, not the one `sra_core.Series` happens to keep.
                  870 runs are in two, so filtering by GSE207938 produced a page
                  of cards all reading GSE207943 — the result contradicting the
                  filter that selected it. */}
              <FootNote icon="folder_open">
                {sample.all_series?.length
                  ? sample.all_series.join(", ")
                  : sample.series}
              </FootNote>
              <FootNote icon="label">{sample.biosample}</FootNote>
            </>
          )}

          {/* Only semantic and hybrid searches score a sample; elsewhere the
              field is null and nothing is drawn. It sits after the facts about
              the sample because it is not one of them — it says how far down the
              ranking this result was found.

              Kept to the brand navy rather than the card's own system colour: a
              bar that changed hue per row would be read as another category
              rather than as a length to compare against the row above. */}
          {pct != null && (
            <span
              title={`Similarity: ${pct}%`}
              data-testid="similarity-bar"
              className="inline-flex items-center gap-2"
            >
              <span className="block w-24 h-1.5 rounded-full bg-placeholder overflow-hidden">
                <span
                  className="block h-full rounded-full bg-brand"
                  style={{ width: `${pct}%` }}
                />
              </span>
              <span className="text-xs font-bold tabular-nums text-brand">
                {pct}%
              </span>
            </span>
          )}
        </div>
      </div>

      {/* Not a link, and it cannot be: the card around it is one, and an anchor
          inside an anchor is not something a browser will parse. It is a label
          for what the card does, which is all it is on a pointer anyway.

          aria-hidden because the card already says this. Read out, it would
          announce a second, unreachable "View Details" on every result. */}
      <div
        aria-hidden
        className="absolute right-3 bottom-3 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none"
      >
        {/* Type and not a chip, in either theme. A filled block of the brand
            colour is a second thing to look at on a card that is already the
            thing being looked at. */}
        <span className="text-xs font-bold px-3 py-1.5 rounded inline-flex items-center gap-1 text-brand group-hover:text-brand-soft transition-colors">
          View Details
          {/* Sized and centred like the footer's icons: without the size winning
              over the icon font's own 24px, and without leading-none collapsing
              its line box, the arrow sat above the middle of the label. */}
          <span className="material-symbols-outlined text-[14px]! leading-none">
            arrow_forward
          </span>
        </span>
      </div>
    </Link>
  );
}
