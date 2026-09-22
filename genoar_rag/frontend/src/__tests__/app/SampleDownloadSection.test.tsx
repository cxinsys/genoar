/* What the sample page's download list does with a source it cannot offer.
 *
 * The section is drawn by the page rather than exported on its own, so the page
 * is what these render, with its four hooks stood in for. The download response
 * is the whole subject here; the sample detail only has to exist, because the
 * section is inside the branch that waits for it. */
import React from "react";
import { render, screen, within } from "@testing-library/react";
import "@testing-library/jest-dom";
import SampleDetailPage from "@/app/sample/[id]/page";
import type { SampleDetail, SampleDownloadResponse } from "@/types/api";

jest.mock("next/link", () => {
  return function MockLink({
    children,
    href,
  }: {
    children: React.ReactNode;
    href: string;
  }) {
    return <a href={href}>{children}</a>;
  };
});

jest.mock("next/navigation", () => ({
  useParams: () => ({ id: "SRR001" }),
  useSearchParams: () => new URLSearchParams(),
}));

jest.mock("@/hooks/useSeriesSamples", () => ({
  useSeriesSamples: () => ({ data: null, isLoading: false, error: null }),
}));

jest.mock("@/hooks/useSimilarSamples", () => ({
  useSimilarSamples: () => ({
    data: null,
    isLoading: false,
    error: null,
    isUnavailable: false,
  }),
}));

jest.mock("@/hooks/useSampleDetail", () => ({
  useSampleDetail: jest.fn(),
}));

jest.mock("@/hooks/useSampleDownload", () => ({
  useSampleDownload: jest.fn(),
}));

import { useSampleDetail } from "@/hooks/useSampleDetail";
import { useSampleDownload } from "@/hooks/useSampleDownload";

const mockDetail = useSampleDetail as jest.MockedFunction<
  typeof useSampleDetail
>;
const mockDownload = useSampleDownload as jest.MockedFunction<
  typeof useSampleDownload
>;

const sample: SampleDetail = {
  run_id: "SRR001",
  series: "GSE0001",
  all_series: ["GSE0001"],
  biosample: null,
  tissue: "lung",
  cell_type: null,
  disease: null,
  disease_category: null,
  organism: "Homo sapiens",
  assay_type: "RNA-Seq",
  library_source: null,
  platform: null,
  instrument: null,
  similarity_score: null,
  sample_name: null,
  treatment: null,
  sex: null,
  age: null,
  strain: null,
  genotype: null,
  size: null,
  str_tis: null,
  str_dis: null,
  str_cell: null,
  cui_tis: null,
  cui_dis: null,
  cui_cell: null,
  extended_fields: [],
};

/** The archive link every response carries, and the one source in these tests
    that is nobody's decision to withhold. */
const geoRecord = {
  kind: "geo_record",
  available: true,
  action: "visit",
  url: "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM0001",
  size_bytes: null,
  description: "The GEO record this sample was deposited in (GSM0001).",
  note: null,
};

/* The service's own words, from
   backend/app/services/download_service.py::ADOPTED_NOTE. Copied rather than
   paraphrased, because the length and the closing sentence are what the row
   has to make room for. */
const ADOPTED_NOTE =
  "This output was adopted on an operator's instruction " +
  "(GENOAR_ADOPT_PRIOR_RESULTS=1). No pipeline run has tied it to this " +
  "sample's input, and the pipeline counts it towards nothing. GENOAR hands " +
  "out only what the pipeline can account for, so the file is named here and " +
  "withheld. Re-running stage 3 over the sample replaces the adoption with " +
  "verified output.";

function response(
  sources: SampleDownloadResponse["sources"],
): SampleDownloadResponse {
  return {
    run_id: "SRR001",
    geo_accession: "GSM0001",
    requested_accession: "SRR001",
    requires_api_key: false,
    sources,
  };
}

function renderPage(data: SampleDownloadResponse) {
  mockDetail.mockReturnValue({
    sample,
    isLoading: false,
    error: null,
    isNotFound: false,
  });
  mockDownload.mockReturnValue({ data, isLoading: false, error: null });
  return render(<SampleDetailPage />);
}

describe("the download list", () => {
  beforeEach(() => {
    mockDetail.mockReset();
    mockDownload.mockReset();
  });

  describe("a processed sample GENOAR will not vouch for", () => {
    /* What the service sends when a run's output was adopted rather than
       produced: named, unavailable, no URL, and carrying the provenance that
       tells this apart from a sample nobody ever processed. */
    const withheld = response([
      {
        kind: "processed_h5",
        available: false,
        action: "download",
        url: null,
        size_bytes: null,
        description: "Counts per gene for the barcodes called as cells.",
        note: ADOPTED_NOTE,
        provenance: "adopted",
      },
      geoRecord,
    ]);

    it("shows the row rather than dropping it", () => {
      renderPage(withheld);
      expect(screen.getByText("Preprocessed matrix (*.h5)")).toBeInTheDocument();
    });

    it("gives the reason, ending in what to do about it", () => {
      renderPage(withheld);
      expect(screen.getByText(ADOPTED_NOTE)).toBeInTheDocument();
    });

    it("still says what is in the file", () => {
      // The note explains the withholding; it does not replace the description.
      renderPage(withheld);
      expect(
        screen.getByText("Counts per gene for the barcodes called as cells."),
      ).toBeInTheDocument();
    });

    it("offers nothing to click", () => {
      renderPage(withheld);
      const mark = screen.getByTestId("withheld-processed_h5");
      // Not the anchor the other two forms render, and not a control of any
      // other kind: a row with nothing behind it must not invite the click.
      expect(mark.tagName).toBe("SPAN");
      expect(mark).not.toHaveAttribute("href");
      expect(screen.queryByTestId("download-processed_h5")).toBeNull();

      const row = mark.parentElement as HTMLElement;
      expect(within(row).queryByRole("link")).toBeNull();
      expect(within(row).queryByRole("button")).toBeNull();
    });

    it("leaves the archive link beside it working", () => {
      // Withholding one source says nothing about the others.
      renderPage(withheld);
      expect(screen.getByTestId("download-geo_record")).toHaveAttribute(
        "href",
        geoRecord.url,
      );
    });

    it("reads the source and not its kind", () => {
      // The same shape under a kind this page has no label for still shows,
      // so a future withheld source needs no change here.
      renderPage(
        response([
          {
            kind: "spatial_h5",
            available: false,
            action: "download",
            url: null,
            size_bytes: null,
            description: "Something the pipeline made.",
            note: "Withheld, and here is why.",
            provenance: "unreadable",
          },
        ]),
      );
      expect(screen.getByTestId("withheld-spatial_h5")).toBeInTheDocument();
      expect(screen.getByText("Withheld, and here is why.")).toBeInTheDocument();
    });
  });

  describe("a sample that was offered its output", () => {
    it("is unchanged: a download link with its size", () => {
      renderPage(
        response([
          {
            kind: "processed_h5",
            available: true,
            action: "download",
            url: "/api/v1/samples/SRR001/files/filtered_feature_bc_matrix.h5",
            size_bytes: 52428800,
            description: "Counts per gene for the barcodes called as cells.",
            note: null,
            provenance: "verified",
          },
          geoRecord,
        ]),
      );
      const link = screen.getByTestId("download-processed_h5");
      expect(link).toHaveAttribute(
        "href",
        "/api/v1/samples/SRR001/files/filtered_feature_bc_matrix.h5",
      );
      expect(within(link).getByText("Download")).toBeInTheDocument();
      expect(screen.getByText("50 MB")).toBeInTheDocument();
      expect(screen.queryByTestId("withheld-processed_h5")).toBeNull();
    });

    it("says nothing about provenance on a row it can hand over", () => {
      // Verified output earns no badge. The download button is the claim.
      renderPage(
        response([
          {
            kind: "processed_h5",
            available: true,
            action: "download",
            url: "/api/v1/samples/SRR001/files/filtered_feature_bc_matrix.h5",
            size_bytes: 1024,
            description: "Counts per gene for the barcodes called as cells.",
            note: null,
            provenance: "verified",
          },
        ]),
      );
      expect(screen.queryByText(/withheld/i)).toBeNull();
    });
  });

  describe("a sample that was never processed", () => {
    it("shows no row for the matrix it does not have", () => {
      // No provenance on the source, because there was no output on disk to
      // read a receipt beside. Nothing is being kept from the reader, so there
      // is nothing to tell them.
      renderPage(
        response([
          {
            kind: "processed_h5",
            available: false,
            action: "download",
            url: null,
            size_bytes: null,
            description: "Counts per gene for the barcodes called as cells.",
            note: "This run has not been processed by the pipeline.",
            provenance: null,
          },
          geoRecord,
        ]),
      );
      expect(screen.queryByText("Preprocessed matrix (*.h5)")).toBeNull();
      expect(screen.queryByTestId("withheld-processed_h5")).toBeNull();
      expect(
        screen.queryByText("This run has not been processed by the pipeline."),
      ).toBeNull();
      // The metadata row and the archive record are all that is left.
      expect(screen.getByTestId("download-metadata_csv")).toBeInTheDocument();
      expect(screen.getByTestId("download-geo_record")).toBeInTheDocument();
    });

    it("shows no row when the deployment has nowhere to serve from", () => {
      // A misconfigured deployment carries no provenance either, and it is not
      // the reader's to act on. Same silence.
      renderPage(
        response([
          {
            kind: "processed_h5",
            available: false,
            action: "download",
            url: null,
            size_bytes: null,
            description: "Counts per gene for the barcodes called as cells.",
            note: "This deployment has no processed-data location configured.",
            provenance: null,
          },
        ]),
      );
      expect(screen.queryByText("Preprocessed matrix (*.h5)")).toBeNull();
    });
  });
});
