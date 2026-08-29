import type { Metadata } from "next";
import { Geist } from "next/font/google";
import Link from "next/link";
import { AnalyticsConsent } from "./components/AnalyticsConsent";
import { SiteHeader } from "./components/SiteHeader";
import { absoluteUrl, SITE_DESCRIPTION, SITE_NAME, SITE_ORIGIN } from "./lib/site";
import "./globals.css";

const geist = Geist({
  variable: "--font-geist",
  subsets: ["latin"],
});

const socialImage = absoluteUrl("/og.png");

export const metadata: Metadata = {
  metadataBase: SITE_ORIGIN,
  title: {
    default: SITE_NAME,
    template: `%s | ${SITE_NAME}`,
  },
  description: SITE_DESCRIPTION,
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
  openGraph: {
    type: "website",
    url: SITE_ORIGIN,
    siteName: SITE_NAME,
    title: SITE_NAME,
    description: SITE_DESCRIPTION,
    images: [{ url: socialImage, width: 1731, height: 909, alt: SITE_NAME }],
  },
  twitter: {
    card: "summary_large_image",
    title: SITE_NAME,
    description: SITE_DESCRIPTION,
    images: [socialImage],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const measurementId = process.env.KLS_GOOGLE_ANALYTICS_ID?.trim() || "";

  return (
    <html lang="en">
      <body className={geist.variable}>
        <SiteHeader />
        {children}
        <footer className="site-footer">
          <div className="page-width footer-layout">
            <div className="footer-about">
              <strong>Keeping Law Simple</strong>
              <p>Neutral, readable summaries tied to the official record.</p>
            </div>
            <nav className="footer-links" aria-label="Site information">
              <Link href="/about">About</Link>
              <Link href="/editorial-standards">How we work</Link>
              <Link href="/corrections">Corrections</Link>
              <Link href="/advertising">Advertising</Link>
              <Link href="/privacy">Privacy</Link>
              <Link href="/terms">Terms</Link>
              <Link href="/contact">Contact</Link>
            </nav>
            <div className="footer-legal">
              <p>Official text always wins.</p>
              {measurementId ? <AnalyticsConsent measurementId={measurementId} /> : null}
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}
