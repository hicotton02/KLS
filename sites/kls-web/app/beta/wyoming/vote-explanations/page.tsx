import type { Metadata } from "next";
import Link from "next/link";
import { headers } from "next/headers";
import { notFound } from "next/navigation";
import {
  ArrowLeft,
  CalendarDays,
  ExternalLink,
  FileCheck2,
  PlayCircle,
  SearchX,
  ShieldCheck,
} from "lucide-react";
import { wyomingVoteExplanationBeta } from "../../../lib/vote-explanations-beta";

export const metadata: Metadata = {
  title: "Wyoming Vote Explanations Beta",
  robots: { index: false, follow: false },
};

const publicHosts = new Set(["keepinglawsimple.org", "www.keepinglawsimple.org"]);

export default async function WyomingVoteExplanationsBeta() {
  const requestHeaders = await headers();
  const host = (requestHeaders.get("x-forwarded-host") || requestHeaders.get("host") || "")
    .split(",")[0]
    .trim()
    .split(":")[0]
    .toLowerCase();

  if (publicHosts.has(host)) notFound();

  const beta = wyomingVoteExplanationBeta;

  return (
    <main className="page-main beta-page">
      <div className="page-width">
        <Link className="back-link" href="/area/wyoming">
          <ArrowLeft size={17} aria-hidden="true" /> Back to Wyoming bills
        </Link>

        <section className="beta-intro" aria-labelledby="beta-title">
          <div>
            <p className="eyebrow">Private beta</p>
            <h1 id="beta-title">Why did they vote that way?</h1>
            <p className="beta-lede">
              When a lawmaker explains a vote in public, we show the reason and link to the
              exact moment. When we cannot find one, we say that plainly.
            </p>
          </div>
          <div className="beta-scan" aria-label={`Latest video checked ${beta.latestVideoChecked}`}>
            <CalendarDays size={22} strokeWidth={1.8} aria-hidden="true" />
            <div>
              <span>Latest video checked</span>
              <strong>{beta.latestVideoChecked}</strong>
            </div>
          </div>
        </section>

        <section className="beta-bill-band" aria-labelledby="beta-bill-title">
          <div>
            <p className="eyebrow">{beta.bill.chamber}</p>
            <h2 id="beta-bill-title">{beta.bill.number}: {beta.bill.title}</h2>
            <p>{beta.bill.result} on {beta.bill.voteDate}.</p>
          </div>
          <a href={beta.bill.officialUrl} target="_blank" rel="noreferrer">
            Official bill and vote <ExternalLink size={16} aria-hidden="true" />
          </a>
        </section>

        <section className="beta-explanations" aria-labelledby="explanations-title">
          <div className="beta-section-heading">
            <div>
              <p className="eyebrow">Examples</p>
              <h2 id="explanations-title">What lawmakers said</h2>
            </div>
            <p>Reasons are paraphrased from the linked public statements.</p>
          </div>

          <div className="explanation-list">
            {beta.examples.map((example) => {
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

        <section className="beta-rule" aria-labelledby="beta-rule-title">
          <ShieldCheck size={24} strokeWidth={1.8} aria-hidden="true" />
          <div>
            <h2 id="beta-rule-title">No guessing</h2>
            <p>A vote alone does not prove a lawmaker&apos;s reason. We only show a reason when a public source supports it.</p>
          </div>
        </section>
      </div>
    </main>
  );
}
