export type Counts = {
  total: number;
  active: number;
  passed: number;
  failed: number;
};

export type SyncStatus = {
  is_running: boolean;
  headline: string;
  detail: string;
} | null;

export type Jurisdiction = {
  slug: string;
  name: string;
  kind: "state" | "federal";
  state_code: string | null;
  coverage_status: string;
  coverage_note: string;
  description: string;
  source_name: string;
  source_url: string | null;
  latest_year?: number | null;
  counts?: Counts;
  sync_status?: SyncStatus;
};

export type BillTag = { value: string; label: string };

export type BillSummary = {
  area_slug: string;
  area_name: string;
  area_kind: "state" | "federal";
  state_code: string | null;
  year: number;
  special_session: number | null;
  bill_num: string;
  catch_title: string | null;
  bill_title: string | null;
  sponsor: string | null;
  status_label: string | null;
  status_explainer: string | null;
  outcome: string | null;
  last_action: string | null;
  last_action_date: string | null;
  updated_at: string | null;
  plain_language_title: string | null;
  summary: string | null;
  interpretation_model: string | null;
  fact_check_status: string | null;
  tags: BillTag[];
  legacy_href: string;
};

export type Overview = {
  site_name: string;
  interpretation_model: string;
  jurisdictions: Jurisdiction[];
  recent_bills: BillSummary[];
};

export type AreaResponse = {
  jurisdiction: Jurisdiction;
  available_years: number[];
  available_tags: BillTag[];
  selected_year: number | null;
  query: string;
  status: string;
  tag: string;
  counts: Counts;
  sync_status: SyncStatus;
  bills: BillSummary[];
};

export type SearchResponse = {
  query: string;
  area: string;
  year: number | null;
  status: string;
  tag: string;
  areas: { value: string; slug: string; label: string }[];
  available_tags: BillTag[];
  results: BillSummary[];
};

export type Interpretation = {
  plain_language_title?: string;
  one_sentence_summary?: string;
  what_it_does?: string[];
  who_it_affects?: string[];
  terms_to_know?: { term: string; meaning: string }[];
  limits_and_unknowns?: string[];
  fact_check_status?: string;
  fact_check_result?: string;
  fact_check_notes?: string[];
  generator_model?: string;
};

export type BillDetailResponse = {
  jurisdiction: Jurisdiction;
  bill: BillSummary & {
    signed_date: string | null;
    effective_date: string | null;
    chapter_no: string | null;
    enrolled_no: string | null;
    official_summary_text: string | null;
    official_digest_text: string | null;
  };
  interpretation: Interpretation;
  official_links: Record<string, string | null>;
  actions: { statusDate?: string; statusMessage?: string; location?: string }[];
  amendments: Array<Record<string, unknown>>;
  relationships: Array<{
    peer: BillSummary;
    relationship_strength: string;
    needs_human_review: boolean;
    pair_summaries: string[];
    combined_effects: string[];
    why_reviews: string[];
    evidence_items: string[];
  }>;
  interpretation_model: string;
};

const API_BASE_URL = (process.env.KLS_API_BASE_URL ?? "https://www.keepinglawsimple.org").replace(/\/$/, "");

const STATE_AREAS: Array<[string, string, string]> = [
  ["alabama", "Alabama", "al"], ["alaska", "Alaska", "ak"], ["arizona", "Arizona", "az"],
  ["arkansas", "Arkansas", "ar"], ["california", "California", "ca"], ["colorado", "Colorado", "co"],
  ["connecticut", "Connecticut", "ct"], ["delaware", "Delaware", "de"],
  ["district-of-columbia", "District of Columbia", "dc"], ["florida", "Florida", "fl"],
  ["georgia", "Georgia", "ga"], ["hawaii", "Hawaii", "hi"], ["idaho", "Idaho", "id"],
  ["illinois", "Illinois", "il"], ["indiana", "Indiana", "in"], ["iowa", "Iowa", "ia"],
  ["kansas", "Kansas", "ks"], ["kentucky", "Kentucky", "ky"], ["louisiana", "Louisiana", "la"],
  ["maine", "Maine", "me"], ["maryland", "Maryland", "md"], ["massachusetts", "Massachusetts", "ma"],
  ["michigan", "Michigan", "mi"], ["minnesota", "Minnesota", "mn"], ["mississippi", "Mississippi", "ms"],
  ["missouri", "Missouri", "mo"], ["montana", "Montana", "mt"], ["nebraska", "Nebraska", "ne"],
  ["nevada", "Nevada", "nv"], ["new-hampshire", "New Hampshire", "nh"], ["new-jersey", "New Jersey", "nj"],
  ["new-mexico", "New Mexico", "nm"], ["new-york", "New York", "ny"],
  ["north-carolina", "North Carolina", "nc"], ["north-dakota", "North Dakota", "nd"],
  ["ohio", "Ohio", "oh"], ["oklahoma", "Oklahoma", "ok"], ["oregon", "Oregon", "or"],
  ["pennsylvania", "Pennsylvania", "pa"], ["rhode-island", "Rhode Island", "ri"],
  ["south-carolina", "South Carolina", "sc"], ["south-dakota", "South Dakota", "sd"],
  ["tennessee", "Tennessee", "tn"], ["texas", "Texas", "tx"], ["utah", "Utah", "ut"],
  ["vermont", "Vermont", "vt"], ["virginia", "Virginia", "va"], ["washington", "Washington", "wa"],
  ["west-virginia", "West Virginia", "wv"], ["wisconsin", "Wisconsin", "wi"], ["wyoming", "Wyoming", "wy"],
];

const EMPTY_COUNTS: Counts = { total: 0, active: 0, passed: 0, failed: 0 };

const FALLBACK_AREAS: Jurisdiction[] = [
  ...STATE_AREAS.map(([slug, name, state_code]) => ({
    slug,
    name,
    kind: "state" as const,
    state_code,
    coverage_status: "live",
    coverage_note: "Available",
    description: `Official ${name} legislation with plain-English summaries and source links.`,
    source_name: `${name} Legislature`,
    source_url: null,
    latest_year: null,
    counts: EMPTY_COUNTS,
    sync_status: null,
  })),
  {
    slug: "federal",
    name: "Federal",
    kind: "federal",
    state_code: "us",
    coverage_status: "live",
    coverage_note: "Available",
    description: "Recent federal bills from Congress with official text and plain-English summaries.",
    source_name: "Congress.gov",
    source_url: "https://www.congress.gov",
    latest_year: null,
    counts: EMPTY_COUNTS,
    sync_status: null,
  },
];

async function fetchKls<T>(path: string): Promise<T | null> {
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      headers: { Accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) return null;
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

function queryString(values: Record<string, string | number | null | undefined>) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined && value !== null && value !== "") params.set(key, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

export async function getOverview(): Promise<Overview> {
  return (
    (await fetchKls<Overview>("/api/v1/overview")) ?? {
      site_name: "Keeping Law Simple",
      interpretation_model: "qwen3.5:27b",
      jurisdictions: FALLBACK_AREAS,
      recent_bills: [],
    }
  );
}

export async function getArea(
  slug: string,
  filters: { year?: string; q?: string; status?: string; tag?: string },
): Promise<AreaResponse | null> {
  const data = await fetchKls<AreaResponse>(
    `/api/v1/areas/${encodeURIComponent(slug)}${queryString({ ...filters, limit: 60 })}`,
  );
  if (data) return data;
  const jurisdiction = FALLBACK_AREAS.find((area) => area.slug === slug);
  if (!jurisdiction) return null;
  return {
    jurisdiction,
    available_years: [],
    available_tags: [],
    selected_year: null,
    query: filters.q ?? "",
    status: filters.status ?? "all",
    tag: filters.tag ?? "",
    counts: EMPTY_COUNTS,
    sync_status: null,
    bills: [],
  };
}

export async function getSearch(filters: {
  q?: string;
  area?: string;
  year?: string;
  status?: string;
  tag?: string;
}): Promise<SearchResponse> {
  return (
    (await fetchKls<SearchResponse>(`/api/v1/search${queryString({ ...filters, limit: 60 })}`)) ?? {
      query: filters.q ?? "",
      area: filters.area ?? "all",
      year: filters.year ? Number(filters.year) : null,
      status: filters.status ?? "all",
      tag: filters.tag ?? "",
      areas: FALLBACK_AREAS.map((area) => ({ value: area.state_code ?? "", slug: area.slug, label: area.name })),
      available_tags: [],
      results: [],
    }
  );
}

export async function getBillDetail(
  slug: string,
  year: string,
  billNum: string,
  specialSession?: string,
): Promise<BillDetailResponse | null> {
  return fetchKls<BillDetailResponse>(
    `/api/v1/areas/${encodeURIComponent(slug)}/bills/${encodeURIComponent(year)}/${encodeURIComponent(billNum)}` +
      queryString({ special_session: specialSession }),
  );
}

export function billHref(bill: BillSummary) {
  const query = bill.special_session === null ? "" : `?special_session=${bill.special_session}`;
  return `/area/${bill.area_slug}/bill/${bill.year}/${encodeURIComponent(bill.bill_num)}${query}`;
}
