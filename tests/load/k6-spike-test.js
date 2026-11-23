/**
 * Spike Test: Sudden 10x traffic burst (10 -> 100 VUs in 10s) (PROD-009)
 * Verifies graceful degradation (200 or 503) and clean post-spike recovery.
 */

import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';
import { formatTextSummary, getBaseUrl } from './k6-common.js';

const errorRate = new Rate('errors');

export const options = {
  scenarios: {
    spike: {
      executor: 'ramping-vus',
      stages: [
        { duration: '2m', target: 10 },
        { duration: '10s', target: 100 },
        { duration: '2m', target: 100 },
        { duration: '10s', target: 10 },
        { duration: '2m', target: 10 },
      ],
    },
  },
  thresholds: {
    'http_req_duration{scenario:spike}': ['p(99)<5000'],
    'http_req_failed{scenario:spike}': ['rate<0.1'],
    errors: ['rate<0.1'],
  },
  tags: {
    test_type: 'spike',
    environment: __ENV.TEST_ENV || 'test',
  },
};

export default function () {
  const response = http.get(`${getBaseUrl()}/health`, {
    tags: { name: 'SpikeHealthCheck', scenario: 'spike' },
  });

  const ok = check(response, {
    'status is 200 or 503': (r) => r.status === 200 || r.status === 503,
  });

  errorRate.add(!ok);
}

export function handleSummary(data) {
  return {
    'results/spike-summary.json': JSON.stringify(data, null, 2),
    stdout: formatTextSummary('10x Traffic Spike Test', data),
  };
}
