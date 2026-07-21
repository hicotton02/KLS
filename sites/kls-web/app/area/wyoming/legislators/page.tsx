import Link from "next/link";
import { ArrowLeft, ChevronRight, Search, Users } from "lucide-react";
import { notFound } from "next/navigation";
import { getLegislators } from "../../../lib/kls";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

export default async function WyomingLegislatorsPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const filters = { q: first(params.q) ?? "", year: first(params.year) ?? "" };
  const data = await getLegislators(filters);
  if (!data) notFound();

  return (
    <main className="page-width page-main">
      <Link className="back-link" href="/area/wyoming"><ArrowLeft size={17} aria-hidden="true" /> Back to Wyoming bills</Link>

      <header className="page-title-row legislator-directory-header">
        <div className="section-icon"><Users size={23} aria-hidden="true" /></div>
        <div>
          <p className="eyebrow">Wyoming Legislature</p>
          <h1>Legislator voting records</h1>
          <p>House and Senate roll calls from official Wyoming legislative records.</p>
        </div>
      </header>

      <form className="filter-bar legislator-filter" action="/area/wyoming/legislators" method="get">
        <label className="filter-field filter-query">
          <span>Find a legislator</span>
          <span className="input-with-icon"><Search size={17} aria-hidden="true" /><input name="q" defaultValue={filters.q} placeholder="Name or district" /></span>
        </label>
        <label className="filter-field">
          <span>Session year</span>
          <select name="year" defaultValue={filters.year}>
            <option value="">All years</option>
            {data.available_years.map((year) => <option key={year} value={year}>{year}</option>)}
          </select>
        </label>
        <button className="filter-button" type="submit">Apply</button>
      </form>

      <section className="content-section" aria-labelledby="legislator-list-title">
        <div className="section-heading">
          <div><p className="eyebrow">Roll-call index</p><h2 id="legislator-list-title">{data.legislators.length} legislators</h2></div>
        </div>
        {data.legislators.length ? (
          <div className="legislator-list">
            {data.legislators.map((legislator) => (
              <article className="legislator-row" key={legislator.member_key}>
                <div>
                  <span>{legislator.title}</span>
                  <h3><Link href={legislator.profile_href}>{legislator.legislator_name}</Link></h3>
                  <p>{[legislator.party, legislator.district].filter(Boolean).join(" · ") || "District unavailable"}</p>
                </div>
                <dl>
                  <div><dt>Yes</dt><dd>{legislator.yes_count}</dd></div>
                  <div><dt>No</dt><dd>{legislator.no_count}</dd></div>
                  <div><dt>Roll calls</dt><dd>{legislator.total_votes}</dd></div>
                </dl>
                <Link className="row-arrow" href={legislator.profile_href} aria-label={`Open ${legislator.legislator_name}'s voting record`}><ChevronRight size={20} /></Link>
              </article>
            ))}
          </div>
        ) : <div className="empty-state"><p>No legislators matched those filters.</p></div>}
      </section>
    </main>
  );
}
