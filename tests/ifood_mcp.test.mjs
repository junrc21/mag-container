import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';

test('iFood MCP exposes only read tools and forwards tenant-bound queries to MAG', async (t) => {
  const fixture = new URL('./fixtures/ifood_fetch_mock.mjs', import.meta.url);
  const child = spawn(process.execPath, ['--import', fixture.pathname, 'mcp/ifood/server.mjs'], {
    cwd: new URL('..', import.meta.url),
    env: { ...process.env, MAG_API_URL: 'http://mag-api:3005', MAG_INTERNAL_KEY: 'test-key', MAG_TENANT_ID: 'tenant-a' },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  t.after(() => child.kill());
  const pending = new Map();
  createInterface({ input: child.stdout }).on('line', (line) => {
    const message = JSON.parse(line);
    pending.get(message.id)?.(message);
    pending.delete(message.id);
  });
  function call(id, method, params = {}) {
    return new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error(`timeout: ${method}`)), 3000);
      pending.set(id, (message) => { clearTimeout(timeout); resolve(message); });
      child.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, method, params })}\n`);
    });
  }

  const initialized = await call(1, 'initialize');
  assert.equal(initialized.result.serverInfo.name, 'mag-ifood');
  const listed = await call(2, 'tools/list');
  assert.deepEqual(listed.result.tools.map((tool) => tool.name), [
    'ifood_analytics_kpis',
    'ifood_financial_sales',
    'ifood_financial_settlements',
    'ifood_financial_events',
    'ifood_financial_anticipations',
  ]);
  const analytics = await call(3, 'tools/call', { name: 'ifood_analytics_kpis', arguments: { dateFrom: '2026-09-01', dateTo: '2026-09-02' } });
  assert.equal(analytics.result.isError, undefined);
  const analyticsResult = JSON.parse(analytics.result.content[0].text);
  assert.match(analyticsResult.url, /^http:\/\/mag-api:3005\/ifood\/internal\/analytics\/kpis\?/);
  assert.equal(analyticsResult.key, 'test-key');
  assert.equal(new URL(analyticsResult.url).searchParams.get('tenantId'), 'tenant-a');
  assert.equal(new URL(analyticsResult.url).searchParams.get('merchantId'), null);
  const financial = await call(4, 'tools/call', { name: 'ifood_financial_sales', arguments: { dateFrom: '2026-09-01', dateTo: '2026-09-02', page: 2 } });
  assert.equal(financial.result.isError, undefined);
  const financialResult = JSON.parse(financial.result.content[0].text);
  assert.match(financialResult.url, /^http:\/\/mag-api:3005\/ifood\/internal\/financial\/sales\?/);
  assert.equal(new URL(financialResult.url).searchParams.get('page'), '2');
  assert.equal(new URL(financialResult.url).searchParams.get('tenantId'), 'tenant-a');
  assert.equal(financialResult.key, 'test-key');

  const settlements = await call(5, 'tools/call', { name: 'ifood_financial_settlements', arguments: { dateFrom: '2026-09-01', dateTo: '2026-09-02' } });
  const settlementsResult = JSON.parse(settlements.result.content[0].text);
  assert.match(settlementsResult.url, /^http:\/\/mag-api:3005\/ifood\/internal\/financial\/settlements\?/);

  const events = await call(6, 'tools/call', { name: 'ifood_financial_events', arguments: { dateFrom: '2026-09-01', dateTo: '2026-09-02', page: 3 } });
  const eventsResult = JSON.parse(events.result.content[0].text);
  assert.match(eventsResult.url, /^http:\/\/mag-api:3005\/ifood\/internal\/financial\/events\?/);
  assert.equal(new URL(eventsResult.url).searchParams.get('page'), '3');

  const anticipations = await call(7, 'tools/call', { name: 'ifood_financial_anticipations', arguments: { dateFrom: '2026-09-01', dateTo: '2026-09-02' } });
  const anticipationsResult = JSON.parse(anticipations.result.content[0].text);
  assert.match(anticipationsResult.url, /^http:\/\/mag-api:3005\/ifood\/internal\/financial\/anticipations\?/);

  const dockerfile = readFileSync(new URL('../Dockerfile', import.meta.url), 'utf8');
  assert.match(dockerfile, /COPY --chown=hermes:hermes mcp\/ifood\/server\.mjs \/opt\/mag\/ifood-mcp\/server\.mjs/);
});
