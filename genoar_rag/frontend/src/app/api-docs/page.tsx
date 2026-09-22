import type { Metadata } from "next";
import Link from "next/link";
import Footer from "@/components/Footer";

export const metadata: Metadata = {
  title: "GENOAR - API",
  description: "Query GENOAR's curated SRA metadata from your own code.",
};

/* Not at /api. The frontend rewrites /api/:path* to the backend, so a page on
   that path would be shadowed by the proxy it is documenting. */

// The sample page's shells, which this page borrows so the two read as one
// site. Written out rather than imported because that page keeps them as local
// constants; if a third page wants them they should move somewhere shared.
const CARD_STILL = "bg-surface rounded-card border border-edge shadow-card";
const ICON_TILE =
  "w-9 h-9 shrink-0 rounded-lg inline-flex items-center justify-center leading-none";
const CODE = "font-mono text-xs bg-sunken px-1 py-0.5 rounded";

interface Endpoint {
  method: string;
  path: string;
  summary: string;
  params?: string;
}

const ENDPOINTS: { group: string; icon: string; blurb: string; items: Endpoint[] }[] =
  [
    {
      group: "Search",
      icon: "search",
      blurb: "Endpoints for finding samples and listing filter values.",
      items: [
        {
          method: "GET",
          path: "/api/v1/search",
          summary:
            "Returns the samples matching the given filters. When search_mode is semantic and a keyword is given, the results are ranked by similarity to that keyword.",
          params:
            "keyword, search_mode (sql | semantic | hybrid), organism, tissue, cell_type, assay_type, library_source, disease, platform, series, top_k, offset, limit, sort_by, sort_order",
        },
        {
          method: "GET",
          path: "/api/v1/filters",
          summary:
            "Returns every filter category with its values and the number of samples for each.",
        },
        {
          method: "GET",
          path: "/api/v1/filters/{category}",
          summary:
            "Returns the values of one category, with paging. Used for the categories that have too many values to send at once.",
          params: "offset, limit, search",
        },
        {
          method: "GET",
          path: "/api/v1/stats",
          summary:
            "Returns the corpus totals, the number of samples per organism, and how many samples carry each combination of tissue, disease and cell type.",
        },
      ],
    },
    {
      group: "Samples",
      icon: "biotech",
      blurb: "Endpoints for an individual sample.",
      items: [
        {
          method: "GET",
          path: "/api/v1/samples",
          summary: "Returns a page of samples.",
          params: "offset, limit, sort_by, sort_order",
        },
        {
          method: "GET",
          path: "/api/v1/samples/{accession}",
          summary:
            "Returns the full record for one sample. Accepts SRA run ids (SRR, ERR, DRR) and GEO sample ids (GSM).",
        },
        {
          method: "GET",
          path: "/api/v1/samples/{accession}/series",
          summary: "Returns the other samples that belong to the same series.",
        },
        {
          method: "GET",
          path: "/api/v1/samples/{accession}/similar",
          summary:
            "Returns the samples whose embeddings are closest to this one. Requires the vector index for that species to be loaded.",
        },
        {
          method: "GET",
          path: "/api/v1/samples/{accession}/download",
          summary:
            "Returns the files and links available for this sample, and the parameters its matrix was produced with. See the section below for which of the files are served here.",
        },
        {
          method: "GET",
          path: "/api/v1/samples/{accession}/files/{filename}",
          summary:
            "Returns one processed output file. Only the filenames the pipeline produces are accepted.",
        },
      ],
    },
    {
      group: "Export and status",
      icon: "table_view",
      blurb: "Endpoints for bulk export and for checking the service.",
      items: [
        {
          method: "GET",
          path: "/api/v1/export",
          summary:
            "Returns the filtered samples as CSV or JSON. It takes the same filters as /search but no paging, and returns every matching row unless max_rows is set.",
          params:
            "the /search filters, plus format (csv | json), max_rows, accession",
        },
        {
          method: "GET",
          path: "/api/v1/datasets",
          summary:
            "Returns the datasets this deployment serves, with their labels and which one is the default. Deployments serving one body of data return a single entry.",
        },
        {
          method: "GET",
          path: "/health",
          summary:
            "Returns whether the service is running and which vector indexes are loaded.",
        },
      ],
    },
  ];

// On deployments that serve more than one dataset, every /api/v1 endpoint
// also accepts a `dataset` query parameter naming which one to answer from.
// Omitted, it means the default dataset; an unknown name is 404. The blurb
// is rendered once below the endpoint groups rather than repeated per row.
const DATASET_NOTE =
  "Deployments can serve more than one dataset (see GET /api/v1/datasets). " +
  "Every /api/v1 endpoint then also accepts dataset=<name>, naming which " +
  "one to answer from; omitted, requests go to the default dataset, and an " +
  "unknown name returns 404. A deployment serving a single dataset needs no " +
  "dataset parameter anywhere.";

const EXAMPLES: { label: string; code: string }[] = [
  {
    label: "Semantic search",
    code: "curl 'https://<host>/api/v1/search?keyword=oral%20and%20ocular%20dryness%20research&search_mode=semantic&limit=5'",
  },
  {
    label: "Filter by field",
    code: "curl 'https://<host>/api/v1/search?organism=Mus%20musculus&tissue=Brain&limit=20'",
  },
  {
    label: "Export a filtered set",
    code: "curl -o genoar.csv 'https://<host>/api/v1/export?organism=Homo%20sapiens'",
  },
  {
    label: "One sample",
    code: "curl 'https://<host>/api/v1/samples/GSM2692027'",
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

export default function ApiDocsPage() {
  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="flex-1 min-h-0 overflow-y-auto scroll-stable flex flex-col">
        <main className="flex-1 w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-8">
          {/* The sample page's breadcrumb, one level shallower. */}
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
            <span className="text-brand font-semibold">API</span>
          </nav>

          {/* The sample page's hero: the name, its standing, and what it is. */}
          <div className="flex flex-col lg:flex-row justify-between items-start lg:items-center gap-6 mb-8 pb-8 border-b border-edge">
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-3 flex-wrap">
                <h1 className="type-figure text-3xl">API</h1>
                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold bg-accent/12 text-accent border border-accent/25">
                  No key required
                </span>
              </div>
              <p className="text-sm text-ink-soft max-w-2xl">
                The website uses these endpoints. They are open to anyone and need
                no account or key. All paths are relative to this host.
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-6 mb-12">
            <SectionHead
              icon="terminal"
              title="Getting started"
              blurb="Example requests."
            />
            <div className={`${CARD_STILL} p-6 flex flex-col gap-4`}>
              {EXAMPLES.map(({ label, code }) => (
                <div key={label}>
                  <p className="text-xs font-semibold text-ink-soft mb-1.5">
                    {label}
                  </p>
                  <pre className="bg-sunken rounded-lg p-3 overflow-x-auto">
                    <code className="font-mono text-xs text-ink-body whitespace-pre">
                      {code}
                    </code>
                  </pre>
                </div>
              ))}
              <p className="text-sm text-ink-soft">
                Responses are JSON. The export endpoint returns CSV unless{" "}
                <code className={CODE}>format=json</code> is set, and the file
                endpoint returns the file itself.
              </p>
            </div>
          </div>

          {ENDPOINTS.map(({ group, icon, blurb, items }) => (
            <div key={group} className="flex flex-col gap-6 mb-12">
              <SectionHead icon={icon} title={group} blurb={blurb} />
              <div className={`${CARD_STILL} divide-y divide-edge`}>
                {items.map((e) => (
                  <div key={e.path} className="p-5">
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <span className="font-mono text-[11px] font-bold text-accent">
                        {e.method}
                      </span>
                      <code className="font-mono text-sm text-ink break-all">
                        {e.path}
                      </code>
                    </div>
                    <p className="text-sm text-ink-body mt-1.5 leading-relaxed">
                      {e.summary}
                    </p>
                    {e.params && (
                      <p className="text-xs text-ink-faint mt-1.5 leading-relaxed">
                        <span className="font-semibold">Query: </span>
                        <span className="font-mono">{e.params}</span>
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}

          <div className={`${CARD_STILL} p-5 mb-12`}>
            <p className="text-sm text-ink-body leading-relaxed">
              {DATASET_NOTE}
            </p>
          </div>

          <div className="flex flex-col gap-6 mb-16">
            <SectionHead
              icon="info"
              title="About the data"
              blurb="Where the data comes from."
            />
            <div className={`${CARD_STILL} p-6 flex flex-col gap-3`}>
              <p className="text-sm text-ink-body leading-relaxed">
                The curated metadata and the processed matrices are produced by
                GENOAR&apos;s pipeline and are served from this host. The raw
                reads remain at GEO and SRA where they were deposited, so the
                download endpoint returns a link to the archive record rather
                than the file.
              </p>
              <p className="text-sm text-ink-body leading-relaxed">
                Each source has an <code className={CODE}>action</code> field. A value
                of <span className="font-mono text-xs">download</span> means the
                file is served here;{" "}
                <span className="font-mono text-xs">visit</span> means the URL is
                a page on another site.
              </p>
              <p className="text-sm text-ink-body leading-relaxed">
                The download endpoint also reports the Cell Ranger version, the
                detected chemistry and the reference the matrix was aligned
                against. Those are read out of the matrix file itself, so they
                describe the file being offered rather than the pipeline in
                general.
              </p>
              <p className="text-sm text-ink-body leading-relaxed">
                Every record includes its accessions, which can be used to cite
                the studies the samples came from.
              </p>
            </div>
          </div>
        </main>
        <Footer />
      </div>
    </div>
  );
}
