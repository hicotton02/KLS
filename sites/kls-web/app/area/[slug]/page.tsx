import type { Metadata } from "next";
import { DisplayAd } from "../../components/DisplayAd";
import Link from "next/link";
import { Clock3, ExternalLink, Filter, MessageSquareQuote, Users } from "lucide-react";
import { notFound } from "next/navigation";
import { cache } from "react";
import { BillList } from "../../components/BillList";
import { getArea, lastScannedLabel } from "../../lib/kls";
import { absoluteUrl, areaHref, billPageHref, jsonLd, SITE_NAME } from "../../lib/site";

type RouteParams = Promise<{ slug: string }>;
type SearchParams = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined, fallback = "") {
  return Array.isArray(value) ? value[0] ?? fallback : value ?? fallback;
}

const loadArea = cache((slug: string, year: string, q: string, status: string, tag: string) =>
  getArea(slug, { year, q, status, tag }),
);

async function routeData(params: RouteParams, searchParams: SearchParams) {
  const { slug } = await params;
  const query = await searchParams;
  const filters = {
    year: first(query.year),
    q: first(query.q),
    status: first(query.status, "all"),
    tag: first(query.tag),
  };
  return { slug, filters, data: await loadArea(slug, filters.year, filters.q, filters.status, filters.tag) };
}

export async function generateMetadata({ params, searchParams }: { params: RouteParams; searchParams: SearchParams }): Promise<Metadata> {
  const { slug, filters, data } = await routeData(params, searchParams);
  if (!data) return { title: "Coverage Area Not Found", robots: { index: false, follow: false } };

  const latestYear = data.available_years.length ? Math.max(...data.available_years) : null;
  const canonicalYear = data.selected_year && data.selected_year !== latestYear ? data.selected_year : null;
  const canonical = areaHref(slug, canonicalYear);
  const filtered = Boolean(filters.q.trim() || filters.tag || filters.status !== "all");
  const yearLabel = data.selected_year ? ` (${data.selected_year})` : "";
  const areaLabel = data.jurisdiction.kind === "federal" ? "Federal Bills" : `${data.jurisdiction.name} Bills`;
  const title = `${areaLabel} in Plain English${yearLabel}`;
  const description = `Track ${data.jurisdiction.name} legislation${data.selected_year ? ` for ${data.selected_year}` : ""} with official sources, current status, and neutral plain-English summaries.`;

  return {
    title,
    description,
    alternates: { canonical },
    robots: { index: !filtered, follow: true },
    openGraph: { type: "website", url: canonical, title: `${title} | ${SITE_NAME}`, description },
  };
}

export default async function AreaPage({ params, searchParams }: { params: RouteParams; searchParams: SearchParams }) {
  const { slug, filters, data } = await routeData(params, searchParams);
  if (!data) notFound();

  const latestYear = data.available_years.length ? Math.max(...data.available_years) : null;
  const canonicalYear = data.selected_year && data.selected_year !== latestYear ? data.selected_year : null;
  const canonical = absoluteUrl(areaHref(slug, canonicalYear));
  const itemList = data.bills.slice(0, 20).map((bill, index) => ({
    "@type": "ListItem",
    position: index + 1,
    name: `${bill.bill_num} ${bill.plain_language_title || bill.catch_title || ""}`.trim(),
    url: absoluteUrl(billPageHref(bill.area_slug, bill.year, bill.bill_num, bill.special_session)),
  }));

  return (
    <main className="page-width page-main">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: jsonLd({
            "@context": "https://schema.org",
            "@type": "CollectionPage",
            name: `${data.jurisdiction.name} bills${data.selected_year ? ` for ${data.selected_year}` : ""}`,
            url: canonical,
            inLanguage: "en-US",
            isPartOf: { "@type": "WebSite", name: SITE_NAME, url: absoluteUrl("/") },
            mainEntity: { "@type": "ItemList", numberOfItems: data.bills.length, itemListElement: itemList },
          }),
        }}
      />
      <nav className="breadcrumbs" aria-label="Breadcrumb"><Link href="/">Coverage</Link><span>/</span><span>{data.jurisdiction.name}</span></nav>

      <section className="area-header">
        <div>
          <p className="eyebrow">{data.jurisdiction.kind === "federal" ? "Congress" : "State legislature"}</p>
          <h1>{data.jurisdiction.name}</h1>
          <p>{data.jurisdiction.description}</p>
          <div className="area-links">
            {data.jurisdiction.source_url ? (
              <a className="source-link" href={data.jurisdiction.source_url} target="_blank" rel="noreferrer">
                {data.jurisdiction.source_name} <ExternalLink size={15} aria-hidden="true" />
              </a>
            ) : null}
            {slug === "wyoming" ? <Link className="source-link" href="/area/wyoming/legislators"><Users size={15} aria-hidden="true" /> Legislator voting records</Link> : null}
            {slug === "wyoming" ? <Link className="source-link" href="/area/wyoming/vote-explanations"><MessageSquareQuote size={15} aria-hidden="true" /> Why lawmakers voted</Link> : null}
          </div>
          <p className="scan-note"><Clock3 size={15} aria-hidden="true" /> {lastScannedLabel(data.jurisdiction.last_scanned_at, data.jurisdiction.state_code)}</p>
        </div>
        <dl className="area-stats">
          <div><dt>Total</dt><dd>{data.counts.total.toLocaleString()}</dd></div>
          <div><dt>Active</dt><dd>{data.counts.active.toLocaleString()}</dd></div>
          <div><dt>Passed</dt><dd>{data.counts.passed.toLocaleString()}</dd></div>
          <div><dt>Did not pass</dt><dd>{data.counts.failed.toLocaleString()}</dd></div>
        </dl>
      </section>

      <form className="filter-bar" action={`/area/${slug}`} method="get">
        <label className="filter-field filter-year">
          <span>{data.jurisdiction.kind === "federal" ? "Congress" : "Year"}</span>
          <select name="year" defaultValue={String(data.selected_year ?? "")}>
            {data.available_years.map((year) => <option key={year} value={year}>{year}</option>)}
          </select>
        </label>
        <label className="filter-field filter-query">
          <span>Search this area</span>
          <input type="search" name="q" defaultValue={filters.q} placeholder="Bill, topic, or sponsor" />
        </label>
        <label className="filter-field">
          <span>Status</span>
          <select name="status" defaultValue={filters.status}>
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="passed">Passed</option>
            <option value="failed">Did not pass</option>
          </select>
        </label>
        <label className="filter-field">
          <span>Topic</span>
          <select name="tag" defaultValue={filters.tag}>
            <option value="">All topics</option>
            {data.available_tags.map((tag) => <option key={tag.value} value={tag.value}>{tag.label}</option>)}
          </select>
        </label>
        <button className="filter-button" type="submit"><Filter size={17} aria-hidden="true" /> Apply</button>
      </form>

      <section className="content-section" aria-labelledby="bill-list-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">{data.selected_year ?? "Current"}</p>
            <h2 id="bill-list-title">{data.bills.length} bills shown</h2>
          </div>
        </div>
        <BillList bills={data.bills} emptyMessage="No bills matched those filters." />
      </section>
      {data.bills.length ? <DisplayAd /> : null}
    </main>
  );
}
