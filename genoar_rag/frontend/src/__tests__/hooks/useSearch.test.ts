import { renderHook, waitFor, act } from "@testing-library/react";
import { useSearch } from "@/hooks/useSearch";

// Mock next/navigation
let mockSearchParams = new URLSearchParams();
const mockReplace = jest.fn();
const mockPush = jest.fn();

jest.mock("next/navigation", () => ({
  useSearchParams: () => mockSearchParams,
  useRouter: () => ({ replace: mockReplace, push: mockPush }),
}));

jest.mock("@/lib/api-client", () => ({
  ...jest.requireActual("@/lib/api-client"),
  searchSamples: jest.fn(),
}));

import { searchSamples, ApiError } from "@/lib/api-client";
const mockSearch = searchSamples as jest.MockedFunction<typeof searchSamples>;

const mockResponse = {
  items: [],
  total: 0,
  offset: 0,
  limit: 20,
  filters_applied: {},
};

describe("useSearch – search_mode", () => {
  beforeEach(() => {
    mockSearchParams = new URLSearchParams();
    mockReplace.mockReset();
    mockPush.mockReset();
    mockSearch.mockReset();
    mockSearch.mockResolvedValue(mockResponse);
  });

  it("parses search_mode=semantic from URL", async () => {
    mockSearchParams = new URLSearchParams("search_mode=semantic");
    const { result } = renderHook(() => useSearch());

    expect(result.current.filters.search_mode).toBe("semantic");
  });

  it("defaults to no search_mode when absent (sql default)", async () => {
    mockSearchParams = new URLSearchParams("");
    const { result } = renderHook(() => useSearch());

    expect(result.current.filters.search_mode).toBeUndefined();
  });

  it("setSearchMode('semantic') updates URL", async () => {
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.setSearchMode("semantic"));

    expect(mockPush).toHaveBeenCalledWith(
      expect.stringContaining("search_mode=semantic"),
      expect.any(Object),
    );
  });

  it("setSearchMode('sql') removes search_mode from URL", async () => {
    mockSearchParams = new URLSearchParams("search_mode=semantic");
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.setSearchMode("sql"));

    const calledUrl = mockPush.mock.calls.at(-1)?.[0] as string;
    expect(calledUrl).not.toContain("search_mode");
  });

  it("setSearchMode resets offset to 0", async () => {
    mockSearchParams = new URLSearchParams("offset=40");
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.setSearchMode("semantic"));

    const calledUrl = mockPush.mock.calls.at(-1)?.[0] as string;
    expect(calledUrl).not.toContain("offset");
  });

  it("setSearchMode('sql') resets similarity_score sort", async () => {
    mockSearchParams = new URLSearchParams(
      "search_mode=semantic&sort_by=similarity_score&sort_order=desc",
    );
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.setSearchMode("sql"));

    const calledUrl = mockPush.mock.calls.at(-1)?.[0] as string;
    expect(calledUrl).not.toContain("sort_by");
    expect(calledUrl).not.toContain("similarity_score");
  });

  it("sets isSemanticUnavailable on 503", async () => {
    mockSearch.mockRejectedValueOnce(new ApiError("Service Unavailable", 503));
    const { result } = renderHook(() => useSearch());

    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.isSemanticUnavailable).toBe(true);
    expect(result.current.error).toBeNull();
  });

  it("resets isSemanticUnavailable on successful retry", async () => {
    mockSearch.mockRejectedValueOnce(new ApiError("Service Unavailable", 503));
    const { result, rerender } = renderHook(() => useSearch());

    await waitFor(() => expect(result.current.isSemanticUnavailable).toBe(true));

    mockSearch.mockResolvedValueOnce(mockResponse);
    // Trigger re-fetch by re-rendering with new search params
    mockSearchParams = new URLSearchParams("keyword=retry");
    rerender();

    await waitFor(() => expect(result.current.isSemanticUnavailable).toBe(false));
  });
});

describe("useSearch – browser history", () => {
  beforeEach(() => {
    mockSearchParams = new URLSearchParams();
    mockReplace.mockReset();
    mockPush.mockReset();
    mockSearch.mockReset();
    mockSearch.mockResolvedValue(mockResponse);
  });

  // Every one of these is a step the user took, so Back has to be able to undo it.
  // Replacing the entry instead would send Back to whatever page came before the
  // search, skipping the searches performed along the way.
  it.each([
    ["setFilter", (r: ReturnType<typeof useSearch>) => r.setFilter("organism", ["Mus musculus"])],
    ["removeFilter", (r: ReturnType<typeof useSearch>) => r.removeFilter("organism", "Mus musculus")],
    ["clearFilters", (r: ReturnType<typeof useSearch>) => r.clearFilters()],
    ["setKeyword", (r: ReturnType<typeof useSearch>) => r.setKeyword("lung")],
    ["setPage", (r: ReturnType<typeof useSearch>) => r.setPage(3)],
    ["setSort", (r: ReturnType<typeof useSearch>) => r.setSort("run_id", "desc")],
    ["setSearchMode", (r: ReturnType<typeof useSearch>) => r.setSearchMode("semantic")],
  ])("%s pushes a history entry", async (_name, action) => {
    mockSearchParams = new URLSearchParams("organism=Mus+musculus");
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => action(result.current));

    expect(mockPush).toHaveBeenCalledTimes(1);
    expect(mockReplace).not.toHaveBeenCalled();
  });

  it("replaces when asked to, for rewrites the user did not request", async () => {
    // The legacy ?q= migration passes "replace" so Back does not land on a URL
    // that immediately rewrites itself.
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.setKeyword("lung", "replace"));

    expect(mockReplace).toHaveBeenCalledTimes(1);
    expect(mockPush).not.toHaveBeenCalled();
  });

  it("keeps successive filter changes as separate entries", async () => {
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.setFilter("organism", ["Homo sapiens"]));
    act(() => result.current.setFilter("tissue", ["Lung"]));

    expect(mockPush).toHaveBeenCalledTimes(2);
  });
});

describe("useSearch – result count", () => {
  beforeEach(() => {
    mockSearchParams = new URLSearchParams();
    mockReplace.mockReset();
    mockPush.mockReset();
    mockSearch.mockReset();
    mockSearch.mockResolvedValue(mockResponse);
  });

  it("reads top_k from the URL", async () => {
    mockSearchParams = new URLSearchParams("top_k=100");
    const { result } = renderHook(() => useSearch());
    expect(result.current.filters.top_k).toBe(100);
  });

  // Without this the server applies its own, much larger pool and the summary
  // reports a count the controls never asked for.
  it("requests the default count when the URL omits it", async () => {
    mockSearchParams = new URLSearchParams("search_mode=semantic");
    const { result } = renderHook(() => useSearch());
    expect(result.current.filters.top_k).toBe(20);
  });

  it("leaves top_k off keyword searches, which count exactly", async () => {
    mockSearchParams = new URLSearchParams("");
    const { result } = renderHook(() => useSearch());
    expect(result.current.filters.top_k).toBeUndefined();
  });

  // The page is sized to the request so the whole set arrives at once — the map
  // draws what it is given, and a slice would misrepresent the result set.
  it("setTopK asks for that many results in one page", async () => {
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.setTopK(50));

    const calledUrl = mockPush.mock.calls.at(-1)?.[0] as string;
    expect(calledUrl).toContain("top_k=50");
    expect(calledUrl).toContain("limit=50");
  });

  it("setTopK returns to the first page", async () => {
    mockSearchParams = new URLSearchParams("offset=40");
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.setTopK(100));

    const calledUrl = mockPush.mock.calls.at(-1)?.[0] as string;
    expect(calledUrl).not.toContain("offset");
  });

  it("leaves the default count out of the URL", async () => {
    mockSearchParams = new URLSearchParams("top_k=100&limit=100");
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.setTopK(20));

    const calledUrl = mockPush.mock.calls.at(-1)?.[0] as string;
    expect(calledUrl).not.toContain("top_k");
    expect(calledUrl).not.toContain("limit");
  });

  // Keyword search counts exactly, so the vector cutoff has no meaning there and
  // must not leave the page sized to whatever the semantic set was.
  it("switching to keyword search drops top_k and the page size", async () => {
    mockSearchParams = new URLSearchParams("search_mode=semantic&top_k=100&limit=100");
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.setSearchMode("sql"));

    const calledUrl = mockPush.mock.calls.at(-1)?.[0] as string;
    expect(calledUrl).not.toContain("top_k");
    expect(calledUrl).not.toContain("limit");
  });
});

// Keyword mode narrows by filter alone and offers no query field, so a keyword
// that reached the URL another way — the header, a mode switch, an old bookmark
// — would AND itself against every filter and empty the results, with nothing in
// the UI able to clear it. It belongs to the vector modes only.
describe("useSearch – the keyword belongs to the vector modes", () => {
  beforeEach(() => {
    mockSearchParams = new URLSearchParams();
    mockReplace.mockReset();
    mockPush.mockReset();
    mockSearch.mockReset();
    mockSearch.mockResolvedValue(mockResponse);
  });

  it("ignores a keyword the URL carries without a vector mode", async () => {
    mockSearchParams = new URLSearchParams("keyword=oral+dryness+research");
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.filters.keyword).toBeUndefined();
  });

  it("keeps the keyword in semantic mode", async () => {
    mockSearchParams = new URLSearchParams("keyword=oral+dryness&search_mode=semantic");
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.filters.keyword).toBe("oral dryness");
  });

  it("keeps the keyword in hybrid mode", async () => {
    mockSearchParams = new URLSearchParams("keyword=oral+dryness&search_mode=hybrid");
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    expect(result.current.filters.keyword).toBe("oral dryness");
  });

  it("switching to keyword search drops the query", async () => {
    mockSearchParams = new URLSearchParams("search_mode=semantic&keyword=oral+dryness");
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.setSearchMode("sql"));

    const calledUrl = mockPush.mock.calls.at(-1)?.[0] as string;
    expect(calledUrl).not.toContain("keyword");
  });

  // The stale keyword is ignored on arrival; touching anything also writes it out
  // of the address, so what the URL says and what the page did agree from then on.
  it("cleans a stale keyword out of the URL on the next filter change", async () => {
    mockSearchParams = new URLSearchParams("keyword=oral+dryness");
    const { result } = renderHook(() => useSearch());
    await waitFor(() => expect(result.current.isLoading).toBe(false));

    act(() => result.current.setFilter("tissue", ["brain"]));

    const calledUrl = mockPush.mock.calls.at(-1)?.[0] as string;
    expect(calledUrl).toContain("tissue=brain");
    expect(calledUrl).not.toContain("keyword");
  });

  it("does not send an ignored keyword to the API", async () => {
    mockSearchParams = new URLSearchParams("keyword=oral+dryness");
    renderHook(() => useSearch());

    await waitFor(() => expect(mockSearch).toHaveBeenCalled());
    expect(mockSearch.mock.calls[0][0].keyword).toBeUndefined();
  });
});
