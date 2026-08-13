#!/usr/bin/env node
// MAG Help Center MCP server (stdio, zero-dependency).
//
// Deixa o agente CONSULTAR a central de ajuda do produto em vez de improvisar. Duas
// ferramentas: search_help (acha as páginas relevantes) e read_help_page (lê uma delas
// inteira).
//
// Por que isto existe: o SOUL já carrega um inventário de capacidades com os links dos
// guias, e o bloco de sigilo do produto manda redirecionar a pessoa para a documentação
// oficial. Só que até agora o agente tinha os links e nenhuma forma de abri-los — sabia
// para onde apontar e não sabia o que estava escrito lá. Resultado: ou respondia raso
// ("está no guia") ou inventava um passo a passo que podia estar errado depois de qualquer
// mudança no produto.
//
// A busca vive no próprio control-center (Next.js), onde o conteúdo dos guias mora. Não
// passa pela mag-api de propósito: `api/` é outro pacote e não consegue importar
// `control-center/lib/docs/`, então servir por lá exigiria uma cópia do conteúdo — que
// desencontraria da original em semanas.
//
// Required env (injected via mcp_servers.helpcenter.env): MAG_DOC_URL.

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-helpcenter';
const SERVER_VERSION = '0.1.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_DOC_URL = (process.env.MAG_DOC_URL || '').replace(/\/$/, '');
const MAX_TEXT = 12000;
// A ajuda é conteúdo estático; se não respondeu rápido, é melhor o agente seguir sem ela
// do que travar o turno inteiro esperando.
const TIMEOUT_MS = 8000;

function log(...a) {
  process.stderr.write(`[mag-helpcenter] ${a.join(' ')}\n`);
}
function send(m) {
  process.stdout.write(JSON.stringify(m) + '\n');
}
function reply(id, result) {
  send({ jsonrpc: '2.0', id, result });
}
function replyError(id, code, message) {
  send({ jsonrpc: '2.0', id, error: { code, message } });
}
function truncate(s) {
  if (typeof s !== 'string') s = JSON.stringify(s, null, 2);
  return s.length > MAX_TEXT ? s.slice(0, MAX_TEXT) + '\n…[truncado]' : s;
}

function assertConfigured() {
  if (!MAG_DOC_URL) {
    throw new Error('Central de ajuda não configurada neste ambiente.');
  }
}

async function callDocs(path) {
  assertConfigured();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${MAG_DOC_URL}${path}`, {
      headers: { accept: 'application/json' },
      signal: controller.signal,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(typeof body.error === 'string' ? body.error : 'Não consegui consultar a ajuda agora.');
    }
    return body;
  } catch (err) {
    // Nunca vazar detalhe de rede/infra para o canal do cliente (barreira de sigilo).
    if (err.name === 'AbortError') throw new Error('A ajuda demorou demais para responder.');
    if (err instanceof TypeError) throw new Error('Não consegui consultar a ajuda agora.');
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

/** Os links precisam sair absolutos: o cliente vai clicar neles no Telegram/WhatsApp. */
function absolute(url) {
  return `${MAG_DOC_URL}${url}`;
}

// ── tools ───────────────────────────────────────────────────────────────────

const tools = {
  search_help: {
    description:
      'Busca na central de ajuda oficial da MAG. Use SEMPRE que perguntarem como fazer algo no produto ' +
      '(conectar canal, criar rotina, subir documento, entender crédito, resolver um problema) — em vez de ' +
      'responder de cabeça. Devolve as páginas mais relevantes com um trecho de cada. Depois de escolher, ' +
      'chame read_help_page para ler a página inteira antes de explicar o passo a passo.',
    inputSchema: {
      type: 'object',
      required: ['query'],
      additionalProperties: false,
      properties: {
        query: {
          type: 'string',
          minLength: 2,
          description: 'O que a pessoa quer resolver, com as palavras dela. Ex.: "conectar whatsapp", "rotina não rodou".',
        },
      },
    },
    async run({ query }) {
      const body = await callDocs(`/api/docs/search?q=${encodeURIComponent(String(query || '').trim())}`);
      const results = Array.isArray(body.results) ? body.results : [];
      if (!results.length) {
        return 'Nenhum guia encontrado para essa busca. Tente outras palavras, ou responda com o que você já sabe e ofereça o suporte.';
      }
      return results.map((r) => ({
        path: r.path,
        titulo: r.title,
        resumo: r.summary,
        trecho: r.snippet,
        link: absolute(r.url),
      }));
    },
  },

  read_help_page: {
    description:
      'Lê o conteúdo completo de uma página da central de ajuda, pelo caminho devolvido por search_help ' +
      '(ex.: "canais/whatsapp"). Use antes de explicar um passo a passo, para responder o que o guia ' +
      'REALMENTE diz — nunca invente etapas de configuração.',
    inputSchema: {
      type: 'object',
      required: ['path'],
      additionalProperties: false,
      properties: {
        path: {
          type: 'string',
          minLength: 2,
          description: 'Caminho da página, sem /docs na frente. Ex.: "canais/telegram" ou "receitas/briefing-das-8h".',
        },
      },
    },
    async run({ path }) {
      const clean = String(path || '')
        .trim()
        .replace(/^\/?(docs\/)?/, '')
        .replace(/\/+$/, '');
      const body = await callDocs(`/api/docs/page?path=${encodeURIComponent(clean)}`);
      return {
        titulo: body.title,
        resumo: body.summary,
        conteudo: body.content,
        link: absolute(body.url),
        ...(Array.isArray(body.children) && body.children.length
          ? { subpaginas: body.children.map((c) => ({ path: c.path, titulo: c.title })) }
          : {}),
      };
    },
  },
};

function toolList() {
  return Object.entries(tools).map(([name, t]) => ({ name, description: t.description, inputSchema: t.inputSchema }));
}

async function handleMessage(msg) {
  const { id, method, params } = msg;
  if (id === undefined || id === null) return;
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
      const t = tools[params?.name];
      if (!t) return reply(id, { content: [{ type: 'text', text: `Ferramenta desconhecida: ${params?.name}` }], isError: true });
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
  const t = line.trim();
  if (!t) return;
  let msg;
  try {
    msg = JSON.parse(t);
  } catch {
    return;
  }
  handleMessage(msg).catch((e) => log('handler error:', e.message));
});

log(`started (docs=${MAG_DOC_URL || 'unset'})`);
