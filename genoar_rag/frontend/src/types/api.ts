import type { Dataset } from "@/lib/datasets";

// TypeScript types mirroring backend Pydantic models

export type SortOrder = "asc" | "desc";
export type SearchMode = "sql" | "semantic" | "hybrid";

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  offset: number;
  limit: number;
}

export interface ExtendedField {
  field_name: string;
  field_value: string | null;
  data_type: string | null;
}

export interface SampleSummary {
  run_id: string;
  /** One of the run's studies — whichever `sra_core.Series` kept. */
  series: string | null;
  /** All of them; 870 of the 6,199 runs belong to two. */
  all_series: string[];
  biosample: string | null;
  tissue: string | null;
  cell_type: string | null;
  /** What the submitter wrote. */
  disease: string | null;
  /** What the disease filter files it under; differs from `disease` for most
      samples that have one, which is why both are shown. */
  disease_category: string | null;
  organism: string | null;
  assay_type: string | null;
  library_source: string | null;
  platform: string | null;
  instrument: string | null;
  similarity_score: number | null;
}

export interface SampleDetail extends SampleSummary {
  sample_name: string | null;
  treatment: string | null;
  sex: string | null;
  age: string | null;
  strain: string | null;
  genotype: string | null;
  size: number | null;
  str_tis: string | null;
  str_dis: string | null;
  str_cell: string | null;
  cui_tis: string | null;
  cui_dis: string | null;
  cui_cell: string | null;
  extended_fields: ExtendedField[];
}

export interface SearchFilters {
  organism?: string[];
  tissue?: string[];
  cell_type?: string[];
  assay_type?: string[];
  library_source?: string[];
  disease?: string[];
  platform?: string[];
  series?: string;
  keyword?: string;
  offset?: number;
  limit?: number;
  /** How many candidates semantic/hybrid search returns in total. */
  top_k?: number;
  sort_by?: string;
  sort_order?: SortOrder;
  search_mode?: SearchMode;
  /** Which corpus this search is of. See `lib/dataset`. */
  dataset?: Dataset;
}

export interface SearchResponse {
  items: SampleSummary[];
  total: number;
  offset: number;
  limit: number;
  filters_applied: SearchFilters;
  search_mode?: SearchMode;
}

export interface FilterValue {
  value: string;
  count: number;
}

export interface FilterCategory {
  name: string;
  values: FilterValue[];
  total_distinct: number;
}

/** One page of a single category's values, from `/api/v1/filters/{category}`. */
export interface FilterCategoryPage extends FilterCategory {
  offset: number;
  limit: number;
  query: string | null;
}

export interface FilterOptionsResponse {
  categories: FilterCategory[];
}

export interface DistributionItem {
  label: string;
  count: number;
  percentage: number;
}

/** One rung of the metadata ladder. The rungs partition a species, so their
    counts sum to the species total and can be drawn as shares of a whole. */
export interface CompletenessTier {
  key: string;
  label: string;
  count: number;
  percentage: number;
}

export interface SpeciesCompleteness {
  species: string;
  organism: string;
  total: number;
  tiers: CompletenessTier[];
}

export interface DashboardStats {
  total_samples: number;
  total_series: number;
  organism_distribution: DistributionItem[];
  assay_distribution: DistributionItem[];
  platform_distribution: DistributionItem[];
  library_source_distribution: DistributionItem[];
  metadata_completeness: SpeciesCompleteness[];
}

export interface SimilarSampleItem {
  run_id: string;
  similarity_score: number;
  tissue: string | null;
  cell_type: string | null;
  disease: string | null;
  organism: string | null;
  assay_type: string | null;
}

export interface SimilarSamplesResponse {
  query_run_id: string;
  items: SimilarSampleItem[];
  total: number;
}

export interface HealthResponse {
  status: string;
  semantic_search: boolean;
  /** Species with a loaded vector index, e.g. ["human", "mouse"]. A species whose
   *  index is missing is absent here, so semantic search should not be offered for it. */
  semantic_species: string[];
}

/** One way to obtain a sample's data. `available` is false when GENOAR knows the
 *  source exists but has no URL to offer, in which case `note` says why. */
export interface DownloadSource {
  kind: string;
  available: boolean;
  /** "download" for a file GENOAR serves, "visit" for somebody else's page. */
  action: string;
  url: string | null;
  size_bytes: number | null;
  description: string | null;
  note: string | null;
  /** What the pipeline can account for about the file behind this source, as one
   *  of `SampleProvenance["status"]`. Absent on a source that is not pipeline
   *  output, and on a source read from another host, where there is nothing to
   *  check. Repeated here so a client reading only `sources` can tell verified
   *  output from the rest. */
  provenance?: string | null;
}

/** What the pipeline can account for about a run's Cell Ranger output. Stage 3
 *  leaves a receipt beside each sample's output saying what produced it, and
 *  this is that receipt as the service read it.
 *
 *  `status` is one of:
 *
 *  - `verified`. A run's own Cell Ranger produced the output and recorded it.
 *  - `adopted`. An operator told a run to reuse output nobody could account
 *    for. No run has tied it to the input it claims to come from.
 *  - `unreadable`. A receipt is there and the service cannot act on it.
 *  - `unrecorded`. There is no receipt. The output predates provenance
 *    tracking, or it was produced outside this pipeline.
 *
 *  `verified` is the same fact as `status === "verified"`, in a form a client
 *  can branch on without knowing the vocabulary. */
export interface SampleProvenance {
  status: string;
  verified: boolean;
  /** Whether the service hands the files out. Verified output is always
   *  offered. Unrecorded output is offered unless the deployment sets
   *  `PROCESSED_RESULTS_REQUIRE_RECEIPT`. Adopted and unreadable are never
   *  offered. */
  offered: boolean;
  /** The run the receipt names as having produced or adopted the output. Null
   *  when there is no readable receipt. */
  receipt_run_id: string | null;
  /** When the receipt was written, as the receipt states it. */
  recorded_at: string | null;
  note: string;
}

/** How a sample's matrix was made, read out of the matrix itself. Each field is
    null when the file does not carry it. */
export interface ProcessingParameters {
  software: string | null;
  chemistry: string | null;
  reference: string | null;
}

export interface SampleDownloadResponse {
  run_id: string;
  geo_accession: string | null;
  requested_accession: string | null;
  requires_api_key: boolean;
  /** Present when the run has a processed matrix to read them from. */
  processing?: ProcessingParameters | null;
  /** Present when this deployment reads its processed data off a mounted results
   *  directory and this run has output there. Absent when there is nothing on
   *  disk for the service to have read a receipt beside. Carried whether or not
   *  the output is offered, so a client can tell a withheld sample from an
   *  unprocessed one. */
  provenance?: SampleProvenance | null;
  sources: DownloadSource[];
}
