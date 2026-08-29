export const SITE_NAME = "Keeping Law Simple";
export const SITE_DESCRIPTION =
  "State and federal legislation explained in neutral, plain English with official sources attached.";

const configuredOrigin = process.env.KLS_PUBLIC_BASE_URL?.trim() || "https://www.keepinglawsimple.org";
export const SITE_ORIGIN = new URL(configuredOrigin);

export function absoluteUrl(path: string) {
  return new URL(path, SITE_ORIGIN).toString();
}

export function areaHref(slug: string, year?: number | string | null) {
  const path = `/area/${encodeURIComponent(slug)}`;
  return year === undefined || year === null || year === "" ? path : `${path}?year=${encodeURIComponent(String(year))}`;
}

export function billPageHref(slug: string, year: number | string, billNum: string, specialSession?: number | string | null) {
  const path = `/area/${encodeURIComponent(slug)}/bill/${encodeURIComponent(String(year))}/${encodeURIComponent(billNum)}`;
  return specialSession === undefined || specialSession === null || specialSession === ""
    ? path
    : `${path}?special_session=${encodeURIComponent(String(specialSession))}`;
}

export function jsonLd(value: unknown) {
  return JSON.stringify(value).replace(/</g, "\\u003c");
}
