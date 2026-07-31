import type { Metadata } from "next";
import Link from "next/link";
import { ArrowLeft, CalendarDays, ChevronRight, FileCheck2, Filter, ShieldCheck } from "lucide-react";
import { billHref, formatScanTimestamp, getVoteExplanations } from "../../../lib/kls";

export const metadata: Metadata = {
  title: "Why Wyoming Lawmakers Voted",
  description: "Plain-language reasons Wyoming lawmakers gave for their votes, linked to public sources.",
};

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

export default async function WyomingVoteExplanationsPage({ searchParams }: { searchParams: SearchParams }) {
  const query = await searchParams;
  const selectedYear = first(query.year);
  const data = await getVoteExplanations(selectedYear);
  const latestScan = formatScanTimestamp(data.last_scanned_at, data.jurisdiction.state_code);

  return (
    <main className="page-main beta-page">
      <div className="page-width">
        <Link className="back-link" href="/area/wyoming">
          <ArrowLeft size={17} aria-hidden="true" /> Back to Wyoming bills
        </Link>

        <section className="beta-intro" aria-labelledby="explanations-title">
          <div>
            <p className="eyebrow">Wyoming vote records</p>
            <h1 id="explanations-title">Why did they vote that way?</h1>
            <p className="beta-lede">
              We show a reason only when a lawmaker explained it in a public source. Every reason links to the statement.
            </p>
          </div>
          <div className="beta-scan" aria-label={latestScan ? `Latest scan ${latestScan}` : "Scan in progress"}>
            <CalendarDays size={22} strokeWidth={1.8} aria-hidden="true" />
            <div>
              <span>Latest scan</span>
              <strong>{latestScan ?? "In progress"}</strong>
            </div>
          </div>
        </section>

        {data.available_years.length ? (
          <form className="explanation-year-filter" action="/area/wyoming/vote-explanations" method="get">
            <label>
              <span>Year</span>
              <select name="year" defaultValue={String(data.selected_year ?? data.available_years[0])}>
                {data.available_years.map((year) => <option key={year} value={year}>{year}</option>)}
              </select>
            </label>
            <button type="submit"><Filter size={17} aria-hidden="true" /> Show year</button>
          </form>
        ) : null}

        <section className="beta-explanations" aria-labelledby="checked-bills-title">
          <div className="beta-section-heading">
            <div>
              <p className="eyebrow">Published reasons</p>
              <h2 id="checked-bills-title">Bills with a clear explanation</h2>
            </div>
          </div>

          {data.bills.length ? (
            <ol className="explanation-bill-list">
              {data.bills.map((item) => (
                <li key={`${item.bill.year}-${item.bill.bill_num}`}>
                  <Link href={billHref(item.bill)}>
                    <FileCheck2 size={20} aria-hidden="true" />
                    <span>
                      <strong>{item.bill.bill_num}: {item.bill.plain_language_title || item.bill.catch_title || item.bill.bill_title}</strong>
                      <small>{item.explanation_count} published reason{item.explanation_count === 1 ? "" : "s"}</small>
                    </span>
                    <ChevronRight size={18} aria-hidden="true" />
                  </Link>
                </li>
              ))}
            </ol>
          ) : (
            <p className="explanation-empty">We are still checking public recordings for this year.</p>
          )}
        </section>

        <section className="beta-rule" aria-labelledby="source-rule-title">
          <ShieldCheck size={24} strokeWidth={1.8} aria-hidden="true" />
          <div>
            <h2 id="source-rule-title">No guessing</h2>
            <p>A vote alone does not prove a lawmaker&apos;s reason. If we cannot support a reason with a public statement, we say: Couldn&apos;t find a published reason.</p>
          </div>
        </section>
      </div>
    </main>
  );
}
