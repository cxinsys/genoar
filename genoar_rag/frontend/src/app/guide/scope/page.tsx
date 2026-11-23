import type { Metadata } from "next";
import Link from "next/link";
import Footer from "@/components/Footer";
import GuideHeader from "@/components/GuideHeader";
import CorpusLevels from "@/components/CorpusLevels";

export const metadata: Metadata = {
  title: "GENOAR - Data Scope",
  description:
    "What the GENOAR corpus holds, how its studies were selected, and where its boundaries are.",
};

// The guide page's shells, so the three tabs read as one place.
const CARD_STILL = "bg-surface rounded-card border border-edge shadow-card";
const ICON_TILE =
  "w-9 h-9 shrink-0 rounded-lg inline-flex items-center justify-center leading-none";
const CODE = "font-mono text-xs bg-sunken px-1 py-0.5 rounded";

/** The GEO search terms the crawler was given. From the paper's supplement;
 *  listed here because a reader judging what the corpus can and cannot show
 *  needs to know what was asked for. */
const SEARCH_TERMS = [
  "human (or mouse, mice)",
  "Homo sapiens (Mus musculus)",
  "transcriptomics",
  "10X Chromium",
  "single-cell transcriptomics",
  "scRNA-seq",
];

function SectionHead({
  icon,
  title,
  blurb,
}: {
  icon: string;
  title: string;
  blurb: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <div className={`${ICON_TILE} bg-accent/12 text-accent`}>
        <span className="material-symbols-outlined">{icon}</span>
      </div>
      <div>
        <h2 className="text-xl font-bold text-ink">{title}</h2>
        <p className="text-sm text-ink-soft">{blurb}</p>
      </div>
    </div>
  );
}

export default function DataScopePage() {
  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="flex-1 min-h-0 overflow-y-auto scroll-stable flex flex-col">
        <main className="flex-1 w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-8">
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
            <span className="text-brand font-semibold">Data Scope</span>
          </nav>

          <GuideHeader active="data" />

          <div className="flex flex-col gap-6 mb-12">
            <SectionHead
              icon="stacked_bar_chart"
              title="What a number counts"
              blurb="Studies, GEO samples, runs and cells are counted separately."
            />
            <div className={`${CARD_STILL} p-6 flex flex-col gap-4`}>
              <p className="text-sm text-ink-body leading-relaxed">
                <b>Study</b>: a GEO series (GSE). <b>GEO sample</b>: one
                biological sample (GSM) within a study. <b>Run</b>: one
                sequencing of a sample (SRR); a sample sequenced across
                several lanes has several runs, each processed separately.{" "}
                <b>Cell</b>: a barcode called by Cell Ranger in a run&apos;s
                filtered matrix. Search pages list runs, as each processed
                matrix is per-run. The counts below are read live from the
                database the dashboard uses.
              </p>
              <CorpusLevels />
            </div>
          </div>

          <div className="flex flex-col gap-6 mb-12">
            <SectionHead
              icon="manage_search"
              title="How studies were selected"
              blurb="A keyword search of GEO, filtered by platform and read depth."
            />
            <div className={`${CARD_STILL} p-6 flex flex-col gap-4`}>
              <p className="text-sm text-ink-body leading-relaxed">
                Studies were collected from NCBI GEO with these search terms,
                for <i>Homo sapiens</i> and <i>Mus musculus</i>:
              </p>
              <div className="flex flex-wrap gap-2">
                {SEARCH_TERMS.map((t) => (
                  <span key={t} className="bg-sunken rounded-md px-2 py-1 text-xs font-mono text-ink-body">
                    {t}
                  </span>
                ))}
              </div>
              <p className="text-sm text-ink-body leading-relaxed">
                Inclusion criteria: 10x Genomics Chromium platform, and read
                depth at or above the threshold stated in the paper. Included
                runs were processed with Cell Ranger against the species
                reference genome. Reference, chemistry and Cell Ranger version
                are reported per run on each sample page, read from the matrix
                file. Tissue and disease composition follows what was
                deposited under these terms.
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-6 mb-12">
            <SectionHead
              icon="label"
              title="What the labels mean"
              blurb="Tissue, disease and cell type are UMLS concepts mapped from submitter text."
            />
            <div className={`${CARD_STILL} p-6 flex flex-col gap-3`}>
              <p className="text-sm text-ink-body leading-relaxed">
                Tissue, disease and cell-type values are UMLS concepts
                (release 2024AB) mapped from the submitter&apos;s free text.
                Disease labels were resolved by the AI nomenclaturist and
                reviewed. Every run carries a tissue where one could be
                mapped. A disease label is present where the metadata
                described one; the dashboard&apos;s metadata charts give the
                share per species. Each sample page states, under its disease
                label, whether a UMLS concept is assigned and how the label is
                classified.
              </p>
              <p className="text-sm text-ink-body leading-relaxed">
                Curated concepts and CUIs are included in every export. The
                submitter&apos;s original wording is at the linked GEO record.
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-6 mb-16">
            <SectionHead
              icon="rule"
              title="Boundaries"
              blurb="What the corpus includes and excludes."
            />
            <div className={`${CARD_STILL} p-6 flex flex-col gap-3`}>
              <ul className="text-sm text-ink-body leading-relaxed list-disc pl-5 flex flex-col gap-2">
                <li>
                  <b>Species.</b> <i>Homo sapiens</i> and <i>Mus musculus</i>.
                  The published pipeline&apos;s end-to-end configuration
                  targets human (GRCh38).
                </li>
                <li>
                  <b>Platform.</b> 10x Chromium single-cell RNA-seq. Other
                  single-cell chemistries and bulk RNA-seq are excluded.
                </li>
                <li>
                  <b>Read depth.</b> Runs below the threshold stated in the
                  paper are excluded.
                </li>
                <li>
                  <b>Composition.</b> Tissue and disease counts follow GEO
                  deposits under the search terms; see the dashboard&apos;s
                  tissue chart.
                </li>
                <li>
                  <b>Unit.</b> One matrix per run. Lane-split runs of one
                  sample contain the same cells and must be merged before use;
                  see{" "}
                  <Link href="/guide" className="text-brand hover:underline">
                    Analysis Code Support
                  </Link>
                  .
                </li>
              </ul>
              <p className="text-xs text-ink-faint">
                Cell Ranger version, chemistry and reference per run: each
                sample&apos;s download section. Export columns:{" "}
                <code className={CODE}>preprocessing_software</code>,{" "}
                <code className={CODE}>preprocessing_chemistry</code>,{" "}
                <code className={CODE}>preprocessing_reference</code>.
              </p>
            </div>
          </div>
        </main>
        <Footer />
      </div>
    </div>
  );
}
