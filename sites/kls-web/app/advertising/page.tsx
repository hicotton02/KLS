import type { Metadata } from "next";
import Link from "next/link";
import { InfoPage } from "../components/InfoPage";

export const metadata: Metadata = {
  title: "Advertising Policy",
  description: "How Keeping Law Simple keeps advertising separate from legislative coverage.",
  alternates: { canonical: "/advertising" },
};

export default function AdvertisingPage() {
  return (
    <InfoPage
      eyebrow="Advertising"
      title="Ads may help pay the hosting bill. They do not buy coverage."
      intro="Keeping Law Simple may use clearly labeled ads and business sponsorships to help cover operating costs."
    >
      <section>
        <h2>Hard line between ads and content</h2>
        <p>Advertisers cannot choose which bills we cover, change a summary, remove a vote, or influence a correction. Payment does not improve how anyone is described.</p>
      </section>
      <section>
        <h2>What we will not accept</h2>
        <p>We do not accept ads from candidates, campaigns, political parties, political action committees, or issue-advocacy groups. We may reject any ad that could confuse readers or weaken trust in the public record.</p>
      </section>
      <section>
        <h2>Clear placement</h2>
        <p>Ads are labeled “Advertisement.” They are not placed inside voting totals, public statements, or official-source links. We never ask readers to click an ad to support the site.</p>
      </section>
      <section>
        <h2>Advertising questions</h2>
        <p>Businesses can use the <Link href="/contact?type=advertising">contact form</Link>. An inquiry is not a promise that an ad will be accepted.</p>
      </section>
    </InfoPage>
  );
}
