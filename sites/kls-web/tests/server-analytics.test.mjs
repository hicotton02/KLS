import assert from "node:assert/strict";
import { createServer, get } from "node:http";
import { once } from "node:events";
import { setTimeout as delay } from "node:timers/promises";
import test from "node:test";
import { createCollector, pageEvent, startServerAnalytics } from "../server-analytics.mjs";

function request(overrides = {}) {
  return { method: "GET", url: "/area/wyoming?q=private", socket: { remoteAddress: "192.0.2.1" },
    headers: { host: "www.keepinglawsimple.org", "user-agent": "Mozilla/5.0", "accept-language": "en-US",
      referer: "https://google.com/search?q=private", cookie: "private-cookie" }, ...overrides };
}
function response(type = "text/html", statusCode = 200) {
  return { statusCode, getHeader: () => type };
}

test("captures pages without query strings, cookies, or full referrers", () => {
  const event = pageEvent(request(), response());
  assert.equal(event.path, "/area/wyoming");
  assert.equal(event.referrer, "https://google.com");
  assert.doesNotMatch(JSON.stringify(event), /private/);
  assert.match(event.event_id, /^[a-f0-9-]{36}$/);
});

test("excludes non-pages, probes, private routes, failures, and preview hosts", () => {
  for (const url of ["/healthz", "/readyz", "/api/overview", "/admin/analytics", "/beta/wyoming", "/_next/static/x", "/favicon.svg", "//evil.com/"]) {
    assert.equal(pageEvent(request({ url }), response()), null, url);
  }
  assert.equal(pageEvent(request({ method: "POST" }), response()), null);
  assert.equal(pageEvent(request(), response("application/json")), null);
  assert.equal(pageEvent(request(), response("text/html", 404)), null);
  assert.equal(pageEvent(request({ headers: { host: "localhost" } }), response()), null);
});

test("counts actual RSC navigations but not prefetches", () => {
  assert.ok(pageEvent(request(), response("text/x-component")));
  for (const headers of [{ purpose: "prefetch" }, { "sec-purpose": "prefetch;prerender" },
    { "next-router-prefetch": "1" }, { "next-router-segment-prefetch": "/area" }]) {
    assert.equal(pageEvent(request({ headers: { ...request().headers, ...headers } }), response("text/x-component")), null);
  }
});

test("bounded asynchronous batches retry the same event IDs", async () => {
  const batches = [];
  const collector = createCollector({ endpoint: "http://internal.test/", token: "test-only", intervalMs: 10,
    maxQueue: 2, log() {}, fetchImpl: async (_, init) => {
      assert.equal(init.headers["x-kls-analytics-token"], "test-only");
      batches.push(JSON.parse(init.body));
      return new Response("", { status: batches.length === 1 ? 503 : 200 });
    } });
  try {
    collector.enqueue({ event_id: "one" }); collector.enqueue({ event_id: "two" }); collector.enqueue({ event_id: "overflow" });
    assert.equal(collector.pending(), 2);
    assert.equal(collector.stats.dropped, 1);
    await collector.flush();
    assert.equal(collector.stats.failed, 1);
    await delay(40);
    await collector.flush();
    assert.equal(collector.stats.sent, 2);
    assert.deepEqual(batches[0], batches[1]);
  } finally { collector.close(); }
});

test("permanent failures drop the batch without an infinite retry loop", async () => {
  const collector = createCollector({ endpoint: "http://internal.test/", token: "test", log() {},
    fetchImpl: async () => new Response("", { status: 422 }) });
  try {
    collector.enqueue({ event_id: "invalid" });
    await collector.flush();
    assert.equal(collector.pending(), 0);
    assert.equal(collector.stats.dropped, 1);
  } finally { collector.close(); }
});

test("a real Node HTTP response emits one page event", async () => {
  const batches = [];
  const collector = startServerAnalytics({ KLS_SITE_ANALYTICS_TOKEN: "test", KLS_API_BASE_URL: "http://internal.test" }, {
    fetchImpl: async (_, init) => { batches.push(JSON.parse(init.body)); return new Response("{}"); },
  });
  const server = createServer((_, res) => { res.setHeader("Content-Type", "text/html"); res.end("ok"); });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  try {
    await new Promise((resolve, reject) => {
      get(`http://127.0.0.1:${server.address().port}/area/wyoming?q=private`, { headers: request().headers }, res => {
        res.resume(); res.on("end", resolve);
      }).on("error", reject);
    });
    await collector.flush();
    assert.equal(batches.length, 1);
    assert.equal(batches[0].events.length, 1);
    assert.equal(batches[0].events[0].path, "/area/wyoming");
  } finally { collector.close(); server.close(); await once(server, "close"); }
});

test("logging is off unless a server-only token and API URL are configured", () => {
  assert.equal(startServerAnalytics({}), null);
  assert.equal(startServerAnalytics({ KLS_API_BASE_URL: "http://internal.test" }), null);
});
