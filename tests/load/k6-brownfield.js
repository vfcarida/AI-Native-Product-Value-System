/**
 * Brownfield Workload: Existing codebase modernization & Strangler Fig planning (PROD-009)
 * Scenario: 50 concurrent users analyzing legacy repositories for boundary extraction
 * SLO Target: p99 latency < 10s, error rate < 1%
 */

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';
import { SharedArray } from 'k6/data';
import {
  buildHeaders,
  formatTextSummary,
  getBaseUrl,
  selectFixture,
} from './k6-common.js';

const errorRate = new Rate('errors');
const latency = new Trend('brownfield_duration');
const tokenUsage = new Counter('tokens_used');

const repos = new SharedArray('brownfield_projects', function () {
  return JSON.parse(open('./fixtures/test-data.json')).brownfield_projects;
});

export const options = {
  scenarios: {
    brownfield: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '3m', target: 15 },
        { duration: '7m', target: 50 },
        { duration: '5m', target: 50 },
        { duration: '3m', target: 0 },
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    'http_req_duration{scenario:brownfield}': ['p(99)<10000'],
    'http_req_failed{scenario:brownfield}': ['rate<0.01'],
    errors: ['rate<0.01'],
  },
  tags: {
    test_type: 'brownfield',
    environment: __ENV.TEST_ENV || 'test',
  },
};

export default function () {
  const repo = selectFixture(repos, __VU, __ITER);
  const payload = JSON.stringify({
    task_type: 'brownfield_modernization',
    repository: {
      id: repo.id,
      name: repo.name,
      language: repo.language,
      loc: repo.loc,
      hotspots: repo.hotspots,
      target_boundary: repo.target_boundary,
      description: repo.description,
    },
    options: {
      include_strangler_plan: true,
      include_acl_contracts: true,
    },
  });

  const params = {
    headers: buildHeaders(__VU, __ITER),
    tags: { name: 'BrownfieldModernization', scenario: 'brownfield' },
  };

  const response = http.post(`${getBaseUrl()}/api/v1/agents/execute`, payload, params);

  const ok = check(response, {
    'status is 200': (r) => r.status === 200,
    'latency < 10s': (r) => r.timings.duration < 10000,
  });

  errorRate.add(!ok);
  latency.add(response.timings.duration);

  if (response.status === 200) {
    try {
      const body = response.json();
      if (body && body.usage && body.usage.total_tokens) {
        tokenUsage.add(body.usage.total_tokens);
      }
    } catch (_err) {
      // Ignore non-JSON bodies
    }
  }

  sleep(3 + ((__VU + __ITER) % 4));
}

export function handleSummary(data) {
  return {
    'results/brownfield-summary.json': JSON.stringify(data, null, 2),
    stdout: formatTextSummary('Brownfield Workload', data),
  };
}
