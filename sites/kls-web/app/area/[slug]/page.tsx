import Link from "next/link";
import { Clock3, ExternalLink, Filter, Users } from "lucide-react";
import { notFound } from "next/navigation";
import { BillList } from "../../components/BillList";
import { getArea, lastScannedLabel } from "../../lib/kls";

type RouteParams = Promise<{ slug: string }>;
type SearchParams = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined, fallback = "") {
  return Array.isArray(value) ? value[0] ?? fallback : value ?? fallback;
}

export default async function AreaPage({ params, searchParams }: { params: RouteParams; searchParams: SearchParams }) {
  const { slug } = await params;
  const query = await searchParams;
  const filters = {
    year: first(query.year),
    q: first(query.q),
    status: first(query.status, "all"),
    tag: first(query.tag),
  };
  const data = await getArea(slug, filters);
  if (!data) notFound();

  return (
    <main className="page-width page-main">
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
          </div>
          <p className="scan-note"><Clock3 size={15} aria-hidden="true" /> {lastScannedLabel(data.jurisdiction.last_scanned_at)}</p>
        </div>
        <dl className="area-stats">
          <div><dt>Total</dt><dd>{data.counts.total.toLocaleString()}</dd></div>
          <div><dt>Active</dt><dd>{data.counts.active.toLocaleString()}</dd></div>
          <div><dt>Passed</dt><dd>{data.counts.passed.toLocaleString()}</dd></div>
          <div><dt>Did not pass</dt><dd>{data.counts.failed.toLocaleString()}</dd></div>
        </dl>
      </section>

      {data.sync_status ? (
        <div className={data.sync_status.is_running ? "sync-bar sync-running" : "sync-bar"}>
          <strong>{data.sync_status.headline}</strong><span>{data.sync_status.detail}</span>
        </div>
      ) : null}

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
    </main>
  );
}
