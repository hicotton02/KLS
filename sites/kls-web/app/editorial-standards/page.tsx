import type { Metadata } from "next";
import Link from "next/link";
import { InfoPage } from "../components/InfoPage";

export const metadata: Metadata = {
  title: "How We Work",
  description: "The source, neutrality, vote-reason, and correction rules used by Keeping Law Simple.",
  alternates: { canonical: "/editorial-standards" },
};

export default function EditorialStandardsPage() {
  return (
    <InfoPage
      eyebrow="How we work"
      title="Simple words. Visible sources. No guessing."
      intro="These rules guide every bill summary and voting-reason page on Keeping Law Simple."
    >
      <section>
        <h2>Start with the official record</h2>
        <p>Bill text, legislative actions, roll calls, and government-published documents come first. Every public bill page links back to the official source when that source is available.</p>
      </section>
      <section>
        <h2>Explain without campaigning</h2>
        <p>Summaries describe what the bill says and who it may affect. They do not tell readers to support a party, lawmaker, or outcome.</p>
      </section>
      <section>
        <h2>A vote does not prove a reason</h2>
        <p>We show a lawmaker&apos;s reason only when it is supported by a public statement, recording, transcript, or published report. Otherwise the page says we could not find a published reason.</p>
      </section>
      <section>
        <h2>Show limits clearly</h2>
        <p>Missing text, unclear wording, and incomplete records should be stated plainly. We do not fill gaps with a guess.</p>
      </section>
      <section>
        <h2>Fix supported mistakes</h2>
        <p>Correction requests are checked against the source record. Supported corrections are made without charging anyone or changing coverage for an advertiser. <Link href="/corrections">Report a problem</Link>.</p>
      </section>
    </InfoPage>
  );
}
