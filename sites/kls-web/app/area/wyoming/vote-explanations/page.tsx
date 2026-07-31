import type { Metadata } from "next";
import Link from "next/link";
import {
  ArrowLeft,
  CalendarDays,
  ExternalLink,
  FileCheck2,
  PlayCircle,
  SearchX,
  ShieldCheck,
} from "lucide-react";
import { wyomingVoteExplanations } from "../../../lib/vote-explanations";

export const metadata: Metadata = {
  title: "Why Wyoming Lawmakers Voted",
  description: "Plain-language reasons Wyoming lawmakers gave for their votes, linked to public sources.",
};

export default function WyomingVoteExplanationsPage() {
  const explanations = wyomingVoteExplanations;

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
              When a lawmaker explains a vote in public, we show the reason and link to the
              exact moment. When we cannot find one, we say that plainly.
            </p>
          </div>
          <div className="beta-scan" aria-label={`Latest video checked ${explanations.latestVideoChecked}`}>
            <CalendarDays size={22} strokeWidth={1.8} aria-hidden="true" />
            <div>
              <span>Latest video checked</span>
              <strong>{explanations.latestVideoChecked}</strong>
            </div>
          </div>
        </section>

        <section className="beta-bill-band" aria-labelledby="explanation-bill-title">
          <div>
            <p className="eyebrow">{explanations.bill.chamber}</p>
            <h2 id="explanation-bill-title">{explanations.bill.number}: {explanations.bill.title}</h2>
            <p>{explanations.bill.result} on {explanations.bill.voteDate}.</p>
          </div>
          <a href={explanations.bill.officialUrl} target="_blank" rel="noreferrer">
            Official bill and vote <ExternalLink size={16} aria-hidden="true" />
          </a>
        </section>

        <section className="beta-explanations" aria-labelledby="statements-title">
          <div className="beta-section-heading">
            <div>
              <p className="eyebrow">Public statements</p>
              <h2 id="statements-title">What lawmakers said</h2>
            </div>
            <p>Reasons are paraphrased from the linked public statements.</p>
          </div>

          <div className="explanation-list">
            {explanations.examples.map((example) => {
              const reasonFound = example.reasonStatus === "found";
              return (
                <article className={`vote-explanation ${reasonFound ? "" : "explanation-missing"}`} key={example.lawmaker}>
                  <header className="explanation-header">
                    <div>
                      <h3>{example.lawmaker}</h3>
                      <p>{example.party} · {example.district}</p>
                    </div>
                    <span className={`vote-badge vote-${example.vote.toLowerCase()}`}>
                      Voted {example.vote}
                    </span>
                  </header>

                  <div className="explanation-body">
                    <div>
                      <p className="explanation-label">
                        {reasonFound ? "Why they voted this way" : "What we found"}
                      </p>
                      <p className="explanation-reason">{example.reason}</p>
                    </div>
                    <div className="explanation-source">
                      {reasonFound ? (
                        <FileCheck2 size={20} aria-hidden="true" />
                      ) : (
                        <SearchX size={20} aria-hidden="true" />
                      )}
                      <div>
                        <span>{reasonFound ? "Public statement found" : "Published reason not found"}</span>
                        <small>{example.sourceLabel}</small>
                        <a href={example.sourceUrl} target="_blank" rel="noreferrer">
                          <PlayCircle size={17} aria-hidden="true" /> {example.sourceAction}
                        </a>
                      </div>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </section>

        <section className="beta-rule" aria-labelledby="source-rule-title">
          <ShieldCheck size={24} strokeWidth={1.8} aria-hidden="true" />
          <div>
            <h2 id="source-rule-title">No guessing</h2>
            <p>A vote alone does not prove a lawmaker&apos;s reason. We only show a reason when a public source supports it.</p>
          </div>
        </section>
      </div>
    </main>
  );
}
