import Link from "next/link";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowUpRight,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  Clock3,
  MessageSquareQuote,
  UserRound,
  UsersRound,
  Vote,
} from "lucide-react";
import { notFound } from "next/navigation";
import {
  billHref,
  formatBillDate,
  formatScanTimestamp,
  getLegislatorVotingRecord,
  type LegislatorVote,
} from "../../../../lib/kls";

type RouteParams = Promise<{ memberKey: string }>;
type SearchParams = Promise<Record<string, string | string[] | undefined>>;

const voteLabels: Record<LegislatorVote["vote_position"], string> = {
  yes: "Yes",
  no: "No",
  absent: "Absent",
  conflict: "Conflict",
  excused: "Excused",
  other: "Other",
};

function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

function voteKey(vote: LegislatorVote) {
  return [
    vote.bill_num,
    vote.vote_date,
    vote.vote_position,
    vote.action,
    vote.amendment_number,
    vote.yes_count,
    vote.no_count,
  ].join("|");
}

function votedWithMajority(vote: LegislatorVote) {
  return (vote.vote_position === "yes") === (vote.yes_count > vote.no_count);
}

function dateValue(value: string | null) {
  const parsed = value ? new Date(value).getTime() : 0;
  return Number.isNaN(parsed) ? 0 : parsed;
}

function districtLabel(value: string | null) {
  const match = value?.match(/^[HS](\d+)$/);
  return match ? `District ${Number(match[1])}` : value;
}

function partyLabel(value: string | null) {
  if (value === "R") return "Republican";
  if (value === "D") return "Democrat";
  return value;
}

async function loadVotingRecord(memberKey: string, requestedYear: string | undefined) {
  if (requestedYear === "all") {
    return getLegislatorVotingRecord(memberKey);
  }
  if (requestedYear) {
    return getLegislatorVotingRecord(memberKey, requestedYear);
  }

  return getLegislatorVotingRecord(memberKey, undefined, true);
}

export default async function LegislatorVotingRecordPage({
  params,
  searchParams,
}: {
  params: RouteParams;
  searchParams: SearchParams;
}) {
  const { memberKey } = await params;
  const query = await searchParams;
  const requestedYear = first(query.year);
  const data = await loadVotingRecord(memberKey, requestedYear);
  if (!data) notFound();

  const uniqueVotes = [...new Map(data.votes.map((vote) => [voteKey(vote), vote])).values()];
  const displayedVotes = uniqueVotes.slice(0, 25);
  const floorVotes = uniqueVotes.filter((vote) => vote.vote_type === "F");
  const majorityVotes = floorVotes.filter(
    (vote) =>
      (vote.vote_position === "yes" || vote.vote_position === "no") &&
      vote.yes_count !== vote.no_count,
  );
  const withMajority = majorityVotes.filter(votedWithMajority).length;
  const againstMajority = majorityVotes.length - withMajority;
  const closeVoteCount = majorityVotes.filter(
    (vote) => Math.abs(vote.yes_count - vote.no_count) <= 5,
  ).length;
  const publishedReasons = data.published_reasons ?? [];
  const reasonBillNumbers = new Set(publishedReasons.map(({ bill }) => bill.bill_num));
  const closeVotes = majorityVotes
    .filter(
      (vote) =>
        Math.abs(vote.yes_count - vote.no_count) <= 2 &&
        !votedWithMajority(vote) &&
        !reasonBillNumbers.has(vote.bill_num),
    )
    .sort((left, right) => {
      const margin = Math.abs(left.yes_count - left.no_count) - Math.abs(right.yes_count - right.no_count);
      return margin || dateValue(right.vote_date) - dateValue(left.vote_date);
    })
    .slice(0, 2);

  const recentDates = uniqueVotes.map((vote) => dateValue(vote.vote_date)).filter(Boolean);
  const earliestRecentDate = recentDates.length
    ? formatBillDate(new Date(Math.min(...recentDates)).toISOString())
    : null;
  const latestRecentDate = recentDates.length
    ? formatBillDate(new Date(Math.max(...recentDates)).toISOString())
    : null;
  const scanDate = formatScanTimestamp(data.jurisdiction.last_scanned_at, "wy");
  const countedVotes = data.counts.yes + data.counts.no;
  const yesPercent = countedVotes ? Math.round((data.counts.yes / countedVotes) * 100) : 0;
  const missedVotes = data.counts.absent + data.counts.excused;
  const legislator = data.legislator;
  const chamberName = legislator.chamber === "S" ? "Senate" : "House";
  const selectedYear = data.selected_year ? String(data.selected_year) : "all";
  const sessionLabel = data.selected_year ? `${data.selected_year} session` : "All stored sessions";
  const subtitle = [
    partyLabel(legislator.party),
    legislator.title,
    districtLabel(legislator.district),
  ].filter(Boolean).join(" · ");

  return (
    <main className="page-width page-main citizen-beta-page">
      <Link className="back-link" href="/area/wyoming/legislators">
        <ArrowLeft size={17} aria-hidden="true" /> Back to Wyoming legislators
      </Link>

      <header className="citizen-beta-header">
        <div className="citizen-beta-identity">
          <span className="citizen-beta-avatar" aria-hidden="true"><UserRound size={30} /></span>
          <div>
            <p className="eyebrow">{sessionLabel} voting record</p>
            <h1>{legislator.name}</h1>
            <p>{subtitle || "Wyoming Legislature"}</p>
          </div>
        </div>
        <div className="citizen-profile-tools">
          <div className="citizen-beta-scan">
            <Clock3 size={19} aria-hidden="true" />
            <div><span>Last scanned</span><strong>{scanDate || "Not yet scanned"}</strong></div>
          </div>
          <form className="year-picker" action={`/area/wyoming/legislators/${encodeURIComponent(memberKey)}`} method="get">
            <label>
              <span>Session</span>
              <select name="year" defaultValue={selectedYear}>
                {data.available_years.map((year) => <option key={year} value={year}>{year}</option>)}
                <option value="all">All years</option>
              </select>
            </label>
            <button className="filter-button" type="submit">Apply</button>
          </form>
        </div>
      </header>

      <section className="citizen-beta-section" aria-labelledby="at-a-glance-title">
        <div className="citizen-beta-section-heading">
          <div><p className="eyebrow">The simple version</p><h2 id="at-a-glance-title">At a glance</h2></div>
          <p>Official roll-call totals for {data.selected_year ? `the ${data.selected_year} session` : "all stored sessions"}.</p>
        </div>

        <dl className="citizen-beta-metrics">
          <div><dt>Votes recorded</dt><dd>{data.counts.total}</dd></div>
          <div className="citizen-beta-yes"><dt>Voted yes</dt><dd>{data.counts.yes}</dd></div>
          <div className="citizen-beta-no"><dt>Voted no</dt><dd>{data.counts.no}</dd></div>
          <div><dt>Absent or excused</dt><dd>{missedVotes}</dd></div>
        </dl>

        {countedVotes ? (
          <div className="citizen-beta-split" aria-label={`${data.counts.yes} yes votes and ${data.counts.no} no votes`}>
            <div className="citizen-beta-split-labels">
              <span><strong>{yesPercent}%</strong> Yes</span>
              <span><strong>{100 - yesPercent}%</strong> No</span>
            </div>
            <div className="citizen-beta-vote-bar" aria-hidden="true">
              <span className="citizen-beta-vote-bar-yes" style={{ width: `${yesPercent}%` }} />
              <span className="citizen-beta-vote-bar-no" style={{ width: `${100 - yesPercent}%` }} />
            </div>
          </div>
        ) : null}

        {data.coverage.unattributed_roll_calls ? (
          <p className="source-note"><AlertTriangle size={17} aria-hidden="true" />
            {data.coverage.unattributed_roll_calls} {data.coverage.unattributed_roll_calls === 1 ? "roll call is" : "roll calls are"} excluded because Wyoming published a tally without member names.
          </p>
        ) : null}
      </section>

      <section className="citizen-beta-section" aria-labelledby="majority-title">
        <div className="citizen-beta-section-heading">
          <div><p className="eyebrow">Recent pattern</p><h2 id="majority-title">Did {legislator.name} usually vote with the {chamberName}?</h2></div>
          <p>{earliestRecentDate && latestRecentDate ? `${earliestRecentDate} to ${latestRecentDate}` : "Latest stored votes"}</p>
        </div>

        <dl className="citizen-beta-patterns">
          <div><dt><UsersRound size={18} aria-hidden="true" /> With the majority</dt><dd>{withMajority}</dd></div>
          <div><dt><Vote size={18} aria-hidden="true" /> Against the majority</dt><dd>{againstMajority}</dd></div>
          <div><dt><CheckCircle2 size={18} aria-hidden="true" /> Close {chamberName} votes</dt><dd>{closeVoteCount}</dd></div>
        </dl>
        <p className="citizen-beta-context">
          Based on {majorityVotes.length} recent {chamberName} floor votes with a clear yes-or-no majority. This describes the vote, not whether it was good or bad.
        </p>
      </section>

      <section className="citizen-beta-section" aria-labelledby="reasons-title">
        <div className="citizen-beta-section-heading">
          <div><p className="eyebrow">In their own words</p><h2 id="reasons-title">When {legislator.name} explained why</h2></div>
          <p>{publishedReasons.length} published {publishedReasons.length === 1 ? "reason" : "reasons"} found for {data.selected_year ?? "the stored years"}.</p>
        </div>

        {publishedReasons.length ? (
          <div className="citizen-beta-reason-grid">
            {publishedReasons.map(({ bill, explanation }) => (
              <article className="citizen-beta-reason" key={`${bill.year}-${bill.bill_num}-${explanation.roll_call_key}`}>
                <div className="citizen-beta-reason-top">
                  <div>
                    <span>{bill.bill_num}</span>
                    <h3><Link href={billHref(bill)}>{bill.catch_title || bill.plain_language_title || "Wyoming bill"}</Link></h3>
                  </div>
                  <span className={`vote-badge vote-${explanation.vote}`}><Vote size={15} aria-hidden="true" /> Voted {voteLabels[explanation.vote]}</span>
                </div>
                <div className="citizen-beta-quote">
                  <MessageSquareQuote size={22} aria-hidden="true" />
                  <p>{explanation.reason}</p>
                </div>
                <a href={explanation.source.url} target="_blank" rel="noreferrer">
                  Watch the original statement <ArrowUpRight size={16} aria-hidden="true" />
                </a>
              </article>
            ))}
          </div>
        ) : (
          <div className="citizen-profile-empty">
            <MessageSquareQuote size={22} aria-hidden="true" />
            <div><h3>Couldn&apos;t find a published reason</h3><p>No reason is guessed or added without a source.</p></div>
          </div>
        )}
      </section>

      <section className="citizen-beta-section" aria-labelledby="close-votes-title">
        <div className="citizen-beta-section-heading">
          <div><p className="eyebrow">Worth a look</p><h2 id="close-votes-title">Close votes where {legislator.name} was in the minority</h2></div>
          <p>These {chamberName} votes were decided by two votes or fewer.</p>
        </div>

        {closeVotes.length ? (
          <div className="citizen-beta-close-list">
            {closeVotes.map((vote) => (
              <article className="citizen-beta-close-row" key={voteKey(vote)}>
                <CalendarDays size={19} aria-hidden="true" />
                <div>
                  <span>{vote.bill_num} · {formatBillDate(vote.vote_date) || vote.year}</span>
                  <h3><Link href={vote.bill_href}>{vote.catch_title || vote.bill_title || "Wyoming bill"}</Link></h3>
                  <p>{chamberName} tally: {vote.yes_count} yes, {vote.no_count} no</p>
                </div>
                <div className="citizen-beta-close-result">
                  <span className={`vote-badge vote-${vote.vote_position}`}><Vote size={15} aria-hidden="true" /> Voted {voteLabels[vote.vote_position]}</span>
                  <p>Couldn&apos;t find a published reason</p>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="citizen-profile-empty"><CheckCircle2 size={22} aria-hidden="true" /><p>No close minority votes appear in the recent record.</p></div>
        )}
      </section>

      <section className="citizen-beta-section" aria-labelledby="recent-votes-title">
        <div className="citizen-beta-section-heading">
          <div><p className="eyebrow">Bill by bill</p><h2 id="recent-votes-title">Recent votes</h2></div>
          <p>The latest {displayedVotes.length} votes are available below.</p>
        </div>

        <details className="citizen-beta-vote-details">
          <summary><span>Show the {displayedVotes.length} latest votes</span><ChevronDown size={19} aria-hidden="true" /></summary>
          <div className="vote-record-list">
            {displayedVotes.map((vote) => (
              <article className="vote-record-row" key={voteKey(vote)}>
                <CalendarDays size={18} aria-hidden="true" />
                <div className="vote-record-copy">
                  <div className="vote-record-meta"><time>{formatBillDate(vote.vote_date) || vote.year}</time></div>
                  <h3><Link href={vote.bill_href}>{vote.bill_num}: {vote.catch_title || vote.bill_title || "Wyoming bill"}</Link></h3>
                  <p>{vote.action || "Official roll call"}</p>
                </div>
                <span className={`vote-badge vote-${vote.vote_position}`}><Vote size={15} aria-hidden="true" /> {voteLabels[vote.vote_position]}</span>
              </article>
            ))}
          </div>
        </details>
      </section>

      <p className="citizen-beta-source-note">
        <CheckCircle2 size={17} aria-hidden="true" /> Vote totals come from official Wyoming roll calls. Published reasons link to the original floor video.
      </p>
    </main>
  );
}
