import { adsensePublisherId } from "../lib/adsense";

export async function GET() {
  const publisherId = adsensePublisherId();
  const value = process.env.KLS_ADS_TXT?.trim() ||
    (publisherId ? `google.com, ${publisherId}, DIRECT, f08c47fec0942fa0` : "");
  if (!value) {
    return new Response("Not configured\n", {
      status: 404,
      headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
    });
  }
  return new Response(`${value}\n`, {
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "public, max-age=3600" },
  });
}
