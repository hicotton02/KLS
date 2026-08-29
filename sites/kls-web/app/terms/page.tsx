import type { Metadata } from "next";
import { InfoPage } from "../components/InfoPage";

export const metadata: Metadata = {
  title: "Terms of Use",
  description: "Basic terms for using Keeping Law Simple and its plain-English legislative summaries.",
  alternates: { canonical: "/terms" },
};

export default function TermsPage() {
  return (
    <InfoPage
      eyebrow="Terms"
      title="Use the summary. Check the source."
      intro="Keeping Law Simple is a public information tool, not a law firm or government office. Last updated August 29, 2026."
    >
      <section>
        <h2>Not legal advice</h2>
        <p>Information on this site is general education. It is not legal, tax, election, or financial advice and does not create a professional relationship.</p>
      </section>
      <section>
        <h2>Official records control</h2>
        <p>Summaries may be incomplete or wrong. Dates and statuses can change. Always check the linked legislature or Congress source before relying on a bill record.</p>
      </section>
      <section>
        <h2>Fair use of the service</h2>
        <p>Do not attack the service, overload it with automated requests, bypass access controls, submit unlawful material, or use the site to mislead people about an official record.</p>
      </section>
      <section>
        <h2>Outside links and availability</h2>
        <p>Outside websites control their own content and availability. Keeping Law Simple may change, pause, or remove features as sources and operating needs change.</p>
      </section>
    </InfoPage>
  );
}
