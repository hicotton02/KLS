import assert from "node:assert/strict";
import test from "node:test";
import { adsController, consentAllowsAds } from "../app/lib/ad-consent.ts";
import { displayAdConfig } from "../app/lib/adsense.ts";

const accepted = { cmpStatus: "loaded", eventStatus: "useractioncomplete", gdprApplies: true,
  purpose: { consents: { 1: true } }, vendor: { consents: { 755: true } } };

test("consent gate fails closed and respects a decline", () => {
  assert.equal(consentAllowsAds(null, true), false);
  assert.equal(consentAllowsAds({}, true), false);
  assert.equal(consentAllowsAds(accepted, false), false);
  assert.equal(consentAllowsAds({ ...accepted, cmpStatus: "error" }, true), false);
  assert.equal(consentAllowsAds({ ...accepted, eventStatus: "cmpuishown" }, true), false);
  assert.equal(consentAllowsAds({ ...accepted, purpose: { consents: { 1: false } } }, true), false);
  assert.equal(consentAllowsAds({ ...accepted, vendor: { consents: { 755: false } } }, true), false);
  assert.equal(consentAllowsAds(accepted, true), true);
  assert.equal(consentAllowsAds({ cmpStatus: "loaded", gdprApplies: false }, true), true);
});

function browser(t, { gpc = false, stored = null } = {}) {
  const original = new Map(["window", "navigator", "document", "localStorage"].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const scripts = [];
  const events = {};
  const storage = new Map(stored ? [["kls-ads-choice", stored]] : []);
  const win = { location: { href: "https://www.keepinglawsimple.org/" }, addEventListener: (name, fn) => { events[name] = fn; } };
  for (const [key, value] of Object.entries({ window: win, navigator: { globalPrivacyControl: gpc },
    localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    document: { createElement: () => ({}), head: { appendChild: value => scripts.push(value) } } })) {
    Object.defineProperty(globalThis, key, { configurable: true, value });
  }
  t.after(() => { for (const [key, descriptor] of original) {
    if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
  } });
  let tcf;
  let usStatus = 1;
  function ready() {
    win.__tcfapi = (_, version, callback) => { assert.equal(version, 2); tcf = callback; };
    win.googlefc.usstatesoptout = { getInitialUsStatesOptOutStatus: () => usStatus };
    for (const entry of win.googlefc.callbackQueue) for (const callback of Object.values(entry)) callback();
  }
  return { win, scripts, events, ready, consent: data => tcf(data, true),
    setUsStatus(value) { usStatus = value; } };
}

test("SDK is paused until both consent frameworks are ready; revocation stops ads", t => {
  const b = browser(t);
  const controller = adsController("ca-pub-4907492213987533");
  let state;
  const unsubscribe = controller.subscribe(value => { state = value; });
  assert.equal(b.scripts.length, 1);
  assert.equal(b.win.adsbygoogle.pauseAdRequests, 1);
  assert.equal(b.win.adsbygoogle.requestNonPersonalizedAds, 1);
  assert.equal(adsController("ca-pub-4907492213987533"), controller);
  b.ready();
  assert.equal(state.allowed, false);
  b.consent(accepted);
  assert.equal(state.allowed, true);
  const element = { dataset: {}, hasAttribute: () => false };
  controller.request(element); controller.request(element);
  assert.equal(b.win.adsbygoogle.length, 1);
  b.consent({ ...accepted, purpose: { consents: {} } });
  assert.equal(state.allowed, false);
  assert.equal(b.win.adsbygoogle.pauseAdRequests, 1);
  unsubscribe();
});

test("unknown or opted-out US state status does not release ads", t => {
  const b = browser(t);
  let state;
  adsController("ca-pub-4907492213987533").subscribe(value => { state = value; });
  for (const status of [0, 3, undefined]) {
    b.setUsStatus(status); b.ready(); b.consent(accepted);
    assert.equal(state.allowed, false);
  }
});

for (const setting of [{ gpc: true }, { stored: "denied" }]) test(`no SDK load for opt-out ${JSON.stringify(setting)}`, t => {
  const b = browser(t, setting);
  let state;
  adsController("ca-pub-4907492213987533").subscribe(value => { state = value; });
  assert.equal(state.allowed, false);
  assert.equal(state.disabled, true);
  assert.equal(b.scripts.length, 0);
});

test("local opt-out and another tab's opt-out pause requests", t => {
  const b = browser(t);
  const controller = adsController("ca-pub-4907492213987533");
  let state;
  controller.subscribe(value => { state = value; }); b.ready(); b.consent(accepted);
  controller.setDisabled(true);
  assert.equal(state.allowed, false);
  controller.setDisabled(false);
  assert.equal(state.allowed, true);
  b.events.storage({ key: "kls-ads-choice", newValue: "denied" });
  assert.equal(state.allowed, false);
  assert.equal(b.scripts.length, 1);
});

test("an SDK load failure never releases requests", t => {
  const b = browser(t);
  let state;
  adsController("ca-pub-4907492213987533").subscribe(value => { state = value; });
  b.scripts[0].onerror(); b.ready(); b.consent(accepted);
  assert.equal(state.allowed, false);
});

test("a later US privacy choice stops requests without trusting the initial snapshot", t => {
  const b = browser(t);
  let state;
  let gpp;
  b.win.__gpp = (_, callback) => { gpp = callback; };
  adsController("ca-pub-4907492213987533").subscribe(value => { state = value; });
  b.ready(); b.consent(accepted);
  gpp({ eventName: "listenerRegistered" }, true);
  assert.equal(state.allowed, true);
  gpp({ eventName: "sectionChange" }, true);
  assert.equal(state.allowed, false);
  b.consent(accepted);
  assert.equal(state.allowed, false);
});

test("ad placement requires explicit enable flag and valid account and slot", t => {
  const keys = ["KLS_ADS_ENABLED", "KLS_ADSENSE_PUBLISHER_ID", "KLS_ADSENSE_DISPLAY_SLOT"];
  const original = keys.map(key => process.env[key]);
  t.after(() => keys.forEach((key, index) => { if (original[index] === undefined) delete process.env[key]; else process.env[key] = original[index]; }));
  process.env.KLS_ADSENSE_PUBLISHER_ID = "pub-4907492213987533";
  process.env.KLS_ADSENSE_DISPLAY_SLOT = "8149837735";
  process.env.KLS_ADS_ENABLED = "false";
  assert.equal(displayAdConfig(), null);
  process.env.KLS_ADS_ENABLED = "true";
  assert.deepEqual(displayAdConfig(), { client: "ca-pub-4907492213987533", slot: "8149837735" });
  process.env.KLS_ADSENSE_DISPLAY_SLOT = "invalid";
  assert.equal(displayAdConfig(), null);
});

test("privacy-page choices do not load the ad SDK", t => {
  const b = browser(t);
  const controller = adsController("ca-pub-4907492213987533");
  controller.subscribe(() => {}, false);
  controller.setDisabled(true); controller.setDisabled(false);
  assert.equal(b.scripts.length, 0);
  controller.subscribe(() => {});
  assert.equal(b.scripts.length, 1);
});

test("Google consent review is requested only through its ready callback", t => {
  const b = browser(t);
  b.win.location.href += "?ad_choices=1";
  adsController("ca-pub-4907492213987533").subscribe(() => {});
  let reviewed = 0;
  b.win.googlefc.showRevocationMessage = () => { reviewed++; };
  assert.equal(reviewed, 0);
  b.ready();
  assert.equal(reviewed, 1);
});
