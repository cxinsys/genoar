/**
 * Example semantic queries supplied by the GENOAR authors, offered as one-click
 * starting points on the search page.
 *
 * The header's search field advertises one of these too, so they live here
 * rather than in the search page: the two would otherwise drift apart and the
 * header would suggest a query the search page never offers.
 */
export const EXAMPLE_QUERIES: Record<"human" | "mouse", string[]> = {
  human: [
    "oral and ocular dryness research",
    "idiopathic parkinson's disease (ipd) research",
  ],
  mouse: ["bone marrow lt-hsc profiling", "covid-19 samples"],
};

/**
 * The example shown when no species is chosen — the same one the search page
 * puts in its placeholder on first load.
 */
export const DEFAULT_EXAMPLE_QUERY = EXAMPLE_QUERIES.human[0];
