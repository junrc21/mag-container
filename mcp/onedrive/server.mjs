#!/usr/bin/env node
// MAG OneDrive MCP server (stdio, zero-dependency).
//
// Exposes OneDrive tools to the Hermes agent. The server never persists tokens:
// on every call it asks the MAG control plane for the tenant's connected
// OneDrive accounts and then delegates list/search/create operations to the
// internal MAG OneDrive endpoints.

import { createInterface } from 'node:readline';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

const SERVER_NAME = 'mag-onedrive';
const SERVER_VERSION = '0.2.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';

const MAX_TEXT = 12000;

// Same convention as the google MCP server: binary content gets written into the
// tenant's workspace and the tool tells the agent to include a MEDIA:<path> line
// in its own reply, which the channel adapter turns into a real attachment.
const WORKSPACE_DIR = '/opt/data/workspace/onedrive';

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

  onedrive_update_document: {
    description: 'Atualiza o conteudo e/ou o nome de um documento existente no OneDrive (pelo id, ver onedrive_list_documents/onedrive_search_documents). Nao funciona para pastas. Acao de escrita - confirme com o usuario antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string', description: 'E-mail ou parte do e-mail da conta OneDrive.' },
        fileId: { type: 'string', description: 'Id do arquivo.' },
        content: { type: 'string', description: 'Novo conteudo - substitui o conteudo atual do arquivo.' },
        name: { type: 'string', description: 'Novo nome, se quiser renomear.' },
      },
      required: ['fileId'],
    },
    async run(args) {
      if (args.content === undefined && !args.name) {
        throw new Error('Informe "content" e/ou "name" para atualizar.');
      }
      const account = await resolveAccount(args.account);
      return magFetch(`/internal/onedrive/documents/${encodeURIComponent(args.fileId)}`, {
        method: 'PATCH',
        body: JSON.stringify({
          tenantId: MAG_TENANT_ID,
          accountId: account.id,
          ...(args.content !== undefined ? { content: String(args.content) } : {}),
          ...(args.name ? { name: String(args.name) } : {}),
        }),
      });
    },
  },

  onedrive_get_document_content: {
    description: 'Le o conteudo de um arquivo especifico do OneDrive (pelo id, ver onedrive_list_documents/onedrive_search_documents). Arquivos de texto vem prontos pra ler; arquivos binarios (PDF, imagem, etc.) sao salvos no workspace do tenant - inclua uma linha MEDIA:<caminho> na sua resposta pra entregar o arquivo ao usuario no chat.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string', description: 'E-mail ou parte do e-mail da conta OneDrive.' },
        fileId: { type: 'string', description: 'Id do arquivo.' },
      },
      required: ['fileId'],
    },
    async run(args) {
      const account = await resolveAccount(args.account);
      const query = new URLSearchParams({ tenantId: MAG_TENANT_ID, accountId: account.id });
      const result = await magFetch(`/internal/onedrive/documents/${encodeURIComponent(args.fileId)}/content?${query.toString()}`);
      if (result.isText) {
        return { id: result.id, name: result.name, mimeType: result.mimeType, content: result.content };
      }
      const buf = Buffer.from(result.content, 'base64');
      await mkdir(WORKSPACE_DIR, { recursive: true });
      const safeName = String(result.name).replace(/[/\\]/g, '_');
      const outPath = path.join(WORKSPACE_DIR, `${Date.now()}-${safeName}`);
      await writeFile(outPath, buf);
      return `Arquivo binario salvo. Para enviar ao usuario, inclua na sua resposta:\nMEDIA:${outPath}`;
    },
  },

  onedrive_share_document: {
    description: 'Compartilha um arquivo do OneDrive - com uma pessoa especifica (por e-mail) e/ou gera um link acessivel a qualquer um que o tiver. Informe "email" e/ou "anyoneWithLink":true. Acao de escrita - confirme com o usuario antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        fileId: { type: 'string' },
        email: { type: 'string', description: 'E-mail da pessoa pra compartilhar diretamente.' },
        role: { type: 'string', description: '"read" (so ve) ou "write" (edita). Padrao "read".' },
        anyoneWithLink: { type: 'boolean', description: 'Se true, cria um link acessivel a qualquer pessoa que o tiver.' },
        notify: { type: 'boolean', description: 'Se true (padrao), avisa a pessoa por e-mail ao compartilhar diretamente.' },
      },
      required: ['fileId'],
    },
    async run(args) {
      if (!args.email && !args.anyoneWithLink) {
        throw new Error('Informe "email" e/ou "anyoneWithLink": true - pelo menos um dos dois.');
      }
      const account = await resolveAccount(args.account);
      return magFetch(`/internal/onedrive/documents/${encodeURIComponent(args.fileId)}/share`, {
        method: 'POST',
        body: JSON.stringify({
          tenantId: MAG_TENANT_ID,
          accountId: account.id,
          ...(args.email ? { email: String(args.email) } : {}),
          ...(args.role ? { role: String(args.role) } : {}),
          ...(args.anyoneWithLink !== undefined ? { anyoneWithLink: !!args.anyoneWithLink } : {}),
          ...(args.notify !== undefined ? { notify: !!args.notify } : {}),
        }),
      });
    },
  },

  onedrive_delete_document: {
    description: 'Move um arquivo do OneDrive para a lixeira (reversivel pelo usuario la - nao e uma exclusao permanente). Acao de escrita - confirme com o usuario antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        fileId: { type: 'string' },
      },
      required: ['fileId'],
    },
    async run(args) {
      const account = await resolveAccount(args.account);
      const query = new URLSearchParams({ tenantId: MAG_TENANT_ID, accountId: account.id });
      return magFetch(`/internal/onedrive/documents/${encodeURIComponent(args.fileId)}?${query.toString()}`, {
        method: 'DELETE',
      });
    },
  },

  onedrive_move_document: {
    description: 'Move um arquivo do OneDrive pra outra pasta. Acao de escrita - confirme com o usuario antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        fileId: { type: 'string' },
        parentPath: { type: 'string', description: 'Caminho da pasta de destino.' },
      },
      required: ['fileId', 'parentPath'],
    },
    async run(args) {
      const account = await resolveAccount(args.account);
      return magFetch(`/internal/onedrive/documents/${encodeURIComponent(args.fileId)}/move`, {
        method: 'POST',
        body: JSON.stringify({
          tenantId: MAG_TENANT_ID,
          accountId: account.id,
          parentPath: String(args.parentPath),
        }),
      });
    },
  },

  onedrive_copy_document: {
    description: 'Duplica um arquivo no OneDrive, opcionalmente com novo nome e/ou pasta de destino. Acao de escrita - confirme com o usuario antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        fileId: { type: 'string' },
        name: { type: 'string', description: 'Nome da copia (padrao: OneDrive gera um nome).' },
        parentPath: { type: 'string', description: 'Caminho da pasta de destino (opcional).' },
      },
      required: ['fileId'],
    },
    async run(args) {
      const account = await resolveAccount(args.account);
      return magFetch(`/internal/onedrive/documents/${encodeURIComponent(args.fileId)}/copy`, {
        method: 'POST',
        body: JSON.stringify({
          tenantId: MAG_TENANT_ID,
          accountId: account.id,
          ...(args.name ? { name: String(args.name) } : {}),
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
