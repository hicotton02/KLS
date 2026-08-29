import type { Metadata } from "next";
import { InfoPage } from "../components/InfoPage";

export const metadata: Metadata = {
  title: "Privacy",
  description: "What Keeping Law Simple collects, why it is collected, and the choices available to visitors.",
  alternates: { canonical: "/privacy" },
};

export default function PrivacyPage() {
  return (
    <InfoPage
      eyebrow="Privacy"
      title="We collect less because less can go wrong."
      intro="This page explains the basic information Keeping Law Simple uses to run the site, measure useful pages, and answer messages. Last updated August 29, 2026."
    >
      <section>
        <h2>Basic server records</h2>
        <p>Like most websites, the server records the page requested, time, browser description, referrer, response status, and estimated general location. Full IP addresses are not stored in the site analytics table. A shortened anonymous signature is used to count repeat visits and spot automated traffic. These analytics records are normally kept for 180 days.</p>
      </section>
      <section>
        <h2>Optional Google Analytics</h2>
        <p>Google Analytics loads only after a visitor chooses “Allow analytics.” A visitor can choose “No thanks” and still use the whole site. The Privacy choices button in the footer can change that choice later.</p>
      </section>
      <section>
        <h2>Messages you send</h2>
        <p>The contact form stores the information a visitor enters so the site owner can review and answer it. Do not submit confidential legal information.</p>
      </section>
      <section>
        <h2>Advertising</h2>
        <p>Advertising cookies are currently disabled. Before personalized advertising is enabled, the site will provide the consent controls required by the advertising provider and applicable law.</p>
      </section>
      <section>
        <h2>Sharing</h2>
        <p>Visitor information is not sold. Limited information may be handled by hosting, security, analytics, or advertising providers when needed to run those services.</p>
      </section>
    </InfoPage>
  );
}
