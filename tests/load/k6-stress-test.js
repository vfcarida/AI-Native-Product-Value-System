/**
 * Stress Test: Identify system breaking point up to 10,000 req/s (PROD-009)
 * Gradually ramps arrival rate until saturation / graceful degradation occurs.
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate } from 'k6/metrics';
import { formatTextSummary, getBaseUrl } from './k6-common.js';

const errorRate = new Rate('errors');

export const options = {
  scenarios: {
    stress: {
      executor: 'ramping-arrival-rate',
      startRate: 10,
      timeUnit: '1s',
      preAllocatedVUs: 500,
      maxVUs: 10000,
      stages: [
        { duration: '5m', target: 100 },
        { duration: '5m', target: 500 },
        { duration: '5m', target: 1000 },
        { duration: '5m', target: 2000 },
        { duration: '5m', target: 5000 },
        { duration: '5m', target: 10000 },
      ],
    },
  },
  thresholds: {
    http_req_duration: ['p(50)<1000', 'p(99)<10000'],
    errors: ['rate<0.5'],
  },
  tags: {
    test_type: 'stress',
    environment: __ENV.TEST_ENV || 'test',
  },
};

export default function () {
  const response = http.get(`${getBaseUrl()}/health`, {
    tags: { name: 'HealthCheck', test_type: 'stress' },
  });

  const ok = check(response, {
    'status is 200': (r) => r.status === 200,
  });

  errorRate.add(!ok);
  sleep(0.1);
}

export function handleSummary(data) {
  return {
    'results/stress-summary.json': JSON.stringify(data, null, 2),
    stdout: formatTextSummary('Stress Test (Breaking Point)', data),
  };
}
