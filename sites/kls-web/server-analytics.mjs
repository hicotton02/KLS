import { channel } from "node:diagnostics_channel";
import { randomUUID } from "node:crypto";

const publicHosts = new Set(["www.keepinglawsimple.org", "keepinglawsimple.org"]);
const excludedPaths = /^(?:\/(?:api|internal|admin|beta|static|_next|assets)(?:\/|$)|\/(?:healthz|readyz|metrics|robots\.txt|sitemap\.xml|ads\.txt|favicon\.ico)$)/;

function header(request, name, length = 300) {
  const value = request.headers[name];
  return (Array.isArray(value) ? value[0] : value || "").slice(0, length);
}

export function pageEvent(request, response, writtenContentType = "") {
  if (request.method !== "GET" || response.statusCode < 200 || response.statusCode >= 300) return null;
  const contentType = String(writtenContentType || response.getHeader("content-type") || "");
  if (!/^(text\/html|text\/x-component)\b/i.test(contentType)) return null;
  if (header(request, "next-router-prefetch") || header(request, "next-router-segment-prefetch") ||
      /prefetch|prerender/i.test(`${header(request, "purpose")} ${header(request, "sec-purpose")}`)) return null;
  const host = header(request, "host").toLowerCase();
  if (!publicHosts.has(host)) return null;
  if (!request.url?.startsWith("/") || request.url.startsWith("//")) return null;
  const path = request.url.split(/[?#]/, 1)[0];
  if (path.length > 1000 || excludedPaths.test(path) || /\.[a-z0-9]{1,8}$/i.test(path)) return null;
  // Send only the referring host, never search terms or referring page contents.
  let referrer = "";
  try {
    const url = new URL(header(request, "referer", 2048));
    if (["http:", "https:"].includes(url.protocol)) referrer = `${url.protocol}//${url.host}`.slice(0, 300);
  } catch { /* A missing or invalid referrer is normal. */ }
  return {
    event_id: randomUUID(), occurred_at: new Date().toISOString(), host, path,
    status_code: response.statusCode, user_agent: header(request, "user-agent"),
    client_ip: (header(request, "x-forwarded-for", 2048).split(",")[0].trim() ||
      header(request, "x-real-ip", 64) || request.socket?.remoteAddress || "").slice(0, 64),
    referrer, language: header(request, "accept-language", 200),
    fetch_mode: header(request, "sec-fetch-mode", 30), fetch_dest: header(request, "sec-fetch-dest", 30),
  };
}

export function createCollector({ endpoint, token, fetchImpl = fetch, log = console.warn,
  intervalMs = 1000, maxQueue = 256, timeoutMs = 5000, maxAttempts = 3 }) {
  const queue = [];
  let inFlight = false;
  let closed = false;
  let retryAt = 0;
  const stats = { sent: 0, failed: 0, dropped: 0 };
  function enqueue(event) {
    if (closed) return;
    if (queue.length >= maxQueue) { stats.dropped++; return; }
    queue.push({ event, attempts: 0 });
  }
  async function flush() {
    if (inFlight || !queue.length || Date.now() < retryAt) return;
    inFlight = true;
    const batch = queue.slice(0, 16);
    let success = false;
    let retryable = true;
    try {
      const response = await fetchImpl(endpoint, {
        method: "POST", redirect: "error", signal: AbortSignal.timeout(timeoutMs),
        headers: { "Content-Type": "application/json", "x-kls-analytics-token": token },
        body: JSON.stringify({ events: batch.map(item => item.event) }),
      });
      success = response.ok;
      retryable = response.status === 429 || response.status >= 500;
      await response.body?.cancel();
    } catch { /* Transient errors retry the same event IDs. */ }
    if (success) {
      queue.splice(0, batch.length);
      stats.sent += batch.length;
      retryAt = 0;
    } else {
      stats.failed++;
      for (const item of batch) item.attempts++;
      if (!retryable || batch[0].attempts >= maxAttempts) {
        queue.splice(0, batch.length);
        stats.dropped += batch.length;
      }
      retryAt = Date.now() + Math.min(15000, intervalMs * 2 ** batch[0].attempts);
      log(`[site-analytics] delivery failed; queued=${queue.length} dropped=${stats.dropped}`);
    }
    inFlight = false;
  }
  const timer = setInterval(() => { void flush(); }, intervalMs);
  timer.unref();
  return { enqueue, flush, stats, pending: () => queue.length,
    close() { closed = true; clearInterval(timer); } };
}

export function startServerAnalytics(env = process.env, options = {}) {
  const token = env.KLS_SITE_ANALYTICS_TOKEN?.trim();
  const base = env.KLS_API_BASE_URL?.trim();
  if (!token || !base) return null;
  const collector = createCollector({
    endpoint: new URL("/internal/site-page-views", base).href, token, ...options,
  });
  const started = channel("http.server.request.start");
  const finished = channel("http.server.response.finish");
  const contentTypes = new WeakMap();
  const onRequest = ({ response }) => {
    const writeHead = response.writeHead;
    // Node getHeader() cannot see headers passed directly to writeHead().
    response.writeHead = function (...args) {
      const headers = typeof args[1] === "string" ? args[2] : args[1];
      if (Array.isArray(headers)) {
        for (let i = 0; i < headers.length; i += 2) {
          if (String(headers[i]).toLowerCase() === "content-type") contentTypes.set(response, String(headers[i + 1]));
        }
      } else if (headers && typeof headers === "object") {
        for (const [name, value] of Object.entries(headers)) {
          if (name.toLowerCase() === "content-type") contentTypes.set(response, String(value));
        }
      }
      return Reflect.apply(writeHead, this, args);
    };
  };
  const listener = ({ request, response }) => {
    // Analytics must never break a page response.
    try {
      const event = pageEvent(request, response, contentTypes.get(response));
      if (event) collector.enqueue(event);
    } catch { /* Ignore malformed request metadata. */ }
    contentTypes.delete(response);
  };
  started.subscribe(onRequest);
  finished.subscribe(listener);
  return { ...collector, close() { started.unsubscribe(onRequest); finished.unsubscribe(listener); collector.close(); } };
}

startServerAnalytics();
