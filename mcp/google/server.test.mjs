import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { test } from 'node:test';

const SERVER = fileURLToPath(new URL('./server.mjs', import.meta.url));

async function withGoogleMcp(fn) {
  const dir = await mkdtemp(path.join(tmpdir(), 'mag-google-mcp-test-'));
  const preloadPath = path.join(dir, 'mock-fetch.mjs');
  await writeFile(preloadPath, `
function json(body, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

globalThis.fetch = async (input, opts = {}) => {
  const url = String(input);
  const body = opts.body ? JSON.parse(String(opts.body)) : null;

  if (url === 'http://mag.test/internal/google/accounts?tenantId=tenant-1') {
    return json([{ id: 'acct-1', email: 'owner@example.com', name: 'Owner', status: 'active', scopes: [] }]);
  }
  if (url === 'http://mag.test/internal/google/accounts/acct-1/token?tenantId=tenant-1') {
    return json({ accountId: 'acct-1', email: 'owner@example.com', accessToken: 'google-token', expiresAt: '2099-01-01T00:00:00.000Z' });
  }
  if (url === 'https://www.googleapis.com/webmasters/v3/sites') {
    return json({ siteEntry: [{ siteUrl: 'sc-domain:example.com', permissionLevel: 'siteOwner' }] });
  }
  if (url === 'https://www.googleapis.com/webmasters/v3/sites/sc-domain%3Aexample.com/searchAnalytics/query') {
    if (body.startDate !== '2026-09-01' || body.endDate !== '2026-09-27') return json({ error: { message: 'bad date range' } }, 400);
    if (body.type !== 'web') return json({ error: { message: 'missing modern type field' } }, 400);
    if (JSON.stringify(body.dimensions) !== JSON.stringify(['query', 'page'])) return json({ error: { message: 'bad dimensions' } }, 400);
    return json({ rows: [{ keys: ['mag ia', 'https://example.com/'], clicks: 12, impressions: 120, ctr: 0.1, position: 3.2 }] });
  }
  if (url === 'https://analyticsadmin.googleapis.com/v1beta/accountSummaries?pageSize=200') {
    return json({ accountSummaries: [{ displayName: 'Example Account', account: 'accounts/7', propertySummaries: [{ displayName: 'Example GA4', property: 'properties/123', propertyType: 'PROPERTY_TYPE_ORDINARY' }] }] });
  }
  if (url === 'https://analyticsdata.googleapis.com/v1beta/properties/123:runReport') {
    if (JSON.stringify(body.dateRanges) !== JSON.stringify([{ startDate: '30daysAgo', endDate: 'today' }])) return json({ error: { message: 'bad date range' } }, 400);
    if (JSON.stringify(body.dimensions) !== JSON.stringify([{ name: 'date' }])) return json({ error: { message: 'bad dimensions' } }, 400);
    if (JSON.stringify(body.metrics) !== JSON.stringify([{ name: 'activeUsers' }, { name: 'sessions' }])) return json({ error: { message: 'bad metrics' } }, 400);
    return json({ dimensionHeaders: [{ name: 'date' }], metricHeaders: [{ name: 'activeUsers' }, { name: 'sessions' }], rowCount: 1, rows: [{ dimensionValues: [{ value: '20260927' }], metricValues: [{ value: '42' }, { value: '55' }] }] });
  }

  return json({ error: { message: 'unexpected fetch: ' + url } }, 500);
};
`);

  const child = spawn(process.execPath, [SERVER], {
    env: {
      ...process.env,
      NODE_OPTIONS: `--import ${pathToFileURL(preloadPath).href}`,
      MAG_API_URL: 'http://mag.test',
      MAG_INTERNAL_KEY: 'internal-key',
      MAG_TENANT_ID: 'tenant-1',
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  });

  let nextId = 1;
  let buffer = '';
  const pending = new Map();
  let stderr = '';

  child.stderr.on('data', (chunk) => { stderr += chunk; });
  child.stdout.on('data', (chunk) => {
    buffer += chunk;
    while (buffer.includes('\n')) {
      const index = buffer.indexOf('\n');
      const raw = buffer.slice(0, index);
      buffer = buffer.slice(index + 1);
      const message = JSON.parse(raw);
      const waiter = pending.get(message.id);
      if (waiter) {
        pending.delete(message.id);
        waiter.resolve(message);
      }
    }
  });

  function request(method, params = {}) {
    const id = nextId++;
    const message = { jsonrpc: '2.0', id, method, params };
    child.stdin.write(`${JSON.stringify(message)}\n`);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(id);
        reject(new Error(`MCP timeout for ${method}; stderr=${stderr}`));
      }, 5000);
      pending.set(id, {
        resolve: (value) => {
          clearTimeout(timer);
          resolve(value);
        },
      });
      child.once('error', reject);
    });
  }

  async function callTool(name, args = {}) {
    const response = await request('tools/call', { name, arguments: args });
    assert.equal(response.result?.isError, undefined, response.result?.content?.[0]?.text);
    return response.result.content[0].text;
  }

  try {
    await request('initialize', { protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'test', version: '1' } });
    await fn({ request, callTool });
  } finally {
    child.kill();
    await rm(dir, { recursive: true, force: true });
  }
}

test('Google MCP exposes and calls Search Console tools', async () => {
  await withGoogleMcp(async ({ request, callTool }) => {
    const list = await request('tools/list');
    const names = list.result.tools.map((tool) => tool.name);
    assert.ok(names.includes('search_console_list_sites'));
    assert.ok(names.includes('search_console_query'));

    const sites = JSON.parse(await callTool('search_console_list_sites'));
    assert.deepEqual(sites, [{ siteUrl: 'sc-domain:example.com', permissionLevel: 'siteOwner' }]);

    const report = JSON.parse(await callTool('search_console_query', {
      siteUrl: 'sc-domain:example.com',
      startDate: '2026-09-01',
      endDate: '2026-09-27',
    }));
    assert.equal(report.rows[0].clicks, 12);
    assert.equal(report.rows[0].keys[0], 'mag ia');
  });
});

test('Google MCP exposes and calls Analytics GA4 tools', async () => {
  await withGoogleMcp(async ({ request, callTool }) => {
    const list = await request('tools/list');
    const names = list.result.tools.map((tool) => tool.name);
    assert.ok(names.includes('analytics_list_properties'));
    assert.ok(names.includes('analytics_run_report'));

    const properties = JSON.parse(await callTool('analytics_list_properties'));
    assert.equal(properties[0].propertyId, '123');
    assert.equal(properties[0].property, 'Example GA4');

    const report = JSON.parse(await callTool('analytics_run_report', {
      propertyId: '123',
      startDate: '30daysAgo',
      endDate: 'today',
      metrics: ['activeUsers', 'sessions'],
    }));
    assert.equal(report.property, 'properties/123');
    assert.equal(report.rows[0].metrics.activeUsers, '42');
    assert.equal(report.rows[0].metrics.sessions, '55');
  });
});
