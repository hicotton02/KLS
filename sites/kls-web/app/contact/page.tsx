import type { Metadata } from "next";
import { ContactForm } from "../components/ContactForm";
import { InfoPage } from "../components/InfoPage";

export const metadata: Metadata = {
  title: "Contact",
  description: "Contact Keeping Law Simple about a correction, general question, or advertising inquiry.",
  alternates: { canonical: "/contact" },
  robots: { index: true, follow: true },
};

type SearchParams = Promise<Record<string, string | string[] | undefined>>;

export default async function ContactPage({ searchParams }: { searchParams: SearchParams }) {
  const query = await searchParams;
  const rawType = Array.isArray(query.type) ? query.type[0] : query.type;
  const initialKind = rawType === "correction" || rawType === "advertising" ? rawType : "general";

  return (
    <InfoPage
      eyebrow="Contact"
      title="Send a clear note."
      intro="Use this form for a possible correction, a general question, or an advertising inquiry."
    >
      <ContactForm initialKind={initialKind} />
    </InfoPage>
  );
}
