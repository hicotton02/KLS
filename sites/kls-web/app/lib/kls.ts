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
  last_scanned_at: string | null;
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
  fact_check_status: string | null;
  tags: BillTag[];
  legacy_href: string;
};

export type Overview = {
  site_name: string;
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
};

export type RollCallMember = {
  member_key: string;
  source_legislator_id: string | null;
  name: string;
  vote_label: string;
  party: string | null;
  district: string | null;
  chamber: string;
  title: string;
  vote: "yes" | "no" | "absent" | "conflict" | "excused" | "other";
  profile_href: string;
};

export type RollCall = {
  roll_call_key: string;
  vote_id: string | null;
  chamber: string;
  vote_date: string | null;
  vote_type: string | null;
  action: string | null;
  amendment_number: string | null;
  counts: { yes: number; no: number; absent: number; conflict: number; excused: number };
  members: RollCallMember[];
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
  roll_calls: RollCall[];
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
};

export type LegislatorSummary = {
  member_key: string;
  source_legislator_id: string | null;
  legislator_name: string;
  party: string | null;
  district: string | null;
  chamber: string;
  latest_year: number;
  total_votes: number;
  bills_voted: number;
  yes_count: number;
  no_count: number;
  absent_count: number;
  conflict_count: number;
  excused_count: number;
  title: string;
  profile_href: string;
};

export type LegislatorsResponse = {
  jurisdiction: Jurisdiction;
  available_years: number[];
  selected_year: number | null;
  query: string;
  legislators: LegislatorSummary[];
};

export type LegislatorVote = {
  year: number;
  special_session_value: number | null;
  bill_num: string;
  vote_position: "yes" | "no" | "absent" | "conflict" | "excused" | "other";
  vote_date: string | null;
  action: string | null;
  amendment_number: string | null;
  catch_title: string | null;
  bill_title: string | null;
  outcome: string | null;
  status_label: string | null;
  bill_href: string;
};

export type LegislatorRecordResponse = {
  jurisdiction: Jurisdiction;
  legislator: {
    member_key: string;
    source_legislator_id: string | null;
    name: string;
    party: string | null;
    district: string | null;
    chamber: string;
    title: string;
  };
  available_years: number[];
  selected_year: number | null;
  counts: Record<"yes" | "no" | "absent" | "conflict" | "excused" | "other" | "total", number>;
  year_breakdown: Array<Record<string, number>>;
  votes: LegislatorVote[];
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
    last_scanned_at: null,
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
    last_scanned_at: null,
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

export async function getLegislators(filters: { q?: string; year?: string }): Promise<LegislatorsResponse | null> {
  return fetchKls<LegislatorsResponse>(
    `/api/v1/areas/wyoming/legislators${queryString({ ...filters, limit: 150 })}`,
  );
}

export async function getLegislatorVotingRecord(
  memberKey: string,
  year?: string,
): Promise<LegislatorRecordResponse | null> {
  return fetchKls<LegislatorRecordResponse>(
    `/api/v1/areas/wyoming/legislators/${encodeURIComponent(memberKey)}${queryString({ year })}`,
  );
}

export function billHref(bill: BillSummary) {
  const query = bill.special_session === null ? "" : `?special_session=${bill.special_session}`;
  return `/area/${bill.area_slug}/bill/${bill.year}/${encodeURIComponent(bill.bill_num)}${query}`;
}

export function formatScanTimestamp(value: string | null | undefined, compact = false) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: compact ? undefined : "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "UTC",
    timeZoneName: "short",
  }).format(date);
}

export function lastScannedLabel(value: string | null | undefined, compact = false) {
  const timestamp = formatScanTimestamp(value, compact);
  return timestamp ? `Last scanned ${timestamp}` : "Not yet scanned";
}
