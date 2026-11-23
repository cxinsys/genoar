import type { Metadata } from "next";
import Link from "next/link";
import Footer from "@/components/Footer";
import GuideHeader from "@/components/GuideHeader";

export const metadata: Metadata = {
  title: "GENOAR - Analysis Code Support",
  description: "Notes on using the data GENOAR provides.",
};

// The API page's shells, so the two read as one site.
const CARD_STILL = "bg-surface rounded-card border border-edge shadow-card";
const ICON_TILE =
  "w-9 h-9 shrink-0 rounded-lg inline-flex items-center justify-center leading-none";
const CODE = "font-mono text-xs bg-sunken px-1 py-0.5 rounded";

/** Served from public/, so the link is the file itself. */
const MERGE_SCRIPT = "genoar_merge_library_groups.py";
const MERGE_SCRIPT_URL = `/downloads/${MERGE_SCRIPT}`;

/** The example's figure: UMAPs and bar charts, as one image. */
const EXAMPLE_FIGURE = "/guide/multi-srr-example.png";
const EXAMPLE_FIGURE_W = 2020;
const EXAMPLE_FIGURE_H = 1645;

/** Naive analysis against the corrected one, for the worked example below. */
const EXAMPLE_ROWS: {
  item: string;
  correct: string;
  naive: string;
  difference: string;
}[] = [
  {
    item: "Total cells",
    correct: "17,867",
    naive: "25,724",
    difference: "+44%",
  },
  {
    item: "Cells in the SRR10079362/363 library",
    correct: "7,925",
    naive: "15,782 (7,865 + 7,917)",
    difference: "2×",
  },
  {
    item: "Median genes per cell in this library",
    correct: "3,039",
    naive: "2,209 / 2,352",
    difference: "About 25% lower",
  },
  {
    item: "This library's share of all cells",
    correct: "44%",
    naive: "61%",
    difference: "Over-represented",
  },
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

export default function GuidePage() {
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
            <span className="text-brand font-semibold">Analysis Code Support</span>
          </nav>

          <GuideHeader active="guide" />

          <div className="flex flex-col gap-6 mb-12">
            <SectionHead
              icon="call_split"
              title="GEO samples split across several SRRs"
              blurb="Read this before using the processed h5 files of several SRRs from one GSM."
            />
            <div className={`${CARD_STILL} p-6 flex flex-col gap-3`}>
              <p className="text-sm text-ink-body leading-relaxed">
                Some GEO samples (GSMs) are split across multiple SRA runs
                (SRRs), and GENOAR provides an h5 file processed separately for
                each SRR. When the SRRs of a GSM are the same 10x library
                sequenced across multiple lanes, each SRR contains the same
                cells. Treating such SRRs as separate samples counts those cells
                more than once; they should therefore be merged with the
                provided script, <code className={CODE}>{MERGE_SCRIPT}</code>,
                before use. The script merges only SRRs that belong to the same
                library, as determined by cell barcode overlap, and does not
                merge SRRs from different channels.
              </p>
              <p className="text-sm text-ink-body leading-relaxed">
                Merging at the count level does not recover UMI deduplication
                across lanes, so UMI counts may be modestly inflated overall.
                The effect on normalized analyses (clustering, marker
                detection, relative expression) is small, but caution is
                advised when applying QC thresholds based on absolute raw UMI
                counts or when comparing with other datasets.
              </p>
            </div>

            <div className="flex items-start gap-4 p-4 border border-edge rounded-card bg-surface">
              <div className={`${ICON_TILE} bg-accent/12 text-accent`}>
                <span className="material-symbols-outlined">code</span>
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="font-semibold text-ink font-mono text-sm break-all">
                  {MERGE_SCRIPT}
                </h3>
                <p className="text-sm text-ink-soft mt-0.5">
                  Python 3 with scanpy, h5py, pandas and scipy. Usage is
                  described at the top of the script.
                </p>
              </div>
              <a
                href={MERGE_SCRIPT_URL}
                download
                className="shrink-0 inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-brand text-on-brand text-sm font-semibold hover:bg-brand-soft transition-colors"
              >
                <span
                  className="material-symbols-outlined"
                  style={{ fontSize: "18px" }}
                >
                  download
                </span>
                Download
              </a>
            </div>
          </div>

          <div className="flex flex-col gap-6 mb-16">
            <SectionHead
              icon="compare"
              title="Example"
              blurb="The same runs analysed naively and with the lane-split SRRs merged."
            />
            <div className={`${CARD_STILL} p-6 flex flex-col gap-4`}>
              <p className="text-sm text-ink-body leading-relaxed">
                SRR10079362, SRR10079363 and SRR10079365 analysed two ways:
                naively, with each SRR as its own sample, and with SRR10079362
                and SRR10079363, which differ only in sequencing lane, merged
                and SRR10079365 kept separate.
              </p>
              <div className="overflow-x-auto border border-edge rounded-lg">
                <table className="w-full text-left border-collapse">
                  <thead className="bg-raised text-xs uppercase text-ink-soft border-b border-edge">
                    <tr>
                      <th className="px-4 py-3 font-semibold">Item</th>
                      <th className="px-4 py-3 font-semibold text-right">
                        Correct handling
                      </th>
                      <th className="px-4 py-3 font-semibold text-right">
                        Naive
                      </th>
                      <th className="px-4 py-3 font-semibold">Difference</th>
                    </tr>
                  </thead>
                  <tbody className="text-sm divide-y divide-edge">
                    {EXAMPLE_ROWS.map((row) => (
                      <tr key={row.item}>
                        <td className="px-4 py-3 text-ink-body">{row.item}</td>
                        {/* nowrap: on a narrow screen the table scrolls in its
                            own box rather than breaking "7,865 + 7,917" across
                            three lines. */}
                        <td className="px-4 py-3 text-right font-mono text-ink whitespace-nowrap">
                          {row.correct}
                        </td>
                        <td className="px-4 py-3 text-right font-mono text-ink whitespace-nowrap">
                          {row.naive}
                        </td>
                        <td className="px-4 py-3 font-semibold text-ink whitespace-nowrap">
                          {row.difference}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {/* Opens full size in a new tab: the panel labels are small once
                  the figure is fitted to a phone. */}
              <a
                href={EXAMPLE_FIGURE}
                target="_blank"
                rel="noopener noreferrer"
                className="block border border-edge rounded-lg overflow-hidden"
              >
                {/* A plain img rather than next/image, like the loading frames:
                    one fixed figure, served as it is. Width and height are given
                    so the space is held before it loads. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={EXAMPLE_FIGURE}
                  width={EXAMPLE_FIGURE_W}
                  height={EXAMPLE_FIGURE_H}
                  alt="A: UMAP with SRR10079362 and SRR10079363 merged, 2 samples, 17,867 cells. B: UMAP with each SRR as a separate sample, 3 samples, 25,724 cells, where SRR10079362 and SRR10079363 overlap point for point. C: cells per sample after QC. D: median genes per cell."
                  loading="lazy"
                  className="w-full h-auto"
                />
              </a>
              <p className="text-xs text-ink-faint">
                Select the figure to open it at full size.
              </p>
            </div>
          </div>
          <div className="flex flex-col gap-6 mb-16">
            <SectionHead
              icon="terminal"
              title="Running the pipeline yourself"
              blurb="What the published pipeline needs on an ordinary machine."
            />
            <div className={`${CARD_STILL} p-6 flex flex-col gap-4`}>
              <p className="text-sm text-ink-body leading-relaxed">
                The pipeline is published at{" "}
                <a
                  href="https://github.com/cxinsys/genoar"
                  className="text-brand hover:underline"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  github.com/cxinsys/genoar
                </a>{" "}
                and runs in Docker. Stages 1 and 2 (collection and curation)
                run from a single <code className={CODE}>make run</code> on
                any machine with Docker. Stage 3 (Cell Ranger) has
                prerequisites that cannot be shipped:
              </p>
              <div className="overflow-x-auto border border-edge rounded-lg">
                <table className="w-full text-left border-collapse">
                  <thead className="bg-raised text-xs uppercase text-ink-soft border-b border-edge">
                    <tr>
                      <th className="px-4 py-3 font-semibold">Stage 3 needs</th>
                      <th className="px-4 py-3 font-semibold">How you get it</th>
                    </tr>
                  </thead>
                  <tbody className="text-sm divide-y divide-edge">
                    {[
                      ["An x86_64 machine", "Cell Ranger is not distributed for ARM."],
                      ["Cell Ranger", "Manual download from 10x Genomics (registration required)."],
                      ["A reference genome (GRCh38)", "Manual download, about 15 GB. The published path is human; other organisms need manual changes."],
                      [".sra input files", "make fetch-sra caches the runs selected in Stage 2; two samples by default."],
                      ["configs/example.yaml", "Ships with the repository; set the core count and paths."],
                    ].map(([need, how]) => (
                      <tr key={need}>
                        <td className="px-4 py-3 text-ink font-medium whitespace-nowrap">{need}</td>
                        <td className="px-4 py-3 text-ink-body">{how}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-sm text-ink-body leading-relaxed">
                <code className={CODE}>make run-stage3</code> checks all five
                before it starts and names the missing one. Expected runtime
                and memory depend on the sample and the core count; the
                repository README gives the commands and the storage each
                stage writes.
              </p>
            </div>
          </div>
        </main>
        <Footer />
      </div>
    </div>
  );
}
