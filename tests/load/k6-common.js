/**
 * Shared k6 utilities for deterministic workload selection, headers, and summary formatting (PROD-009).
 */

export function getBaseUrl() {
  return (__ENV.BASE_URL || 'http://localhost:8000').replace(/\/+$/, '');
}

export function buildHeaders(vu, iter) {
  const headers = {
    'Content-Type': 'application/json',
    'X-Request-ID': `k6-${vu}-${iter}`,
  };
  if (__ENV.TEST_API_KEY) {
    headers['Authorization'] = `Bearer ${__ENV.TEST_API_KEY}`;
  }
  return headers;
}

/**
 * Deterministic selection based on VU + iteration + configurable seed (reproducible runs).
 */
export function selectFixture(items, vu, iter) {
  const seed = parseInt(__ENV.LOAD_TEST_SEED || '42', 10);
  const idx = Math.abs((vu * 31 + iter * 17 + seed) % items.length);
  return items[idx];
}

/**
 * Compact plain-text summary formatter requiring no external CDN imports.
 */
export function formatTextSummary(title, data) {
  const m = data.metrics || {};
  const dur = (m.http_req_duration && m.http_req_duration.values) || {};
  const reqs = (m.http_reqs && m.http_reqs.values) || {};
  const failed = (m.http_req_failed && m.http_req_failed.values) || {};
  const lines = [
    `=== k6 Load Test Summary: ${title} ===`,
    `  Total Requests : ${reqs.count || 0} (${(reqs.rate || 0).toFixed(2)} req/s)`,
    `  Latency p50    : ${(dur['p(50)'] || dur.med || 0).toFixed(1)} ms`,
    `  Latency p95    : ${(dur['p(95)'] || 0).toFixed(1)} ms`,
    `  Latency p99    : ${(dur['p(99)'] || 0).toFixed(1)} ms`,
    `  HTTP Failed    : ${((failed.rate || 0) * 100).toFixed(2)}%`,
  ];
  return `${lines.join('\n')}\n`;
}
