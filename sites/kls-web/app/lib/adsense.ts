export function adsensePublisherId(): string {
  const value = process.env.KLS_ADSENSE_PUBLISHER_ID?.trim().replace(/^ca-/, "") || "";
  return /^pub-\d{16}$/.test(value) ? value : "";
}
