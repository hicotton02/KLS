"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const STORAGE_KEY = "kls-analytics-consent";
type ConsentChoice = "granted" | "denied";
type AnalyticsWindow = Window & {
  dataLayer?: unknown[];
  gtag?: (...args: unknown[]) => void;
  __klsAnalyticsLoaded?: boolean;
};

function analyticsWindow() {
  return window as AnalyticsWindow;
}

function setGoogleConsent(choice: ConsentChoice) {
  const current = analyticsWindow();
  current.dataLayer = current.dataLayer || [];
  current.gtag = current.gtag || function gtag(...args: unknown[]) { current.dataLayer?.push(args); };
  current.gtag("consent", "update", {
    analytics_storage: choice,
    ad_storage: "denied",
    ad_user_data: "denied",
    ad_personalization: "denied",
  });
}

function loadAnalytics(measurementId: string) {
  const current = analyticsWindow();
  if (current.__klsAnalyticsLoaded) return;
  current.__klsAnalyticsLoaded = true;
  current.dataLayer = current.dataLayer || [];
  current.gtag = current.gtag || function gtag(...args: unknown[]) { current.dataLayer?.push(args); };
  current.gtag("consent", "default", {
    analytics_storage: "granted",
    ad_storage: "denied",
    ad_user_data: "denied",
    ad_personalization: "denied",
  });
  current.gtag("js", new Date());
  current.gtag("config", measurementId, { send_page_view: false });

  const script = document.createElement("script");
  script.async = true;
  script.src = `https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(measurementId)}`;
  document.head.appendChild(script);
}

export function AnalyticsConsent({ measurementId }: { measurementId: string }) {
  const pathname = usePathname();
  const [choice, setChoice] = useState<ConsentChoice | null | undefined>(undefined);

  useEffect(() => {
    let storedChoice: ConsentChoice | null = null;
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      storedChoice = stored === "granted" || stored === "denied" ? stored : null;
    } catch {}
    const timer = window.setTimeout(() => setChoice(storedChoice), 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (choice !== "granted" || !measurementId) return;
    loadAnalytics(measurementId);
    analyticsWindow().gtag?.("event", "page_view", {
      page_location: window.location.href,
      page_path: pathname,
      page_title: document.title,
    });
  }, [choice, measurementId, pathname]);

  function saveChoice(nextChoice: ConsentChoice) {
    try {
      window.localStorage.setItem(STORAGE_KEY, nextChoice);
    } catch {
      // The current page still honors the choice when storage is unavailable.
    }
    setGoogleConsent(nextChoice);
    setChoice(nextChoice);
  }

  if (choice === undefined) return null;

  return (
    <>
      <button className="privacy-choice-button" type="button" onClick={() => setChoice(null)}>
        Privacy choices
      </button>
      {choice === null ? (
        <section className="consent-banner" role="dialog" aria-label="Analytics privacy choice">
          <div>
            <strong>Help us improve the site?</strong>
            <p>Allow anonymous Google Analytics so we can see which pages help people. Advertising cookies stay off.</p>
            <Link href="/privacy">Read the privacy policy</Link>
          </div>
          <div className="consent-actions">
            <button type="button" onClick={() => saveChoice("denied")}>No thanks</button>
            <button className="consent-accept" type="button" onClick={() => saveChoice("granted")}>Allow analytics</button>
          </div>
        </section>
      ) : null}
    </>
  );
}
