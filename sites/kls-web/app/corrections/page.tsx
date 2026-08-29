import type { Metadata } from "next";
import Link from "next/link";
import { InfoPage } from "../components/InfoPage";

export const metadata: Metadata = {
  title: "Corrections",
  description: "Report a possible error in a Keeping Law Simple bill summary, vote, date, or source link.",
  alternates: { canonical: "/corrections" },
};

export default function CorrectionsPage() {
  return (
    <InfoPage
      eyebrow="Corrections"
      title="Found a mistake? Point us to it."
      intro="A bill number, page link, and the official source are usually enough for us to check a possible error."
    >
      <section>
        <h2>What to send</h2>
        <p>Include the page address, what looks wrong, and a source link when you have one. Do not send private records or sensitive personal information.</p>
      </section>
      <section>
        <h2>What happens next</h2>
        <p>We compare the report with the official record. Supported corrections are made as soon as practical. Disagreement with a vote or policy position by itself is not a factual correction.</p>
      </section>
      <p><Link className="primary-button" href="/contact?type=correction">Report a correction</Link></p>
    </InfoPage>
  );
}
