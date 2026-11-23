/**
 * Greenfield Workload: New project analysis (PROD-009)
 * Scenario: User submits greenfield codebase spec for initial analysis
 * SLO Target: p99 latency < 5s, error rate < 1%, throughput > 10 RPS
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
const latency = new Trend('request_duration');
const tokenUsage = new Counter('tokens_used');

const projects = new SharedArray('greenfield_projects', function () {
  return JSON.parse(open('./fixtures/test-data.json')).greenfield_projects;
});

export const options = {
  scenarios: {
    rampup: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '5m', target: 20 },
        { duration: '10m', target: 100 },
        { duration: '5m', target: 100 },
        { duration: '5m', target: 0 },
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    'http_req_duration{scenario:rampup}': ['p(99)<5000'],
    'http_req_failed{scenario:rampup}': ['rate<0.01'],
    errors: ['rate<0.01'],
  },
  tags: {
    test_type: 'greenfield',
    environment: __ENV.TEST_ENV || 'test',
  },
};

export default function () {
  const project = selectFixture(projects, __VU, __ITER);
  const payload = JSON.stringify({
    task_type: 'greenfield_analysis',
    project: {
      id: project.id,
      name: project.name,
      domain: project.domain,
      files: project.files,
      description: project.description,
    },
    options: {
      depth: 'comprehensive',
      model: __ENV.LOAD_TEST_MODEL || 'gpt-4o-mini',
    },
  });

  const params = {
    headers: buildHeaders(__VU, __ITER),
    tags: { name: 'GreenfieldAgentExecution', scenario: 'greenfield' },
  };

  const response = http.post(`${getBaseUrl()}/api/v1/agents/execute`, payload, params);

  const ok = check(response, {
    'status is 200': (r) => r.status === 200,
    'has result': (r) => {
      try {
        return r.json('result') !== undefined;
      } catch (_err) {
        return false;
      }
    },
    'latency < 5s': (r) => r.timings.duration < 5000,
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

  sleep(2 + ((__VU + __ITER) % 4));
}

export function handleSummary(data) {
  return {
    'results/greenfield-summary.json': JSON.stringify(data, null, 2),
    stdout: formatTextSummary('Greenfield Workload', data),
  };
}
