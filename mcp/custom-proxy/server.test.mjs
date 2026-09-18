import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { test } from 'node:test';

test('Ghost Admin tool signs a short-lived JWT and reads posts', async () => {
  const secret = 'a'.repeat(64);
  const apiKey = `testid:${secret}`;
  const server = createServer((req, res) => {
    try {
      assert.equal(req.url, '/ghost/api/admin/posts/?limit=1');
      assert.equal(req.headers['accept-version'], 'v5.0');
      const [scheme, token] = req.headers.authorization.split(' ');
      assert.equal(scheme, 'Ghost');
      const [header, payload, signature] = token.split('.');
      const signed = createHmac('sha256', Buffer.from(secret, 'hex'))
        .update(`${header}.${payload}`).digest('base64url');
      assert.equal(signature, signed);
      assert.equal(JSON.parse(Buffer.from(header, 'base64url')).kid, 'testid');
      const claims = JSON.parse(Buffer.from(payload, 'base64url'));
      assert.equal(claims.aud, '/admin/');
      assert.ok(claims.exp > claims.iat && claims.exp - claims.iat <= 300);
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ posts: [{ id: 'post-1' }] }));
    } catch (error) {
      res.writeHead(400);
      res.end(error.message);
    }
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');

  const config = {
    name: 'Ghost test', baseUrl: `http://127.0.0.1:${server.address().port}`,
    apiKey, authType: 'ghost_admin',
    tools: [{ name: 'ghost_list_posts', method: 'GET', path: '/ghost/api/admin/posts/?limit=1' }],
  };
  const child = spawn(process.execPath, [new URL('./server.mjs', import.meta.url).pathname], {
    env: { ...process.env, CUSTOM_CONNECTOR_CONFIG: JSON.stringify(config) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr += chunk; });
  try {
    const response = await new Promise((resolve, reject) => {
      let buffer = '';
      const timer = setTimeout(() => reject(new Error('MCP timeout')), 5000);
      child.stdout.on('data', (chunk) => {
        buffer += chunk;
        while (buffer.includes('\n')) {
          const index = buffer.indexOf('\n');
          const message = JSON.parse(buffer.slice(0, index));
          buffer = buffer.slice(index + 1);
          if (message.id === 1) child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: 2, method: 'tools/call', params: { name: 'ghost_list_posts', arguments: {} } }) + '\n');
          if (message.id === 2) {
            clearTimeout(timer);
            resolve(JSON.parse(message.result.content[0].text));
          }
        }
      });
      child.on('error', reject);
      child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} }) + '\n');
    });
    assert.equal(response.status, 200);
    assert.equal(response.body.posts.length, 1);
    assert.ok(!stderr.includes(apiKey));
  } finally {
    child.kill();
    server.close();
  }
});

test('SIGE Cloud sends three auth headers and rejects a login response', async () => {
  let calls = 0;
  const server = createServer((req, res) => {
    calls += 1;
    assert.equal(req.headers['authorization-token'], 'test-token');
    assert.equal(req.headers.user, 'usuario@exemplo.com');
    assert.equal(req.headers.app, 'API');
    if (calls === 1) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify(Array.from({ length: 50 }, (_, id) => ({ id, name: 'x'.repeat(500) }))));
    } else {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end('<html>Login</html>');
    }
  });
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const child = spawn(process.execPath, [new URL('./server.mjs', import.meta.url).pathname], {
    env: { ...process.env, CUSTOM_CONNECTOR_CONFIG: JSON.stringify({
      name: 'SIGE test', baseUrl: `http://127.0.0.1:${server.address().port}`,
      apiKey: 'test-token', apiUser: 'usuario@exemplo.com', apiApp: 'API', authType: 'sige_cloud',
      tools: [{ name: 'empresas', method: 'GET', path: '/request/Empresas/GetTodasEmpresas' }],
    }) },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  try {
    const results = await new Promise((resolve, reject) => {
      const results = [];
      let buffer = '';
      const timer = setTimeout(() => reject(new Error('MCP timeout')), 5000);
      child.stdout.on('data', (chunk) => {
        buffer += chunk;
        while (buffer.includes('\n')) {
          const index = buffer.indexOf('\n');
          const message = JSON.parse(buffer.slice(0, index));
          buffer = buffer.slice(index + 1);
          if (message.id === 1 || message.id === 2) child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: message.id + 1, method: 'tools/call', params: { name: 'empresas', arguments: {} } }) + '\n');
          if (message.id === 2 || message.id === 3) results.push(JSON.parse(message.result.content[0].text));
          if (message.id === 3) { clearTimeout(timer); resolve(results); }
        }
      });
      child.on('error', reject);
      child.stdin.write(JSON.stringify({ jsonrpc: '2.0', id: 1, method: 'initialize', params: {} }) + '\n');
    });
    assert.equal(results[0].ok, true);
    assert.equal(results[0].truncated, true);
    assert.equal(results[0].totalItems, 50);
    assert.ok(results[0].body.length > 0 && results[0].body.length < 50);
    assert.equal(results[1].error, true);
    assert.match(results[1].message, /login/);
  } finally {
    child.kill();
    server.close();
  }
});
