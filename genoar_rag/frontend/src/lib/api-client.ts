import type {
  DashboardStats,
  FilterCategoryPage,
  FilterOptionsResponse,
  HealthResponse,
  PaginatedResponse,
  SampleDetail,
  SampleDownloadResponse,
  SampleSummary,
  SearchFilters,
  SearchResponse,
  SimilarSamplesResponse,
} from "@/types/api";
import { beginRequest, endRequest } from "@/lib/inflight";
import {
  isDefaultDataset,
  type Dataset,
  type DatasetsResponse,
} from "@/lib/datasets";

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

export class ApiError extends Error {
  constructor(
    message: string,
    public status?: number,
    public detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export class NotFoundError extends ApiError {
  constructor(message = "Resource not found") {
    super(message, 404);
    this.name = "NotFoundError";
  }
}

export class ValidationError extends ApiError {
  constructor(message = "Validation error", detail?: unknown) {
    super(message, 422, detail);
    this.name = "ValidationError";
  }
}

export async function fetchApi<T>(
  path: string,
  signal?: AbortSignal,
): Promise<T> {
  // The one place the app talks to the server, so the one place worth counting
  // from: the global indicator learns about a request nobody told it about, and
  // a hook that forgets to clear its own isLoading cannot leave it stuck on.
  // Wrapped in try/finally rather than counted down at each exit, because this
  // function has five of them and an abort leaves by the same door as an error.
  beginRequest();
  try {
    return await request<T>(path, signal);
  } finally {
    endRequest();
  }
}

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, { signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw err;
    }
    throw new ApiError("Network error: Unable to connect to server");
  }

  if (!res.ok) {
    let detail: unknown;
    try {
      detail = await res.json();
    } catch {
      // ignore parse failure
    }

    if (res.status === 404) {
      throw new NotFoundError(
        typeof detail === "object" && detail && "detail" in detail
          ? String((detail as { detail: unknown }).detail)
          : "Resource not found",
      );
    }
    if (res.status === 422) {
      throw new ValidationError("Validation error", detail);
    }
    throw new ApiError(
      `API error: ${res.status} ${res.statusText}`,
      res.status,
      detail,
    );
  }

  return res.json() as Promise<T>;
}

/** Append the dataset to a query, unless it is the one a silent request means.
 *
 * Leaving the default out is why a deployment serving one dataset produces the
 * addresses it always produced, and nothing bookmarked changes meaning.
 */
function withDataset(qs: URLSearchParams, dataset?: Dataset): string {
  if (!isDefaultDataset(dataset)) qs.set("dataset", dataset!);
  return qs.toString();
}

/** The same, for an endpoint whose path carries everything else. */
function datasetQuery(dataset?: Dataset): string {
  const qs = withDataset(new URLSearchParams(), dataset);
  return qs ? `?${qs}` : "";
}

export function buildSearchParams(filters: SearchFilters): string {
  const params = new URLSearchParams();

  const arrayFields: (keyof SearchFilters)[] = [
    "organism",
    "tissue",
    "cell_type",
    "assay_type",
    "library_source",
    "disease",
    "platform",
  ];

  for (const field of arrayFields) {
    const values = filters[field];
    if (Array.isArray(values)) {
      for (const v of values) {
        if (v) params.append(field, v);
      }
    }
  }

  if (filters.keyword) params.set("keyword", filters.keyword);
  if (filters.series) params.set("series", filters.series);
  if (filters.offset != null) params.set("offset", String(filters.offset));
  if (filters.limit != null) params.set("limit", String(filters.limit));
  if (filters.top_k != null) params.set("top_k", String(filters.top_k));
  if (filters.sort_by) params.set("sort_by", filters.sort_by);
  if (filters.sort_order) params.set("sort_order", filters.sort_order);
  if (filters.search_mode && filters.search_mode !== "sql") {
    params.set("search_mode", filters.search_mode);
  }

  return withDataset(params, filters.dataset);
}

/** URL for exporting the current filter set's metadata.
 *
 * The browser navigates to this directly so the file streams from the backend
 * without being buffered through JS. Pagination is dropped: an export covers the
 * whole filtered set rather than the page on screen.
 *
 * `search_mode` is kept, and that is the point. Drop it while leaving the
 * natural-language keyword in and the export runs the AI Librarian's question as
 * a SQL LIKE: a screen showing twenty Parkinson's samples downloads a file with
 * none, under a card saying it holds those twenty. The mode has to travel with
 * the keyword for the file to be the thing on screen.
 */
export function buildExportUrl(
  filters: SearchFilters,
  format: "csv" | "json" = "csv",
): string {
  const params = new URLSearchParams(buildSearchParams(filters));
  params.delete("offset");
  params.delete("limit");
  params.set("format", format);
  return `/api/v1/export?${params.toString()}`;
}

/** URL for exporting one sample's metadata, by run id or GEO sample id. */
export function buildSampleExportUrl(
  accession: string,
  format: "csv" | "json" = "csv",
  dataset?: Dataset,
): string {
  const params = new URLSearchParams({ accession, format });
  return `/api/v1/export?${withDataset(params, dataset)}`;
}

/** What this deployment serves: its datasets, their labels, and whether there
 *  is more than one. Asked for rather than compiled in, so a client cannot
 *  disagree with the server about which bodies of data exist. */
export function getDatasets(signal?: AbortSignal): Promise<DatasetsResponse> {
  return fetchApi<DatasetsResponse>("/api/v1/datasets", signal);
}

export function getStats(
  signal?: AbortSignal,
  dataset?: Dataset,
): Promise<DashboardStats> {
  return fetchApi<DashboardStats>(
    `/api/v1/stats${datasetQuery(dataset)}`,
    signal,
  );
}

export function getFilters(
  signal?: AbortSignal,
  dataset?: Dataset,
): Promise<FilterOptionsResponse> {
  return fetchApi<FilterOptionsResponse>(
    `/api/v1/filters${datasetQuery(dataset)}`,
    signal,
  );
}

/**
 * A page of one category's values. `/api/v1/filters` returns only the head of
 * each category — tissue alone has thousands — so this is how the rest is
 * reached, either by paging or by searching.
 */
export function getFilterCategory(
  category: string,
  params: {
    offset?: number;
    limit?: number;
    q?: string;
    dataset?: Dataset;
  } = {},
  signal?: AbortSignal,
): Promise<FilterCategoryPage> {
  const qs = new URLSearchParams();
  if (params.offset != null) qs.set("offset", String(params.offset));
  if (params.limit != null) qs.set("limit", String(params.limit));
  if (params.q) qs.set("q", params.q);
  const query = withDataset(qs, params.dataset);
  return fetchApi<FilterCategoryPage>(
    `/api/v1/filters/${encodeURIComponent(category)}${query ? `?${query}` : ""}`,
    signal,
  );
}

export function getSamples(
  params: { offset?: number; limit?: number },
  signal?: AbortSignal,
): Promise<PaginatedResponse<SampleSummary>> {
  const qs = new URLSearchParams();
  if (params.offset != null) qs.set("offset", String(params.offset));
  if (params.limit != null) qs.set("limit", String(params.limit));
  const query = qs.toString();
  return fetchApi<PaginatedResponse<SampleSummary>>(
    `/api/v1/samples${query ? `?${query}` : ""}`,
    signal,
  );
}

export function getSampleDetail(
  runId: string,
  signal?: AbortSignal,
  dataset?: Dataset,
): Promise<SampleDetail> {
  return fetchApi<SampleDetail>(
    `/api/v1/samples/${encodeURIComponent(runId)}${datasetQuery(dataset)}`,
    signal,
  );
}

/** Every sample an accession names.
 *
 * A GSM is a biological sample and a run is a sequencing run of it, so one GSM
 * can name several runs — 1,268 of the 3,919 in the corpus do, up to twelve. All
 * of them are returned so the accession box can offer the choice rather than
 * opening the first and saying nothing about the rest.
 */
export function resolveAccession(
  accession: string,
  signal?: AbortSignal,
): Promise<PaginatedResponse<SampleSummary>> {
  return fetchApi<PaginatedResponse<SampleSummary>>(
    `/api/v1/samples/resolve/${encodeURIComponent(accession)}`,
    signal,
  );
}

export function getSeriesSamples(
  runId: string,
  params: { offset?: number; limit?: number; dataset?: Dataset },
  signal?: AbortSignal,
): Promise<PaginatedResponse<SampleSummary>> {
  const qs = new URLSearchParams();
  if (params.offset != null) qs.set("offset", String(params.offset));
  if (params.limit != null) qs.set("limit", String(params.limit));
  const query = withDataset(qs, params.dataset);
  return fetchApi<PaginatedResponse<SampleSummary>>(
    `/api/v1/samples/${encodeURIComponent(runId)}/series${query ? `?${query}` : ""}`,
    signal,
  );
}

export function searchSamples(
  filters: SearchFilters,
  signal?: AbortSignal,
): Promise<SearchResponse> {
  const qs = buildSearchParams(filters);
  return fetchApi<SearchResponse>(
    `/api/v1/search${qs ? `?${qs}` : ""}`,
    signal,
  );
}

export function getSampleDownload(
  accession: string,
  signal?: AbortSignal,
  dataset?: Dataset,
): Promise<SampleDownloadResponse> {
  return fetchApi<SampleDownloadResponse>(
    `/api/v1/samples/${encodeURIComponent(accession)}/download`
      + datasetQuery(dataset),
    signal,
  );
}

export function getSimilarSamples(
  runId: string,
  limit?: number,
  signal?: AbortSignal,
  dataset?: Dataset,
): Promise<SimilarSamplesResponse> {
  const qs = new URLSearchParams();
  if (limit != null) qs.set("limit", String(limit));
  const query = withDataset(qs, dataset);
  return fetchApi<SimilarSamplesResponse>(
    `/api/v1/samples/${encodeURIComponent(runId)}/similar${query ? `?${query}` : ""}`,
    signal,
  );
}

export function checkHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return fetchApi<HealthResponse>("/health", signal);
}
