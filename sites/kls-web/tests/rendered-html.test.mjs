import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const templateRoot = new URL("../", import.meta.url);

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
      headers: { accept: "text/html" },
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

test("contains product metadata and no starter or model details", async () => {
  const [page, billPage, apiClient, layout, nextConfig, dockerfile, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
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
