import { renderHook, waitFor } from "@testing-library/react";
import { useSimilarSamples } from "@/hooks/useSimilarSamples";

jest.mock("@/lib/api-client", () => ({
  ...jest.requireActual("@/lib/api-client"),
  getSimilarSamples: jest.fn(),
}));

import { getSimilarSamples, ApiError } from "@/lib/api-client";
const mockGetSimilar = getSimilarSamples as jest.MockedFunction<typeof getSimilarSamples>;

const mockResponse = {
  query_run_id: "SRR001",
  items: [
    {
      run_id: "SRR002",
      similarity_score: 0.95,
      tissue: "Blood",
      cell_type: null,
      disease: null,
      organism: "Homo sapiens",
      assay_type: "RNA-Seq",
    },
  ],
  total: 1,
};

describe("useSimilarSamples", () => {
  beforeEach(() => {
    mockGetSimilar.mockReset();
  });

  it("returns data on success", async () => {
    mockGetSimilar.mockResolvedValueOnce(mockResponse);
    const { result } = renderHook(() => useSimilarSamples("SRR001"));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.data).toEqual(mockResponse);
    expect(result.current.error).toBeNull();
    expect(result.current.isUnavailable).toBe(false);
  });

  it("starts with loading state", () => {
    mockGetSimilar.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useSimilarSamples("SRR001"));
    expect(result.current.isLoading).toBe(true);
  });

  it("sets isUnavailable on 503", async () => {
    mockGetSimilar.mockRejectedValueOnce(new ApiError("Unavailable", 503));
    const { result } = renderHook(() => useSimilarSamples("SRR001"));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isUnavailable).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("sets error on network failure", async () => {
    mockGetSimilar.mockRejectedValueOnce(new ApiError("Network error"));
    const { result } = renderHook(() => useSimilarSamples("SRR001"));

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.error).toBeInstanceOf(ApiError);
    expect(result.current.isUnavailable).toBe(false);
  });

  it("does not set error on abort", async () => {
    mockGetSimilar.mockRejectedValueOnce(
      new DOMException("The operation was aborted", "AbortError"),
    );
    const { result, unmount } = renderHook(() => useSimilarSamples("SRR001"));
    unmount();
    // After abort, error should remain null
    expect(result.current.error).toBeNull();
  });

  it("refetches when runId changes", async () => {
    mockGetSimilar.mockResolvedValue(mockResponse);
    const { rerender } = renderHook(
      ({ runId }) => useSimilarSamples(runId),
      { initialProps: { runId: "SRR001" } },
    );

    await waitFor(() => expect(mockGetSimilar).toHaveBeenCalledTimes(1));

    rerender({ runId: "SRR002" });
    await waitFor(() => expect(mockGetSimilar).toHaveBeenCalledTimes(2));
  });
});
