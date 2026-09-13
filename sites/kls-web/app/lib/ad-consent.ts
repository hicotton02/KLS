export type ConsentData = {
  cmpStatus?: string;
  eventStatus?: string;
  gdprApplies?: boolean;
  purpose?: { consents?: Record<number, boolean> };
  vendor?: { consents?: Record<number, boolean> };
};

type AdState = { allowed: boolean; disabled: boolean; gdprApplies?: boolean };
type AdQueue = Array<Record<string, unknown>> & { pauseAdRequests?: number; requestNonPersonalizedAds?: number };
type GoogleFc = {
  callbackQueue: Array<Record<string, () => void>>;
  showRevocationMessage?: () => void;
  usstatesoptout?: { getInitialUsStatesOptOutStatus?: () => number };
};
type AdsWindow = Window & {
  adsbygoogle?: AdQueue;
  googlefc?: GoogleFc;
  __tcfapi?: (command: string, version: number, callback: (data: ConsentData | null, success: boolean) => void) => void;
  __gpp?: (command: string, callback: (data: { eventName?: string } | null, success: boolean) => void) => void;
  __klsAds?: ReturnType<typeof createAdController>;
};

// Google validates the complete TC string, including additional legal bases.
// This extra gate never requests an ad without device-storage consent where TCF applies.
export function consentAllowsAds(data: ConsentData | null, success: boolean): boolean {
  if (!success || data?.cmpStatus !== "loaded") return false;
  if (data.gdprApplies === false) return true;
  return data.gdprApplies === true && ["tcloaded", "useractioncomplete"].includes(data.eventStatus || "") &&
    data.purpose?.consents?.[1] === true && data.vendor?.consents?.[755] === true;
}

const STORAGE_KEY = "kls-ads-choice";

function createAdController(client: string) {
  const current = window as AdsWindow;
  const listeners = new Set<(state: AdState) => void>();
  let disabled = false;
  try { disabled = localStorage.getItem(STORAGE_KEY) === "denied"; } catch { /* Storage is optional. */ }
  const gpc = (navigator as Navigator & { globalPrivacyControl?: boolean }).globalPrivacyControl === true;
  let state: AdState = { allowed: false, disabled: disabled || gpc };
  let euAllowed = false;
  let usAllowed = false;
  let failed = false;
  let started = false;
  let eligible = false;
  function publish() {
    state = { ...state, allowed: !failed && !state.disabled && euAllowed && usAllowed };
    current.adsbygoogle = current.adsbygoogle || [];
    current.adsbygoogle.pauseAdRequests = state.allowed ? 0 : 1;
    current.adsbygoogle.requestNonPersonalizedAds = 1;
    for (const listener of listeners) listener(state);
  }
  function start() {
    if (started || state.disabled || !eligible) return;
    started = true;
    current.adsbygoogle = current.adsbygoogle || [];
    current.adsbygoogle.pauseAdRequests = 1;
    current.adsbygoogle.requestNonPersonalizedAds = 1;
    current.googlefc = current.googlefc || { callbackQueue: [] };
    current.googlefc.callbackQueue = current.googlefc.callbackQueue || [];
    current.googlefc.usstatesoptout = current.googlefc.usstatesoptout || {};
    current.googlefc.callbackQueue.push({ CONSENT_API_READY: () => {
      if (new URL(current.location.href).searchParams.get("ad_choices") === "1") {
        current.googlefc?.showRevocationMessage?.();
      }
      current.__tcfapi?.("addEventListener", 2, (data, success) => {
        euAllowed = consentAllowsAds(data, success);
        state = { ...state, gdprApplies: data?.gdprApplies };
        publish();
      });
    } });
    current.googlefc.callbackQueue.push({ INITIAL_US_STATES_OPT_OUT_DATA_READY: () => {
      const status = current.googlefc?.usstatesoptout?.getInitialUsStatesOptOutStatus?.();
      usAllowed = status === 1 || status === 2;
      publish();
      current.__gpp?.("addEventListener", (data, success) => {
        // Google's initial status is a snapshot. Stop for later US consent changes;
        // a fresh page load will read the updated choice before requesting ads again.
        if (!success || data?.eventName === "sectionChange") {
          usAllowed = false;
          publish();
        }
      });
    } });
    const script = document.createElement("script");
    script.id = "kls-adsense-script";
    script.async = true;
    script.crossOrigin = "anonymous";
    script.src = `https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=${encodeURIComponent(client)}`;
    script.onerror = () => { failed = true; publish(); };
    document.head.appendChild(script);
  }
  window.addEventListener("storage", event => {
    if (event.key !== STORAGE_KEY && event.key !== null) return;
    state = { ...state, disabled: gpc || event.newValue === "denied" };
    publish(); start();
  });
  return {
    subscribe(listener: (value: AdState) => void, loadSdk = true) {
      eligible = eligible || loadSdk;
      listeners.add(listener); listener(state); start();
      return () => { listeners.delete(listener); };
    },
    setDisabled(value: boolean) {
      try { localStorage.setItem(STORAGE_KEY, value ? "denied" : "allowed"); } catch { /* Honor the choice in memory. */ }
      state = { ...state, disabled: value || gpc };
      publish(); start();
    },
    request(element: HTMLElement) {
      if (!state.allowed || element.dataset.klsRequested === "true" || element.hasAttribute("data-adsbygoogle-status")) return;
      element.dataset.klsRequested = "true";
      current.adsbygoogle?.push({});
    },
  };
}

export function adsController(client: string) {
  const current = window as AdsWindow;
  current.__klsAds = current.__klsAds || createAdController(client);
  return current.__klsAds;
}
