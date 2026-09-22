import assert from "node:assert/strict";
import test from "node:test";

const originalFetch = globalThis.fetch;
process.env.KLS_API_BASE_URL = "https://content-fixture.invalid";
process.env.KLS_ADS_ENABLED = "true";
process.env.KLS_ADSENSE_PUBLISHER_ID = "pub-4907492213987533";
process.env.KLS_ADSENSE_DISPLAY_SLOT = "8149837735";

function fixture(indexable, ready) {
  return {
    jurisdiction: {name: "Wyoming", state_code: "wy", slug: "wyoming", source_name: "Wyoming Legislature", source_url: "https://www.wyoleg.gov"},
    bill: {bill_num: "HB0001", year: 2026, catch_title: "School funding", outcome: "active",
      summary: ready ? "This bill funds school repairs." : "",
      content_quality: {summary_ready: ready, indexable, ads_eligible: ready,
        notice: "A plain-English explanation is not ready. Check the official record below."}},
    interpretation: {one_sentence_summary: "This bill funds school repairs.", what_it_does: ["Sets aside money for roof repairs."]},
    official_links: {official_page: "https://www.wyoleg.gov/Legislation/2026/HB0001"},
    amendments: [], actions: [], roll_calls: [], vote_explanations: [], relationships: [],
  };
}

test("bill pages fail closed for unfinished explanations while keeping useful archives", async () => {
  try {
    for (const [name, indexable, ready] of [["thin", false, false], ["archive", true, false], ["ready", true, true]]) {
      globalThis.fetch = async (input, init) => {
        const url = String(input instanceof Request ? input.url : input);
        if (url.startsWith("https://content-fixture.invalid/api/v1/areas/wyoming/bills/")) {
          return Response.json(fixture(indexable, ready));
        }
        return originalFetch(input, init);
      };
      const url = new URL("../dist/server/index.js", import.meta.url);
      url.searchParams.set("case", name);
      const { default: worker } = await import(url.href);
      const response = await worker.fetch(new Request(`https://www.keepinglawsimple.org/area/wyoming/bill/2026/${name}`, {
        headers: {accept: "text/html", host: "www.keepinglawsimple.org"},
      }), {ASSETS: {fetch: async () => new Response("Not found", {status: 404})}}, {waitUntil() {}, passThroughOnException() {}});
      assert.equal(response.status, 200, name);
      const html = await response.text();
      assert.match(html, indexable ? /name="robots" content="index, follow"/ : /name="robots" content="noindex, follow"/, name);
      assert.match(html, /Official bill page/);
      if (ready) {
        assert.match(html, /Sets aside money for roof repairs/);
        assert.match(html, /class="display-ad-anchor"/);
      } else {
        assert.match(html, /explanation is not ready/);
        assert.doesNotMatch(html, /Sets aside money for roof repairs|class="display-ad-anchor"|Who it affects/);
      }
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});
