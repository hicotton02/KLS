// Run against an ads-enabled local/canary site; all Google traffic is mocked.
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.KLS_BROWSER_TEST_URL || "http://127.0.0.1:4180";
const browser = await chromium.launch({ headless: true });
const output = process.env.KLS_BROWSER_TEST_OUTPUT || "test-output/ads";
await mkdir(output, { recursive: true });
const errors = [];
const accepted = { cmpStatus: "loaded", eventStatus: "useractioncomplete", gdprApplies: true,
  purpose: { consents: { 1: true } }, vendor: { consents: { 755: true } } };

async function setup({ width = 1365, stored, gpc = false, sdkFailure = false, path = "/area/wyoming" } = {}) {
  const context = await browser.newContext({ viewport: { width, height: 900 } });
  await context.addInitScript(({ stored, gpc }) => {
    localStorage.setItem("kls-analytics-consent", "denied");
    if (stored) localStorage.setItem("kls-ads-choice", stored);
    Object.defineProperty(navigator, "globalPrivacyControl", { value: gpc });
  }, { stored, gpc });
  let sdkRequests = 0;
  await context.route("**/*", route => {
    const url = route.request().url();
    if (/\/pagead\/js\/adsbygoogle\.js/.test(url)) {
      sdkRequests++;
      return sdkFailure ? route.abort() : route.fulfill({ contentType: "text/javascript", body: `
        window.__testAdRequests = 0;
        window.__testPauseAtLoad = adsbygoogle.pauseAdRequests;
        window.__testConsent = null;
        window.__tcfapi = (command, version, callback) => { window.__testConsent = callback; };
        window.__gpp = (command, callback) => { window.__testGpp = callback; };
        googlefc.usstatesoptout.getInitialUsStatesOptOutStatus = () => 1;
        googlefc.showRevocationMessage = () => { window.__testReview = true; };
        const run = entry => Object.values(entry).forEach(callback => callback());
        googlefc.callbackQueue.forEach(run);
        googlefc.callbackQueue.push = run;
        adsbygoogle.push = () => {
          if (adsbygoogle.pauseAdRequests !== 0) throw new Error('Requested while paused');
          window.__testAdRequests++;
          const unit = document.querySelector('ins.adsbygoogle:not([data-adsbygoogle-status])');
          unit.setAttribute('data-adsbygoogle-status', 'done');
          unit.setAttribute('data-ad-status', 'filled');
          const frame = document.createElement('iframe');
          frame.title = 'Test advertisement';
          frame.style.cssText = 'width:100%;height:100%;border:0';
          frame.srcdoc = '<body style="margin:0;background:#edf0f2;color:#52606a;display:grid;place-items:center;font:14px Arial;height:100vh">Test ad</body>';
          unit.append(frame);
        };
      ` });
    }
    if (/googlesyndication|doubleclick|google-analytics|googletagmanager|fundingchoices/.test(url)) return route.abort();
    return route.continue();
  });
  const page = await context.newPage();
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(base + path, { waitUntil: "networkidle" });
  return { context, page, sdkRequests: () => sdkRequests };
}

async function consent(page, data = accepted) {
  await page.waitForFunction(() => typeof window.__testConsent === "function");
  await page.evaluate(data => window.__testConsent(data, true), data);
}
async function requests(page) { return page.evaluate(() => window.__testAdRequests || 0); }
async function reveal(page) {
  await page.locator(".display-ad-anchor").scrollIntoViewIfNeeded();
  await page.waitForTimeout(250);
}

try {
  const b = await setup();
  await b.page.waitForFunction(() => window.__testPauseAtLoad !== undefined, null, { timeout: 10000 });
  assert.equal(await b.page.evaluate(() => window.__testPauseAtLoad), 1);
  assert.equal(await b.page.locator("ins.adsbygoogle").count(), 0);
  await consent(b.page, { ...accepted, purpose: { consents: {} } });
  await reveal(b.page);
  assert.equal(await requests(b.page), 0);
  await b.page.evaluate(() => scrollTo(0, 0));
  await b.page.waitForTimeout(250);
  await consent(b.page);
  assert.equal(await requests(b.page), 0, "ad stays lazy above the fold");
  await reveal(b.page);
  assert.equal(await requests(b.page), 1);
  assert.equal(await b.page.locator("ins").getAttribute("data-ad-slot"), "8149837735");
  assert.equal(await b.page.locator("ins").getAttribute("data-restrict-data-processing"), "1");
  await b.page.screenshot({ path: join(output, "desktop.png") });
  await b.page.setViewportSize({ width: 1280, height: 900 });
  await reveal(b.page);
  assert.equal(await requests(b.page), 1, "resize does not duplicate a request");
  await b.page.locator("ins").evaluate(el => el.setAttribute("data-ad-status", "unfilled"));
  assert.equal(await b.page.locator(".display-ad").isVisible(), false, "unfilled ad collapses");
  await b.page.locator('a[href="/privacy"]').first().click();
  await b.page.getByLabel("Show ads on this browser").uncheck();
  assert.equal(await b.page.evaluate(() => localStorage.getItem("kls-ads-choice")), "denied");
  await b.page.goto(base + "/area/wyoming", { waitUntil: "networkidle" });
  await reveal(b.page);
  assert.equal(await b.page.locator("ins.adsbygoogle").count(), 0);
  assert.equal(b.sdkRequests(), 1, "opt-out persists across page loads");
  await b.context.close();

  const privacy = await setup({ path: "/privacy" });
  await privacy.page.getByLabel("Show ads on this browser").uncheck();
  await privacy.page.getByLabel("Show ads on this browser").check();
  assert.equal(privacy.sdkRequests(), 0, "privacy page never loads an ad tag");
  await privacy.page.getByRole("link", { name: "Google consent settings" }).click();
  await privacy.page.waitForFunction(() => window.__testReview === true);
  assert.equal(privacy.sdkRequests(), 1);
  await privacy.context.close();

  const navigation = await setup();
  await consent(navigation.page); await reveal(navigation.page);
  assert.equal(await requests(navigation.page), 1);
  const billLink = navigation.page.locator('a[href^="/area/wyoming/bill/"]').first();
  const billPath = await billLink.getAttribute("href");
  await billLink.click();
  await navigation.page.waitForURL(url => url.pathname === billPath);
  await navigation.page.getByRole("heading", { name: billPath.split("/").at(-1), exact: true }).waitFor();
  await navigation.page.locator(".display-ad-anchor").waitFor();
  await reveal(navigation.page);
  await navigation.page.waitForFunction(() => window.__testAdRequests === 2, null, { timeout: 5000 });
  assert.equal(await requests(navigation.page), 2, "client navigation gets one fresh ad unit");
  assert.equal(navigation.sdkRequests(), 1, "client navigation reuses the SDK");
  await navigation.context.close();

  for (const options of [{ gpc: true }, { stored: "denied" }, { sdkFailure: true }]) {
    const b = await setup(options); await reveal(b.page);
    assert.equal(await b.page.locator("ins.adsbygoogle").count(), 0);
    assert.equal(b.sdkRequests(), options.sdkFailure ? 1 : 0);
    await b.context.close();
  }
  for (const width of [320, 390, 768, 1365]) {
    const b = await setup({ width });
    await consent(b.page); await reveal(b.page);
    assert.equal(await requests(b.page), width < 360 ? 0 : 1);
    const dimensions = await b.page.evaluate(() => ({ viewport: innerWidth, page: document.documentElement.scrollWidth,
      unit: document.querySelector("ins").getBoundingClientRect().toJSON() }));
    assert.ok(dimensions.page <= width + 1, `page overflow at ${width}: ${JSON.stringify(dimensions)}`);
    if (width >= 360) {
      assert.ok(dimensions.unit.left >= 0 && dimensions.unit.right <= width, `ad overflow at ${width}`);
      await b.page.screenshot({ path: join(output, `${width}.png`) });
    } else {
      await b.page.setViewportSize({ width: 390, height: 900 });
      await reveal(b.page);
      assert.equal(await requests(b.page), 1, "ad starts after widening a narrow screen");
    }
    await b.page.evaluate(() => window.__testGpp({ eventName: "sectionChange" }, true));
    assert.equal(await b.page.locator("ins.adsbygoogle").count(), 0);
    await b.context.close();
  }
  assert.deepEqual(errors, []);
  console.log("PASS: consent, lazy single requests, SDK failures, privacy choices, GPC, unfilled ads, 4 viewport widths, and live opt-out changes; no real ad requests.");
} finally { await browser.close(); }
