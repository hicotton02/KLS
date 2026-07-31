import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const templateRoot = new URL("../", import.meta.url);

async function render(pathname = "/", origin = "http://localhost") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  const requestUrl = new URL(pathname, origin);

  return worker.fetch(
    new Request(requestUrl, {
      headers: {
        accept: "text/html",
        host: requestUrl.host,
        "x-forwarded-host": requestUrl.host,
      },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the KLS home page", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>Keeping Law Simple<\/title>/i);
  assert.match(html, /Bills, in plain English\./);
  assert.match(html, /Find your state/);
  assert.match(html, /Congress, without the fog\./);
  assert.match(html, /Last scanned|Not yet scanned/);
  assert.match(html, /action="\/search"/);
  assert.match(html, /Official sources\. Neutral summaries\./);
  assert.doesNotMatch(html, /codex-preview|taking shape|react-loading-skeleton/i);
});

test("renders Wyoming vote explanations on the public route", async () => {
  const response = await render(
    "/area/wyoming/vote-explanations",
    "https://www.keepinglawsimple.org",
  );
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Wyoming vote records/);
  assert.match(html, /Why did they vote that way\?/);
  assert.match(html, /Latest scan/);
  assert.match(html, /Bills with a clear explanation/);
  assert.match(html, /Couldn(?:'|&#x27;)t find a published reason/);
  assert.doesNotMatch(html, /Private beta/);
  assert.doesNotMatch(html, /model|confidence score|AI-generated/i);
});

test("renders Wyoming scan and bill dates for regular people", async () => {
  const response = await render(
    "/area/wyoming/bill/2026/SF0001",
    "https://www.keepinglawsimple.org",
  );
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Last scanned/);
  assert.match(html, /Mar 9, 2026/);
  assert.match(html, /Jul 1, 2026/);
  assert.doesNotMatch(html, /\bUTC\b|2026-03-09T00:00:00/);
});

test("keeps vote explanations scoped to Wyoming and out of primary navigation", async () => {
  const [oldBetaResponse, otherStateResponse] = await Promise.all([
    render(
      "/beta/wyoming/vote-explanations",
      "https://www.keepinglawsimple.org",
    ),
    render(
      "/area/colorado/vote-explanations",
      "https://www.keepinglawsimple.org",
    ),
  ]);
  assert.equal(oldBetaResponse.status, 404);
  assert.equal(otherStateResponse.status, 404);

  const [header, areaPage] = await Promise.all([
    readFile(new URL("../app/components/SiteHeader.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/area/[slug]/page.tsx", import.meta.url), "utf8"),
  ]);
  assert.doesNotMatch(header, /vote-explanations|Wyoming votes/i);
  assert.match(areaPage, /slug === "wyoming"/);
  assert.match(areaPage, /href="\/area\/wyoming\/vote-explanations"/);
  await assert.rejects(access(new URL("../app/lib/vote-explanations.ts", import.meta.url)));
});

test("contains product metadata and no starter or model details", async () => {
  const [page, header, billPage, apiClient, layout, nextConfig, dockerfile, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/components/SiteHeader.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/area/[slug]/bill/[year]/[billNum]/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/lib/kls.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../next.config.ts", import.meta.url), "utf8"),
    readFile(new URL("../Dockerfile", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(packageJson, /"name": "keeping-law-simple-sites"/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton|starter/i);
  assert.match(page, /getOverview/);
  assert.match(page, /Latest scan/);
  assert.doesNotMatch(page, /Coverage areas|Current-session bills/);
  assert.doesNotMatch(header, /Wyoming votes|area\/wyoming\/legislators/);
  assert.match(billPage, /Last scanned/);
  assert.match(billPage, /Why lawmakers voted/);
  assert.match(billPage, /Couldn&apos;t find a published reason/);
  assert.doesNotMatch(`${page}\n${billPage}\n${apiClient}`, /qwen|generator_model|interpretation_model/i);
  assert.match(apiClient, /wy: "America\/Denver"/);
  assert.match(layout, /Keeping Law Simple/);
  assert.doesNotMatch(layout, /codex-preview|_sites-preview|Starter Project/i);
  assert.match(nextConfig, /output: "standalone"/);
  assert.match(dockerfile, /CMD \["node", "server\.js"\]/);

  await access(new URL("../dist/standalone/server.js", import.meta.url));

  await assert.rejects(
    access(new URL("app/_sites-preview/SkeletonPreview.tsx", templateRoot)),
  );
});
