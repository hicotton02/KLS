import Link from "next/link";
import { AlertTriangle, ArrowLeft, CalendarDays, CheckCircle2, ExternalLink, FileText, Scale } from "lucide-react";
import { notFound } from "next/navigation";
import { getBillDetail, type Interpretation } from "../../../../../lib/kls";

type RouteParams = Promise<{ slug: string; year: string; billNum: string }>;
type SearchParams = Promise<Record<string, string | string[] | undefined>>;

function first(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

function items(value: string[] | undefined) {
  return value?.filter(Boolean) ?? [];
}

function outcomeClass(outcome: string | null) {
  return ["active", "passed", "failed", "replaced"].includes(outcome ?? "") ? outcome : "active";
}

export default async function BillPage({ params, searchParams }: { params: RouteParams; searchParams: SearchParams }) {
  const { slug, year, billNum } = await params;
  const query = await searchParams;
  const data = await getBillDetail(slug, year, billNum, first(query.special_session));
  if (!data) notFound();

  const interpretation = data.interpretation;
  const sourceLinks = Object.entries(data.official_links).filter((entry): entry is [string, string] => Boolean(entry[1]));
  const linkLabels: Record<string, string> = {
    official_page: "Official bill page",
    introduced: "Introduced text",
    digest: "Official digest",
    summary: "Official summary",
    current_version: "Current bill text",
  };

  return (
    <main className="page-width page-main bill-page">
      <Link className="back-link" href={`/area/${slug}`}><ArrowLeft size={17} aria-hidden="true" /> Back to {data.jurisdiction.name}</Link>

      <header className="bill-header">
        <div>
          <div className="bill-kicker">
            <span>{data.jurisdiction.name}</span><span>{data.bill.year}</span>
            <span className={`status status-${outcomeClass(data.bill.outcome)}`}>{data.bill.status_label ?? "Status pending"}</span>
          </div>
          <h1>{data.bill.bill_num}</h1>
          <p className="bill-subtitle">{interpretation.plain_language_title || data.bill.catch_title || data.bill.bill_title}</p>
        </div>
        <div className="model-stamp">
          <Scale size={20} aria-hidden="true" />
          <span><strong>Plain-English interpretation</strong><small>{interpretation.generator_model || data.interpretation_model}</small></span>
        </div>
      </header>

      <section className="summary-band" aria-labelledby="summary-title">
        <div className="summary-main">
          <p className="eyebrow">In one sentence</p>
          <h2 id="summary-title">{interpretation.one_sentence_summary || data.bill.summary || "A plain-English summary is not available yet."}</h2>
          {items(interpretation.what_it_does).length ? (
            <div className="detail-block">
              <h3>What it does</h3>
              <ul className="check-list">{items(interpretation.what_it_does).map((item) => <li key={item}><CheckCircle2 size={18} aria-hidden="true" /><span>{item}</span></li>)}</ul>
            </div>
          ) : null}
        </div>
        <aside className="bill-facts" aria-label="Bill facts">
          <h2>Bill facts</h2>
          <dl>
            <div><dt>Sponsor</dt><dd>{data.bill.sponsor || "Not listed"}</dd></div>
            <div><dt>Last action</dt><dd>{data.bill.last_action || "Not listed"}</dd></div>
            <div><dt>Last action date</dt><dd>{data.bill.last_action_date || "Not listed"}</dd></div>
            {data.bill.effective_date ? <div><dt>Effective date</dt><dd>{data.bill.effective_date}</dd></div> : null}
          </dl>
        </aside>
      </section>

      <section className="detail-columns">
        <div className="detail-section">
          <h2>Who it affects</h2>
          {items(interpretation.who_it_affects).length ? <ul>{items(interpretation.who_it_affects).map((item) => <li key={item}>{item}</li>)}</ul> : <p>Not clearly identified in the stored source.</p>}
        </div>
        <div className="detail-section">
          <h2>Limits and unknowns</h2>
          {items(interpretation.limits_and_unknowns).length ? <ul className="warning-list">{items(interpretation.limits_and_unknowns).map((item) => <li key={item}><AlertTriangle size={17} aria-hidden="true" /><span>{item}</span></li>)}</ul> : <p>No additional limitations are listed.</p>}
        </div>
      </section>

      {interpretation.terms_to_know?.length ? (
        <section className="content-section terms-section" aria-labelledby="terms-title">
          <div className="section-heading"><div><p className="eyebrow">Plain language</p><h2 id="terms-title">Terms to know</h2></div></div>
          <dl className="term-grid">{interpretation.terms_to_know.map((term) => <div key={term.term}><dt>{term.term}</dt><dd>{term.meaning}</dd></div>)}</dl>
        </section>
      ) : null}

      <section className="content-section" aria-labelledby="sources-title">
        <div className="section-heading"><div><p className="eyebrow">Official record</p><h2 id="sources-title">Sources</h2></div><span className="trust-note"><CheckCircle2 size={17} aria-hidden="true" /> {interpretation.fact_check_status === "validated" ? "Validated" : "Source attached"}</span></div>
        <div className="source-grid">
          {sourceLinks.map(([key, url]) => <a href={url} target="_blank" rel="noreferrer" key={key}><FileText size={19} aria-hidden="true" /><span>{linkLabels[key] ?? key}</span><ExternalLink size={15} aria-hidden="true" /></a>)}
          <a href={`https://www.keepinglawsimple.org${data.bill.legacy_href}`} target="_blank" rel="noreferrer"><FileText size={19} aria-hidden="true" /><span>Stored full record</span><ExternalLink size={15} aria-hidden="true" /></a>
        </div>
        {data.bill.official_summary_text ? <div className="official-text"><h3>Official summary</h3><p>{data.bill.official_summary_text}</p></div> : null}
      </section>

      {data.relationships.length ? (
        <section className="content-section" aria-labelledby="related-title">
          <div className="section-heading"><div><p className="eyebrow">Read together</p><h2 id="related-title">Related bills</h2></div></div>
          <div className="related-list">{data.relationships.map((relationship) => <article key={relationship.peer.bill_num}><Link href={`/area/${slug}/bill/${relationship.peer.year}/${encodeURIComponent(relationship.peer.bill_num)}`}>{relationship.peer.bill_num}</Link><strong>{relationship.peer.plain_language_title || relationship.peer.catch_title}</strong>{relationship.pair_summaries?.[0] ? <p>{relationship.pair_summaries[0]}</p> : null}</article>)}</div>
        </section>
      ) : null}

      {data.actions.length ? (
        <section className="content-section" aria-labelledby="history-title">
          <div className="section-heading"><div><p className="eyebrow">Official activity</p><h2 id="history-title">Bill history</h2></div></div>
          <ol className="timeline">{data.actions.slice(0, 20).map((action, index) => <li key={`${action.statusDate}-${index}`}><CalendarDays size={17} aria-hidden="true" /><div><time>{action.statusDate?.slice(0, 10) || "Date unavailable"}</time><strong>{action.statusMessage || "Official action"}</strong>{action.location ? <span>{action.location}</span> : null}</div></li>)}</ol>
        </section>
      ) : null}

      {data.amendments.length ? (
        <section className="content-section" aria-labelledby="amendments-title">
          <div className="section-heading"><div><p className="eyebrow">Changes</p><h2 id="amendments-title">Amendments</h2></div><p>{data.amendments.length} stored</p></div>
          <div className="amendment-list">{data.amendments.map((amendment, index) => {
            const summary = amendment.interpretation_json as Interpretation | undefined;
            return <article key={String(amendment.amendment_number ?? index)}><strong>{String(amendment.amendment_number ?? `Amendment ${index + 1}`)}</strong>{summary?.one_sentence_summary ? <p>{summary.one_sentence_summary}</p> : <p>Official amendment record.</p>}</article>;
          })}</div>
        </section>
      ) : null}
    </main>
  );
}
