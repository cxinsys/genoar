import {
  fetchApi,
  buildSearchParams,
  buildExportUrl,
  getSimilarSamples,
  ApiError,
  NotFoundError,
  ValidationError,
} from "@/lib/api-client";

// Mock global fetch
const mockFetch = jest.fn();
global.fetch = mockFetch;

describe("buildExportUrl", () => {
  it("targets the export endpoint and defaults to CSV", () => {
    const url = buildExportUrl({});
    expect(url.startsWith("/api/v1/export?")).toBe(true);
    expect(url).toContain("format=csv");
  });

  it("carries the active filters", () => {
    const url = buildExportUrl({ organism: ["Mus musculus"], keyword: "bone marrow" });
    expect(url).toContain("organism=Mus+musculus");
    expect(url).toContain("keyword=bone+marrow");
  });

  it("drops pagination so the whole filtered set is exported", () => {
    const url = buildExportUrl({ offset: 40, limit: 20 });
    expect(url).not.toContain("offset=");
    expect(url).not.toContain("limit=");
  });

  it("keeps search_mode so the file is searched the way the page was", () => {
    // Dropping it while keeping the keyword sent the AI Librarian's question to
    // the SQL matcher, and a page showing twenty results downloaded zero.
    const url = buildExportUrl({ search_mode: "semantic", keyword: "x" });
    expect(url).toContain("search_mode=semantic");
    expect(url).toContain("keyword=x");
  });

  it("keeps top_k so the file covers the candidate set the page counted", () => {
    const url = buildExportUrl({ search_mode: "semantic", keyword: "x", top_k: 50 });
    expect(url).toContain("top_k=50");
  });

  it("supports JSON", () => {
    expect(buildExportUrl({}, "json")).toContain("format=json");
  });
});

describe("buildSearchParams", () => {
  it("builds multi-value params", () => {
    const qs = buildSearchParams({
      tissue: ["Blood", "Brain"],
      organism: ["Homo sapiens"],
    });
    expect(qs).toContain("tissue=Blood");
    expect(qs).toContain("tissue=Brain");
    expect(qs).toContain("organism=Homo+sapiens");
  });

  it("skips null/undefined values", () => {
    const qs = buildSearchParams({
      keyword: undefined,
      tissue: [],
    });
    expect(qs).toBe("");
  });

  it("includes keyword and pagination", () => {
    const qs = buildSearchParams({
      keyword: "lung cancer",
      offset: 20,
      limit: 10,
    });
    expect(qs).toContain("keyword=lung+cancer");
    expect(qs).toContain("offset=20");
    expect(qs).toContain("limit=10");
  });

  it("includes sort params", () => {
    const qs = buildSearchParams({
      sort_by: "run_id",
      sort_order: "desc",
    });
    expect(qs).toContain("sort_by=run_id");
    expect(qs).toContain("sort_order=desc");
  });

  it("includes search_mode=semantic in params", () => {
    const qs = buildSearchParams({ search_mode: "semantic" });
    expect(qs).toContain("search_mode=semantic");
  });

  it("omits search_mode when sql (default)", () => {
    const qs = buildSearchParams({ search_mode: "sql" });
    expect(qs).not.toContain("search_mode");
  });

  it("omits search_mode when not specified", () => {
    const qs = buildSearchParams({ keyword: "lung" });
    expect(qs).not.toContain("search_mode");
  });
});

describe("fetchApi", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("returns parsed JSON on success", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: "ok" }),
    });
    const result = await fetchApi("/health");
    expect(result).toEqual({ status: "ok" });
  });

  it("throws NotFoundError on 404", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: "Not Found",
      json: async () => ({ detail: "Sample not found" }),
    });
    await expect(fetchApi("/api/v1/samples/INVALID")).rejects.toThrow(NotFoundError);
  });

  it("throws ValidationError on 422", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 422,
      statusText: "Unprocessable Entity",
      json: async () => ({ detail: [{ msg: "invalid" }] }),
    });
    await expect(fetchApi("/api/v1/search?limit=-1")).rejects.toThrow(ValidationError);
  });

  it("throws ApiError on other errors", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: async () => ({}),
    });
    await expect(fetchApi("/api/v1/stats")).rejects.toThrow(ApiError);
  });

  it("throws ApiError on network failure", async () => {
    mockFetch.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    await expect(fetchApi("/health")).rejects.toThrow(ApiError);
  });

  it("rethrows AbortError without wrapping", async () => {
    const abortError = new DOMException("The operation was aborted", "AbortError");
    mockFetch.mockRejectedValueOnce(abortError);
    await expect(fetchApi("/health")).rejects.toThrow(DOMException);
  });
});

describe("getSimilarSamples", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("calls correct URL with limit", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ query_run_id: "SRR001", items: [], total: 0 }),
    });
    await getSimilarSamples("SRR001", 5);
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/samples/SRR001/similar?limit=5"),
      expect.any(Object),
    );
  });

  it("calls URL without limit when omitted", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ query_run_id: "SRR001", items: [], total: 0 }),
    });
    await getSimilarSamples("SRR001");
    const calledUrl = mockFetch.mock.calls[0][0] as string;
    expect(calledUrl).not.toContain("limit");
  });

  it("encodes special characters in runId", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ query_run_id: "SRR/001", items: [], total: 0 }),
    });
    await getSimilarSamples("SRR/001");
    expect(mockFetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/v1/samples/SRR%2F001/similar"),
      expect.any(Object),
    );
  });
});
