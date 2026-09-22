/** Emptying the filter search box.
 *
 * The sidebar's categories are searchable, and a search that matched nothing
 * left the category empty — deleting the text put nothing back, because the
 * hook only fetched for a non-empty query and there was no other path home.
 * The clear button worked, so the box could be recovered by pressing the ✕ and
 * not by pressing backspace, which is not a distinction anybody makes.
 *
 * The search page's dropdown had been patched at its own call site, routing an
 * emptied box through `clearSearch`. That patch is why only the dashboard's
 * list showed the fault, and why these tests are on the hook rather than on
 * either component.
 */

import { act, renderHook, waitFor } from "@testing-library/react";

import { useFilterCategory } from "@/hooks/useFilterCategory";

jest.mock("@/lib/api-client", () => ({
  ...jest.requireActual("@/lib/api-client"),
  getFilterCategory: jest.fn(),
}));

import { getFilterCategory } from "@/lib/api-client";

const mockGet = getFilterCategory as jest.MockedFunction<typeof getFilterCategory>;

const OPENING_PAGE = [
  { value: "Blood", count: 10 },
  { value: "Bone Marrow", count: 8 },
  { value: "Lung", count: 5 },
];

const options = {
  initialValues: OPENING_PAGE,
  initialTotal: OPENING_PAGE.length,
};

function pageOf(values: { value: string; count: number }[]) {
  return {
    name: "tissue",
    values,
    total_distinct: values.length,
    offset: 0,
    limit: 50,
    query: null,
  };
}

beforeEach(() => {
  jest.useFakeTimers();
  mockGet.mockReset();
});

afterEach(() => {
  jest.useRealTimers();
});

/** Type into the box without letting the debounce elapse. */
function type(result: ReturnType<typeof renderHook>["result"], text: string) {
  act(() => (result.current as ReturnType<typeof useFilterCategory>).setQuery(text));
}

/** Type into the box and let the debounce elapse. */
async function search(result: ReturnType<typeof renderHook>["result"], text: string) {
  act(() => (result.current as ReturnType<typeof useFilterCategory>).setQuery(text));
  await act(async () => {
    jest.advanceTimersByTime(500);
  });
}

describe("emptying the search box", () => {
  it("puts the opening list back after a search that matched nothing", async () => {
    // The exact shape of the report: search, get nothing, delete the text, and
    // the category is empty with no way back to its values.
    mockGet.mockResolvedValue(pageOf([]));
    const { result } = renderHook(() => useFilterCategory("tissue", options));

    await search(result, "zzz");
    expect(result.current.values).toEqual([]);

    await search(result, "");
    expect(result.current.values).toEqual(OPENING_PAGE);
    expect(result.current.total).toBe(OPENING_PAGE.length);
  });

  it("puts it back after a search that matched something", async () => {
    mockGet.mockResolvedValue(pageOf([{ value: "Blood", count: 10 }]));
    const { result } = renderHook(() => useFilterCategory("tissue", options));

    await search(result, "blo");
    expect(result.current.values).toHaveLength(1);

    await search(result, "");
    expect(result.current.values).toEqual(OPENING_PAGE);
  });

  it("does not ask the server for the whole list again", async () => {
    // It is already in hand — the parent supplied it — and a request here would
    // put a spinner on a keystroke that is meant to feel like an undo.
    mockGet.mockResolvedValue(pageOf([]));
    const { result } = renderHook(() => useFilterCategory("tissue", options));

    await search(result, "zzz");
    const afterSearching = mockGet.mock.calls.length;

    await search(result, "");
    expect(mockGet.mock.calls.length).toBe(afterSearching);
  });

  it("is the same whether the box is emptied or the button is pressed", async () => {
    mockGet.mockResolvedValue(pageOf([]));
    const { result } = renderHook(() => useFilterCategory("tissue", options));

    await search(result, "zzz");
    await act(async () => result.current.clearSearch());

    expect(result.current.query).toBe("");
    expect(result.current.values).toEqual(OPENING_PAGE);
  });
});

describe("a search still in flight when the box is emptied", () => {
  it("stops the spinner, so Show more is not left disabled", async () => {
    // `fetchPage` clears the flag only while its own request is the current
    // one, and emptying the box stops it being that. The flag stayed on a
    // request nobody was waiting for, and the button that fetches the next page
    // is disabled while it is set — so a category of more than fifty values
    // could not be paged through until the panel was reopened.
    mockGet.mockImplementation(() => new Promise(() => {}));
    const { result } = renderHook(() => useFilterCategory("tissue", options));

    await search(result, "blo");
    expect(result.current.isLoading).toBe(true);

    await search(result, "");
    expect(result.current.isLoading).toBe(false);
    expect(result.current.values).toEqual(OPENING_PAGE);
    expect(result.current.total).toBe(OPENING_PAGE.length);
  });

  it("does not leave the failed query's error behind", async () => {
    mockGet.mockRejectedValue(new Error("network"));
    const { result } = renderHook(() => useFilterCategory("tissue", options));

    await search(result, "blo");
    expect(result.current.error).not.toBeNull();

    await search(result, "");
    expect(result.current.error).toBeNull();
  });

  it("does not land on top of the restored list", async () => {
    // Slower than the delete that follows it. Without the abort its results
    // arrive afterwards and the box reads as empty while showing a filtered
    // list — the same wrong state, one step further along.
    let settle: (page: ReturnType<typeof pageOf>) => void = () => {};
    mockGet.mockImplementation(
      () => new Promise((resolve) => (settle = resolve)),
    );

    const { result } = renderHook(() => useFilterCategory("tissue", options));
    await search(result, "blo");
    await search(result, "");

    await act(async () => {
      settle(pageOf([{ value: "Blood", count: 10 }]));
    });

    await waitFor(() => expect(result.current.values).toEqual(OPENING_PAGE));
  });
});


describe("one search replacing another", () => {
  it("shows the second query's results and not the first's", async () => {
    // Typing "b" then "bl" starts one request and, 250ms later, a second. If
    // the first resolves in between it is still the current request as far as
    // `fetchPage` knows, and its results appear under a box that says "bl".
    const settles: ((page: ReturnType<typeof pageOf>) => void)[] = [];
    mockGet.mockImplementation(
      () => new Promise((resolve) => settles.push(resolve)),
    );

    const { result } = renderHook(() => useFilterCategory("tissue", options));
    await search(result, "b");
    await search(result, "bl");

    // The first query answers late, after it has been replaced. Its value is
    // one the opening list does not hold, so seeing it could only mean this
    // response landed.
    const STALE = { value: "Stale answer to b", count: 99 };
    await act(async () => {
      settles[0](pageOf([STALE]));
    });
    expect(result.current.values).not.toContainEqual(STALE);

    await act(async () => {
      settles[1](pageOf([{ value: "Blood", count: 10 }]));
    });
    await waitFor(() =>
      expect(result.current.values).toEqual([{ value: "Blood", count: 10 }]),
    );
  });
});


describe("a replaced request failing late", () => {
  it("does not put its error over the search that replaced it", async () => {
    // An abort rejects with `AbortError` and is ignored. This is the other
    // rejection: a request that had already failed before it was replaced,
    // arriving with a real error to report about a query nobody is running.
    const rejects: ((err: Error) => void)[] = [];
    mockGet.mockImplementation(
      () => new Promise((_resolve, reject) => rejects.push(reject)),
    );

    const { result } = renderHook(() => useFilterCategory("tissue", options));
    await search(result, "b");
    await search(result, "bl");

    await act(async () => {
      rejects[0](new Error("stale network failure"));
    });

    expect(result.current.error).toBeNull();
  });

  it("still reports a failure of the search that is running", async () => {
    // The check is which request failed, not whether to report failures.
    mockGet.mockRejectedValue(new Error("network"));
    const { result } = renderHook(() => useFilterCategory("tissue", options));

    await search(result, "blo");
    expect(result.current.error).not.toBeNull();
  });
});

describe("the quarter second before a search is sent", () => {
  it("counts as loading, so Show more cannot act on the old list", async () => {
    // `Show more` is disabled while loading, and during the debounce the list
    // on screen still answers the previous query while `hasMore` is true of
    // it. Pressing it there asked for page two of a list about to be replaced.
    mockGet.mockResolvedValue(pageOf([{ value: "Blood", count: 10 }]));
    const { result } = renderHook(() => useFilterCategory("tissue", options));

    type(result, "blo");
    expect(result.current.isLoading).toBe(true);
  });

  it("so a press in that window asks for nothing", async () => {
    mockGet.mockResolvedValue(pageOf([{ value: "Blood", count: 10 }]));
    const { result } = renderHook(() =>
      useFilterCategory("tissue", { ...options, initialTotal: 500 }),
    );

    type(result, "blo");
    act(() => result.current.loadMore());

    // Not page two of the previous query, which is what an enabled button in
    // this window would have fetched.
    expect(mockGet).not.toHaveBeenCalled();
  });
});
