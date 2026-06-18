#!/usr/bin/env node
// MAG OneDrive MCP server (stdio, zero-dependency).
//
// Exposes OneDrive tools to the Hermes agent. The server never persists tokens:
// on every call it asks the MAG control plane for the tenant's connected
// OneDrive accounts and then delegates list/search/create operations to the
// internal MAG OneDrive endpoints.

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-onedrive';
const SERVER_VERSION = '0.1.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';

const MAX_TEXT = 12000;

function log(...args) {
  process.stderr.write(`[mag-onedrive] ${args.join(' ')}\n`);
}

function send(msg) {
  process.stdout.write(JSON.stringify(msg) + '\n');
}

function reply(id, result) {
  send({ jsonrpc: '2.0', id, result });
}

function replyError(id, code, message) {
  send({ jsonrpc: '2.0', id, error: { code, message } });
}

function truncate(s) {
  if (typeof s !== 'string') s = JSON.stringify(s, null, 2);
  return s.length > MAX_TEXT ? s.slice(0, MAX_TEXT) + '\n...[truncated]' : s;
}

function magHeaders() {
  return { 'x-internal-key': MAG_INTERNAL_KEY, 'content-type': 'application/json' };
}

function assertConfigured() {
  if (!MAG_API_URL || !MAG_INTERNAL_KEY || !MAG_TENANT_ID) {
    throw new Error('MCP nao configurado (MAG_API_URL/MAG_INTERNAL_KEY/MAG_TENANT_ID ausentes).');
  }
}

async function magFetch(path, init = {}) {
  assertConfigured();
  const res = await fetch(`${MAG_API_URL}${path}`, {
    ...init,
    headers: { ...magHeaders(), ...(init.headers || {}) },
  });
  const text = await res.text();
  let body;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) {
    const msg = body?.error || (typeof body === 'string' ? body : JSON.stringify(body));
    throw new Error(`MAG API ${res.status}: ${msg}`);
  }
  return body;
}

async function magListAccounts() {
  return magFetch(`/internal/onedrive/accounts?tenantId=${encodeURIComponent(MAG_TENANT_ID)}`);
}

async function resolveAccount(account) {
  const accounts = await magListAccounts();
  if (!accounts.length) {
    throw new Error('Nenhuma conta OneDrive conectada. Conecte uma em Fontes.');
  }

  let chosen;
  if (account) {
    const q = String(account).toLowerCase();
    chosen =
      accounts.find((a) => a.email.toLowerCase() === q) ||
      accounts.find((a) => a.email.toLowerCase().includes(q)) ||
      accounts.find((a) => (a.displayName || '').toLowerCase().includes(q));
    if (!chosen) {
      throw new Error(`Conta "${account}" nao encontrada. Disponiveis: ${accounts.map((a) => a.email).join(', ')}`);
    }
  } else if (accounts.length === 1) {
    chosen = accounts[0];
  } else {
    throw new Error(
      `Ha ${accounts.length} contas OneDrive conectadas; informe "account". Disponiveis: ${accounts.map((a) => a.email).join(', ')}`,
    );
  }

  return chosen;
}

const tools = {
  onedrive_list_accounts: {
    description: 'Lista as contas OneDrive conectadas a este MAG (e-mail, nome, status e permissoes).',
    inputSchema: { type: 'object', properties: {} },
    async run() {
      const accounts = await magListAccounts();
      const lines = accounts.map(
        (a) => `- ${a.email}${a.displayName ? ` (${a.displayName})` : ''} - ${a.status} - escopos: ${a.scopes?.length ?? 0}`,
      );
      return accounts.length ? `Contas OneDrive conectadas:\n${lines.join('\n')}` : 'Nenhuma conta OneDrive conectada.';
    },
  },

  onedrive_list_documents: {
    description: 'Lista arquivos e pastas do OneDrive. Pode receber uma conta e um caminho opcional.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string', description: 'E-mail ou parte do e-mail da conta OneDrive.' },
        path: { type: 'string', description: 'Caminho/pasta a listar. Se omitido, usa a raiz.' },
      },
    },
    async run(args) {
      const account = await resolveAccount(args.account);
      const query = new URLSearchParams({
        tenantId: MAG_TENANT_ID,
        accountId: account.id,
      });
      if (args.path) query.set('path', String(args.path));
      return magFetch(`/internal/onedrive/documents?${query.toString()}`);
    },
  },

  onedrive_search_documents: {
    description: 'Busca arquivos no OneDrive pelo texto informado.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string', description: 'E-mail ou parte do e-mail da conta OneDrive.' },
        query: { type: 'string', description: 'Texto da busca.' },
      },
      required: ['query'],
    },
    async run(args) {
      const account = await resolveAccount(args.account);
      const query = new URLSearchParams({
        tenantId: MAG_TENANT_ID,
        accountId: account.id,
        q: String(args.query),
      });
      return magFetch(`/internal/onedrive/documents/search?${query.toString()}`);
    },
  },

  onedrive_create_folder: {
    description: 'Cria uma pasta no OneDrive. Acao de escrita - confirme com o usuario antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string', description: 'E-mail ou parte do e-mail da conta OneDrive.' },
        folderName: { type: 'string', description: 'Nome da pasta.' },
        parentPath: { type: 'string', description: 'Caminho pai opcional.' },
      },
      required: ['folderName'],
    },
    async run(args) {
      const account = await resolveAccount(args.account);
      return magFetch('/internal/onedrive/folders', {
        method: 'POST',
        body: JSON.stringify({
          tenantId: MAG_TENANT_ID,
          accountId: account.id,
          folderName: String(args.folderName),
          ...(args.parentPath ? { parentPath: String(args.parentPath) } : {}),
        }),
      });
    },
  },

  onedrive_create_document: {
    description: 'Cria um documento texto no OneDrive. Acao de escrita - confirme com o usuario antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string', description: 'E-mail ou parte do e-mail da conta OneDrive.' },
        fileName: { type: 'string', description: 'Nome do arquivo.' },
        content: { type: 'string', description: 'Conteudo do arquivo texto.' },
        parentPath: { type: 'string', description: 'Caminho pai opcional.' },
      },
      required: ['fileName', 'content'],
    },
    async run(args) {
      const account = await resolveAccount(args.account);
      return magFetch('/internal/onedrive/documents', {
        method: 'POST',
        body: JSON.stringify({
          tenantId: MAG_TENANT_ID,
          accountId: account.id,
          fileName: String(args.fileName),
          content: String(args.content),
          ...(args.parentPath ? { parentPath: String(args.parentPath) } : {}),
        }),
      });
    },
  },
};

function toolList() {
  return Object.entries(tools).map(([name, t]) => ({
    name,
    description: t.description,
    inputSchema: t.inputSchema,
  }));
}

async function handleMessage(msg) {
  const { id, method, params } = msg;

  if (id === undefined || id === null) {
    return;
  }

  try {
    if (method === 'initialize') {
      return reply(id, {
        protocolVersion: params?.protocolVersion || PROTOCOL_VERSION,
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
      });
    }
    if (method === 'ping') return reply(id, {});
    if (method === 'tools/list') return reply(id, { tools: toolList() });
    if (method === 'tools/call') {
      const name = params?.name;
      const t = tools[name];
      if (!t) return reply(id, { content: [{ type: 'text', text: `Ferramenta desconhecida: ${name}` }], isError: true });
      try {
        const result = await t.run(params.arguments || {});
        const text = typeof result === 'string' ? result : JSON.stringify(result, null, 2);
        return reply(id, { content: [{ type: 'text', text: truncate(text) }] });
      } catch (err) {
        return reply(id, { content: [{ type: 'text', text: `Erro: ${err.message}` }], isError: true });
      }
    }
    return replyError(id, -32601, `Method not found: ${method}`);
  } catch (err) {
    return replyError(id, -32603, err.message);
  }
}

const rl = createInterface({ input: process.stdin });
rl.on('line', (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;
  let msg;
  try {
    msg = JSON.parse(trimmed);
  } catch {
    log('skip non-JSON line');
    return;
  }
  handleMessage(msg).catch((err) => log('handler error:', err.message));
});

log(`started (api=${MAG_API_URL || 'unset'} tenant=${MAG_TENANT_ID || 'unset'})`);
