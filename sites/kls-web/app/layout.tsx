import type { Metadata } from "next";
import { Geist } from "next/font/google";
import { headers } from "next/headers";
import { SiteHeader } from "./components/SiteHeader";
import "./globals.css";

const geist = Geist({
  variable: "--font-geist",
  subsets: ["latin"],
});

const description =
  "State and federal legislation explained in neutral, plain English with official sources attached.";

export async function generateMetadata(): Promise<Metadata> {
  const requestHeaders = await headers();
  const forwardedHost = requestHeaders.get("x-forwarded-host")?.split(",")[0]?.trim();
  const host = forwardedHost || requestHeaders.get("host") || "www.keepinglawsimple.org";
  const forwardedProtocol = requestHeaders.get("x-forwarded-proto")?.split(",")[0]?.trim();
  const protocol = forwardedProtocol || (host.startsWith("localhost") ? "http" : "https");
  const origin = new URL(`${protocol}://${host}`);
  const socialImage = new URL("/og.png", origin).toString();

  return {
    metadataBase: origin,
    title: {
      default: "Keeping Law Simple",
      template: "%s | Keeping Law Simple",
    },
    description,
    icons: {
      icon: "/favicon.svg",
      shortcut: "/favicon.svg",
    },
    openGraph: {
      type: "website",
      url: origin,
      siteName: "Keeping Law Simple",
      title: "Keeping Law Simple",
      description,
      images: [{ url: socialImage, width: 1731, height: 909, alt: "Keeping Law Simple" }],
    },
    twitter: {
      card: "summary_large_image",
      title: "Keeping Law Simple",
      description,
      images: [socialImage],
    },
  };
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={geist.variable}>
        <SiteHeader />
        {children}
        <footer className="site-footer">
          <div className="page-width footer-row">
            <p><strong>Keeping Law Simple</strong> turns official bill records into neutral, readable summaries.</p>
            <p>Official text always wins.</p>
          </div>
        </footer>
      </body>
    </html>
  );
}
