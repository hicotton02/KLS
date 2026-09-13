export function adsensePublisherId(): string {
  const value = process.env.KLS_ADSENSE_PUBLISHER_ID?.trim().replace(/^ca-/, "") || "";
  return /^pub-\d{16}$/.test(value) ? value : "";
}

export function displayAdConfig(): { client: string; slot: string } | null {
  const publisher = adsensePublisherId();
  const slot = process.env.KLS_ADSENSE_DISPLAY_SLOT?.trim() || "";
  return process.env.KLS_ADS_ENABLED === "true" && publisher && /^\d{10}$/.test(slot)
    ? { client: `ca-${publisher}`, slot } : null;
}
