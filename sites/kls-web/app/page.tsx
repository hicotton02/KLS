import Link from "next/link";
import { ArrowRight, Landmark, ShieldCheck } from "lucide-react";
import { BillList } from "./components/BillList";
import { SearchBox } from "./components/SearchBox";
import { StateDirectory } from "./components/StateDirectory";
import { getOverview } from "./lib/kls";

export default async function Home() {
  const overview = await getOverview();
  const states = overview.jurisdictions.filter((area) => area.kind === "state");
  const federal = overview.jurisdictions.find((area) => area.kind === "federal");
  const currentBills = overview.jurisdictions.reduce(
    (total, area) => total + (area.counts?.total ?? 0),
    0,
  );

  return (
    <main>
      <section className="intro-band">
        <div className="page-width intro-layout">
          <div className="intro-copy">
            <p className="eyebrow">Official sources. Neutral summaries.</p>
            <h1>Bills, in plain English.</h1>
            <p className="lede">
              Follow state and federal legislation without digging through legal language.
              Every explanation stays tied to the official record.
            </p>
            <SearchBox />
          </div>

          <dl className="coverage-summary" aria-label="Current coverage">
            <div>
              <dt>Coverage areas</dt>
              <dd>{overview.jurisdictions.length || 52}</dd>
            </div>
            <div>
              <dt>Current-session bills</dt>
              <dd>{currentBills.toLocaleString()}</dd>
            </div>
            <div>
              <dt>Interpretation</dt>
              <dd className="model-name">Qwen 3.5 27B</dd>
            </div>
          </dl>
        </div>
      </section>

      <section className="page-width federal-strip" aria-labelledby="federal-title">
        <div className="section-icon" aria-hidden="true">
          <Landmark size={22} strokeWidth={1.8} />
        </div>
        <div className="federal-copy">
          <p className="eyebrow">Federal</p>
          <h2 id="federal-title">Congress, without the fog.</h2>
          <p>{federal?.description ?? "Recent federal bills, status, and source-checked summaries."}</p>
        </div>
        <Link className="text-link" href="/area/federal">
          Browse Congress <ArrowRight size={18} aria-hidden="true" />
        </Link>
      </section>

      <section className="page-width content-section" id="browse" aria-labelledby="browse-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Browse</p>
            <h2 id="browse-title">Find your state</h2>
          </div>
          <p>{states.length || 51} state and district legislatures</p>
        </div>
        <StateDirectory areas={states} />
      </section>

      <section className="page-width content-section" aria-labelledby="recent-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Latest activity</p>
            <h2 id="recent-title">Recently updated bills</h2>
          </div>
          <span className="trust-note">
            <ShieldCheck size={17} aria-hidden="true" /> Source checked
          </span>
        </div>
        <BillList bills={overview.recent_bills} emptyMessage="No recent bill updates are available." />
      </section>
    </main>
  );
}
