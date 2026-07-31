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

test("renders the private Wyoming vote-explanation beta", async () => {
  const response = await render("/beta/wyoming/vote-explanations");
  assert.equal(response.status, 200);

  const html = await response.text();
  assert.match(html, /Private beta/);
  assert.match(html, /Why did they vote that way\?/);
  assert.match(html, /Art Washut/);
  assert.match(html, /Pam Thayer/);
  assert.match(html, /Elissa Campbell/);
  assert.match(html, /Scott Smith/);
  assert.match(html, /Couldn(?:'|&#x27;)t find a published reason/);
  assert.match(html, /youtube\.com\/watch\?v=X45rOkJsR2g&amp;t=8464s/);
  assert.doesNotMatch(html, /model|confidence score|AI-generated/i);
});

test("keeps the beta off the public host and primary navigation", async () => {
  const publicResponse = await render(
    "/beta/wyoming/vote-explanations",
    "https://www.keepinglawsimple.org",
  );
  assert.equal(publicResponse.status, 404);

  const header = await readFile(new URL("../app/components/SiteHeader.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(header, /vote-explanations|Wyoming votes/i);
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
  assert.doesNotMatch(`${page}\n${billPage}\n${apiClient}`, /qwen|generator_model|interpretation_model/i);
  assert.match(layout, /Keeping Law Simple/);
  assert.doesNotMatch(layout, /codex-preview|_sites-preview|Starter Project/i);
  assert.match(nextConfig, /output: "standalone"/);
  assert.match(dockerfile, /CMD \["node", "server\.js"\]/);

  await access(new URL("../dist/standalone/server.js", import.meta.url));

  await assert.rejects(
    access(new URL("app/_sites-preview/SkeletonPreview.tsx", templateRoot)),
  );
});
