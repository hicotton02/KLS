export async function GET() {
  const value = process.env.KLS_ADS_TXT?.trim();
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
