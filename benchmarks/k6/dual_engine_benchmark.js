import http from 'k6/http';
import { check, sleep } from 'k6';
import { Trend } from 'k6/metrics';

const sparqlLatency = new Trend('sparql_engine_duration', true);
const sqlLatency = new Trend('sql_engine_duration', true);

export const options = {
  stages: [
    { duration: '10s', target: 20 }, // Ramp up to 20 virtual users
    { duration: '20s', target: 50 }, // Sustained load at 50 virtual users
    { duration: '10s', target: 0 },  // Ramp down
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    sparql_engine_duration: ['p(95)<250'],
    sql_engine_duration: ['p(95)<150'],
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8000';

export default function () {
  // 1. Query BIBFRAME RDF Knowledge Graph via SPARQL
  const sparqlRes = http.get(`${BASE_URL}/api/v1/graph/works`);
  sparqlLatency.add(sparqlRes.timings.duration);
  check(sparqlRes, {
    'SPARQL status is 200': (r) => r.status === 200,
    'SPARQL returns 3 works': (r) => r.json('total_works') === 3,
  });

  // 2. Query Normalized Relational SQLite Baseline
  const sqlRes = http.get(`${BASE_URL}/api/v1/sql/works`);
  sqlLatency.add(sqlRes.timings.duration);
  check(sqlRes, {
    'SQL status is 200': (r) => r.status === 200,
    'SQL returns 3 works': (r) => r.json('total_works') === 3,
  });

  sleep(0.2);
}