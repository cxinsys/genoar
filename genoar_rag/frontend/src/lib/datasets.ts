/** Which body of data a page is showing.
 *
 * A deployment usually serves one, and then this is invisible: nothing appears
 * in a URL, no page says which corpus it is, and every screen describes the
 * same thing. A deployment can also serve several, and then the dashboard need
 * not describe the same data as the search page — which is a configuration,
 * not a second product.
 *
 * Two datasets are two databases, so neither is a subset of the other and a
 * sample listed on one page may simply not exist on the other. That is why a
 * click has to carry its answer: a card opened from the dashboard says which
 * dataset it came from, or the page it opens asks the wrong one.
 */

/** The dataset a request that names none is about. Decided by the server, not
 *  here: it is whichever the deployment configured first. */
export const NO_DATASET = undefined;

export type Dataset = string | undefined;

export interface DatasetInfo {
  name: string;
  label: string;
  is_default: boolean;
}

export interface DatasetsResponse {
  items: DatasetInfo[];
  default: string;
  /** Whether more than one body of data is served. Where a page decides
   *  whether naming its corpus carries information — with one, the name is on
   *  every page and says nothing. */
  is_split: boolean;
}

/** The dataset the dashboard describes.
 *
 * Empty means the same one the search page uses, which is the case unless a
 * deployment says otherwise. Read at build time rather than fetched, because a
 * page cannot ask which data it is about before it draws itself — and changing
 * it means changing what data is deployed, which is a deploy either way.
 */
export const DASHBOARD_DATASET: Dataset =
  process.env.NEXT_PUBLIC_DASHBOARD_DATASET || undefined;

/** Whether this needs saying in a URL.
 *
 * The default is left out, so a deployment serving one dataset produces the
 * addresses it always produced and nothing bookmarked changes meaning.
 */
export function isDefaultDataset(dataset: Dataset): boolean {
  return !dataset;
}

/** Read a dataset off a query string.
 *
 * Anything is accepted: which names exist is the server's to know, and it
 * answers 404 for one it does not serve. Validating here would mean the client
 * holding a second copy of the list and disagreeing with the server about it.
 */
export function parseDataset(value: string | null): Dataset {
  return value?.trim() || undefined;
}

/** The address of a sample page, in the dataset the reader is currently in.
 *
 * Every way into a sample has to agree on this. While there were two
 * implementations they did not: cards carried the dataset and the links inside
 * a sample page did not, so following a series sibling out of one dataset
 * landed in another, where that sample may not exist at all.
 */
export function sampleHref(runId: string, dataset: Dataset): string {
  return isDefaultDataset(dataset)
    ? `/sample/${runId}`
    : `/sample/${runId}?dataset=${encodeURIComponent(dataset!)}`;
}
