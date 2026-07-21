import Link from "next/link";
import { AlertTriangle, ArrowLeft, CalendarDays, UserRound, Vote } from "lucide-react";
import { notFound } from "next/navigation";
import { getLegislatorVotingRecord } from "../../../../lib/kls";

type RouteParams = Promise<{ memberKey: string }>;
type SearchParams = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

const voteLabels: Record<string, string> = {
  yes: "Yes",
  no: "No",
  absent: "Absent",
  excused: "Excused",
  conflict: "Conflict",
  other: "Other",
};

export default async function LegislatorVotingRecordPage({ params, searchParams }: { params: RouteParams; searchParams: SearchParams }) {
  const { memberKey } = await params;
  const query = await searchParams;
  const selectedYear = first(query.year) ?? "";
  const data = await getLegislatorVotingRecord(memberKey, selectedYear);
  if (!data) notFound();

  const legislator = data.legislator;
  const district = legislator.district?.replace(/^[HS]/, "District ");

  return (
    <main className="page-width page-main legislator-page">
      <Link className="back-link" href="/area/wyoming/legislators"><ArrowLeft size={17} aria-hidden="true" /> Back to Wyoming legislators</Link>

      <header className="legislator-header">
        <div className="legislator-identity">
          <span className="legislator-avatar" aria-hidden="true"><UserRound size={28} /></span>
          <div>
            <p className="eyebrow">{legislator.title}</p>
            <h1>{legislator.name}</h1>
            <p>{[legislator.party, district].filter(Boolean).join(" · ") || "Wyoming Legislature"}</p>
          </div>
        </div>
        <form className="year-picker" action={`/area/wyoming/legislators/${encodeURIComponent(memberKey)}`} method="get">
          <label><span>Session</span><select name="year" defaultValue={selectedYear}><option value="">All years</option>{data.available_years.map((year) => <option key={year} value={year}>{year}</option>)}</select></label>
          <button className="filter-button" type="submit">Apply</button>
        </form>
      </header>

      <section className="content-section" aria-labelledby="breakdown-title">
        <div className="section-heading"><div><p className="eyebrow">Official roll calls</p><h2 id="breakdown-title">Voting breakdown</h2></div><p>{selectedYear || "All stored sessions"}</p></div>
        <dl className="vote-summary-grid">
          {(["yes", "no", "absent", "excused", "conflict"] as const).map((position) => (
            <div className={`vote-summary-${position}`} key={position}><dt>{voteLabels[position]}</dt><dd>{data.counts[position]}</dd></div>
          ))}
          <div><dt>Total roll calls</dt><dd>{data.counts.total}</dd></div>
        </dl>
        {data.coverage.unattributed_roll_calls ? (
          <p className="source-note"><AlertTriangle size={17} aria-hidden="true" />
            {data.coverage.unattributed_roll_calls} {data.coverage.unattributed_roll_calls === 1 ? "roll call is" : "roll calls are"} excluded because Wyoming published a tally without member names.
          </p>
        ) : null}
      </section>

      <section className="content-section" aria-labelledby="vote-record-title">
        <div className="section-heading"><div><p className="eyebrow">Bill by bill</p><h2 id="vote-record-title">Recorded votes</h2></div><p>{data.votes.length} shown</p></div>
        <div className="vote-record-list">
          {data.votes.map((vote, index) => (
            <article className="vote-record-row" key={`${vote.year}-${vote.bill_num}-${vote.vote_date}-${index}`}>
              <CalendarDays size={18} aria-hidden="true" />
              <div className="vote-record-copy">
                <div className="vote-record-meta"><time>{vote.vote_date?.slice(0, 10) || String(vote.year)}</time><span>{vote.year}</span></div>
                <h3><Link href={vote.bill_href}>{vote.bill_num}: {vote.catch_title || vote.bill_title || "Wyoming bill"}</Link></h3>
                <p>{vote.action || "Official roll call"}</p>
              </div>
              <span className={`vote-badge vote-${vote.vote_position}`}><Vote size={15} aria-hidden="true" /> {voteLabels[vote.vote_position] || vote.vote_position}</span>
            </article>
          ))}
        </div>
      </section>
    </main>
  );
}
