import type { Metadata } from "next";
import Link from "next/link";
import { InfoPage } from "../components/InfoPage";

export const metadata: Metadata = {
  title: "About",
  description: "Why Keeping Law Simple exists and how it helps regular people read state and federal legislation.",
  alternates: { canonical: "/about" },
};

export default function AboutPage() {
  return (
    <InfoPage
      eyebrow="About"
      title="Law should not require a law degree to follow."
      intro="Keeping Law Simple helps regular people understand what a bill does, where it stands, and where the information came from."
    >
      <section>
        <h2>What you will find</h2>
        <p>Each bill page keeps the plain-English summary beside its status, important dates, and official source links. Wyoming pages also show recorded votes and published reasons when a lawmaker explained a vote in public.</p>
      </section>
      <section>
        <h2>What we do not do</h2>
        <p>We do not tell you what to believe, grade lawmakers, or replace legal advice. A summary can miss context. The linked official record is always the final word.</p>
      </section>
      <section>
        <h2>See something wrong?</h2>
        <p>Good public information needs a clear way to fix mistakes. Read our <Link href="/editorial-standards">working standards</Link> or <Link href="/corrections">report a correction</Link>.</p>
      </section>
    </InfoPage>
  );
}
