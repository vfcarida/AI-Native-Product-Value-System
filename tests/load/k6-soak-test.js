/**
 * Soak Test: 24-hour sustained load for memory leak & drift detection (PROD-009)
 * Constant 50 VUs (~10 req/s); SLO target p99 < 1s and < 1% errors throughout.
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';
import { buildHeaders, formatTextSummary, getBaseUrl } from './k6-common.js';

const errorRate = new Rate('errors');

export const options = {
  scenarios: {
    soak: {
      executor: 'constant-vus',
      vus: parseInt(__ENV.SOAK_VUS || '50', 10),
      duration: __ENV.SOAK_DURATION || '24h',
    },
  },
  thresholds: {
    http_req_duration: ['p(99)<1000'],
    http_req_failed: ['rate<0.01'],
    errors: ['rate<0.01'],
  },
  tags: {
    test_type: 'soak',
    environment: __ENV.TEST_ENV || 'test',
  },
};

export default function () {
  const response = http.post(
    `${getBaseUrl()}/api/v1/agents/execute`,
    JSON.stringify({
      task_type: 'analysis',
      input: `Soak test iteration ${__ITER}`,
    }),
    {
      headers: buildHeaders(__VU, __ITER),
      tags: { name: 'SoakExecution', test_type: 'soak' },
    }
  );

  const ok = check(response, {
    'status is 200': (r) => r.status === 200,
    'latency stable < 1s': (r) => r.timings.duration < 1000,
  });

  errorRate.add(!ok);
  sleep(5);
}

export function handleSummary(data) {
  return {
    'results/soak-summary.json': JSON.stringify(data, null, 2),
    stdout: formatTextSummary('24h Soak Test', data),
  };
}
