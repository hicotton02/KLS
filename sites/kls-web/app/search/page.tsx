import { Filter, Search } from "lucide-react";
import { BillList } from "../components/BillList";
import { getSearch } from "../lib/kls";

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined, fallback = "") {
  return Array.isArray(value) ? value[0] ?? fallback : value ?? fallback;
}

export default async function SearchPage({ searchParams }: { searchParams: SearchParams }) {
  const params = await searchParams;
  const filters = {
    q: first(params.q),
    area: first(params.area, "all"),
    year: first(params.year),
    status: first(params.status, "all"),
    tag: first(params.tag),
  };
  const data = await getSearch(filters);
  const hasCriteria = Boolean(filters.q || filters.year || filters.tag || filters.area !== "all" || filters.status !== "all");

  return (
    <main className="page-width page-main">
      <div className="page-title-row">
        <div>
          <p className="eyebrow">All coverage areas</p>
          <h1>Search bills</h1>
          <p>Search official records and plain-English interpretations together.</p>
        </div>
      </div>

      <form className="filter-bar search-filters" action="/search" method="get">
        <label className="filter-field filter-query">
          <span>Search</span>
          <div className="input-with-icon"><Search size={18} aria-hidden="true" /><input type="search" name="q" defaultValue={filters.q} placeholder="Bill, topic, sponsor, or phrase" /></div>
        </label>
        <label className="filter-field">
          <span>Area</span>
          <select name="area" defaultValue={filters.area}>
            <option value="all">All coverage</option>
            {data.areas.map((area) => <option key={area.value} value={area.value}>{area.label}</option>)}
          </select>
        </label>
        <label className="filter-field filter-year">
          <span>Year</span>
          <input type="number" name="year" min="2020" defaultValue={filters.year} placeholder="Any" />
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
        <button className="filter-button" type="submit"><Filter size={17} aria-hidden="true" /> Apply</button>
      </form>

      <section className="content-section" aria-labelledby="results-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Results</p>
            <h2 id="results-title">{hasCriteria ? `${data.results.length} bills found` : "Ready when you are"}</h2>
          </div>
        </div>
        <BillList
          bills={data.results}
          emptyMessage={hasCriteria ? "No bills matched those filters." : "Enter a bill number, topic, sponsor, or phrase above."}
        />
      </section>
    </main>
  );
}
