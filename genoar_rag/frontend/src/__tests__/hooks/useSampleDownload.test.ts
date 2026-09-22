import { renderHook, waitFor } from "@testing-library/react";
import { useSampleDownload } from "@/hooks/useSampleDownload";

jest.mock("@/lib/api-client", () => ({
  ...jest.requireActual("@/lib/api-client"),
  getSampleDownload: jest.fn(),
}));

import { getSampleDownload, ApiError } from "@/lib/api-client";
const mockGetDownload = getSampleDownload as jest.MockedFunction<typeof getSampleDownload>;

const mockResponse = {
  run_id: "SRR001",
  geo_accession: "GSM0001",
  requested_accession: "SRR001",
  requires_api_key: false,
  sources: [
    {
      kind: "geo_record",
      available: true,
      action: "visit",
      url: "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSM0001",
      size_bytes: null,
      description: "The GEO record this sample was deposited in.",
      note: null,
    },
    {
      kind: "processed_h5",
      available: false,
      action: "download",
      url: null,
      size_bytes: null,
      description: "Analysis-ready matrix.",
      note: "No processed-data URL configured.",
    },
  ],
};

describe("useSampleDownload", () => {
  beforeEach(() => {
    mockGetDownload.mockReset();
  });

  it("returns the sources once loaded", async () => {
    mockGetDownload.mockResolvedValue(mockResponse);
    const { result } = renderHook(() => useSampleDownload("SRR001"));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data?.sources).toHaveLength(2);
    expect(result.current.error).toBeNull();
  });

  it("passes sources through unfiltered, leaving the choice to the caller", async () => {
    // The page hides sources it cannot offer; the hook itself does not drop them.
    mockGetDownload.mockResolvedValue(mockResponse);
    const { result } = renderHook(() => useSampleDownload("SRR001"));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    const h5 = result.current.data?.sources.find((s) => s.kind === "processed_h5");
    expect(h5?.available).toBe(false);
  });

  it("surfaces errors", async () => {
    mockGetDownload.mockRejectedValue(new ApiError("boom", 500));
    const { result } = renderHook(() => useSampleDownload("SRR001"));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.data).toBeNull();
  });

  it("refetches when the accession changes", async () => {
    mockGetDownload.mockResolvedValue(mockResponse);
    const { result, rerender } = renderHook(({ id }) => useSampleDownload(id), {
      initialProps: { id: "SRR001" },
    });
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    rerender({ id: "GSM0002" });
    await waitFor(() => expect(mockGetDownload).toHaveBeenCalledTimes(2));
    // Arity left open: the call also carries the corpus the page is of, and
    // what this test is about is that a new accession refetches at all.
    expect(mockGetDownload.mock.calls.at(-1)?.[0]).toBe("GSM0002");
  });
});
