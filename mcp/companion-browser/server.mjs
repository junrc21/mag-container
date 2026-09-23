#!/usr/bin/env node
// MAG Companion Browser MCP server (stdio, zero-dependência) — mesmo esqueleto de
// mcp/teammates/server.mjs.
//
// Controla o navegador REAL do computador do cliente (Mac ou Windows), com a sessão
// real dele já logada — via o MAG Companion, nunca direto. Este processo NUNCA fala
// com o Companion: ele só chama de volta o control plane (MAG_API_URL), que mantém o
// canal de verdade (WebSocket) com o Companion e faz a ponte. Mesmo padrão de
// "callback puro" de teammates/mag-ops/c6-bank — nenhuma credencial de navegador,
// sessão ou domínio passa por aqui.
//
// Nome deliberadamente 'companion-browser', NUNCA 'browser': já existe um toolset
// nativo do Hermes chamado 'browser' (Playwright sandboxed dentro do container, sem
// sessão do cliente) — são coisas fisicamente diferentes, e o nome não pode confundir
// quem administra (ver admin-catalog.ts do lado control-plane).
//
// Required env (injected via mcp_servers.companion_browser.env): MAG_API_URL,
// MAG_INTERNAL_KEY, MAG_TENANT_ID.

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-companion-browser';
const SERVER_VERSION = '0.1.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';
const MAX_TEXT = 12000;

function log(...a) {
  process.stderr.write(`[mag-companion-browser] ${a.join(' ')}\n`);
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
  if (!MAG_API_URL || !MAG_INTERNAL_KEY || !MAG_TENANT_ID) {
    throw new Error('MCP não configurado (MAG_API_URL/MAG_INTERNAL_KEY/MAG_TENANT_ID ausentes).');
  }
}

async function callMag(path, options = {}) {
  assertConfigured();
  const res = await fetch(`${MAG_API_URL}${path}`, {
    ...options,
    headers: { 'x-internal-key': MAG_INTERNAL_KEY, 'content-type': 'application/json', ...(options.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    // O errorHandler global do mag-api sempre devolve { error: { code, message } } — um
    // OBJETO, não string (diferente do fallback frouxo que outros MCPs mais antigos
    // deste repo assumem). Ler `.message` é o que dá pro modelo o motivo de verdade
    // (ex.: "Este campo parece ser de senha..."), não um genérico "MAG API 400".
    const message = typeof body.error === 'object' && body.error?.message
      ? body.error.message
      : typeof body.error === 'string'
        ? body.error
        : `MAG API ${res.status}`;
    throw new Error(message);
  }
  return body;
}

/** Despacha uma ação canônica e devolve texto pronto pro modelo — as três formas de resposta
 *  da rota (`done`/`pending_approval`/erro lançado) viram, cada uma, uma frase clara. */
async function dispatch(deviceId, action) {
  const body = await callMag('/internal/browser/actions', {
    method: 'POST',
    body: JSON.stringify({ tenantId: MAG_TENANT_ID, deviceId, action }),
  });
  if (body.status === 'pending_approval') return body.message;
  if (body.status === 'error') return `Erro: ${body.message}`;
  return body.result;
}

function refField(descricao) {
  return { type: 'string', description: `Referência EXATA do elemento, vinda de um browser_snapshot recente (ex.: "e3"). ${descricao}` };
}

// ── tools ───────────────────────────────────────────────────────────────────
// Allowlist FECHADA de propósito: nunca existe uma tool tipo "rodar JS arbitrário" ou
// "avaliar código" aqui — mesmo que o backend real (Playwright, no Windows) exponha
// isso por padrão (confirmado num spike: browser_run_code_unsafe/browser_evaluate
// aparecem na listagem sem nenhuma flag escondendo). A trava é isto aqui não existir,
// não uma configuração que promete escondê-las.
const DEVICE_ID_FIELD = { type: 'string', description: 'O deviceId informado no início desta conversa — nunca invente nem reaproveite de outra.' };

const tools = {
  browser_navigate: {
    description: 'Abre uma URL no navegador real do computador do usuário.',
    inputSchema: {
      type: 'object',
      properties: { deviceId: DEVICE_ID_FIELD, url: { type: 'string', description: 'URL completa, com https://.' } },
      required: ['deviceId', 'url'],
    },
    run: (a) => dispatch(a.deviceId, { type: 'navigate', url: a.url }),
  },

  browser_go_back: {
    description: 'Volta para a página anterior no histórico do navegador.',
    inputSchema: { type: 'object', properties: { deviceId: DEVICE_ID_FIELD }, required: ['deviceId'] },
    run: (a) => dispatch(a.deviceId, { type: 'go_back' }),
  },

  browser_snapshot: {
    description:
      'Lê a página atual: devolve o texto e a lista de elementos interativos (botões, links, campos), cada um com ' +
      'uma referência (ref) — use essa ref em browser_click/browser_type. SEMPRE tire um snapshot novo antes de ' +
      'clicar ou digitar se a página pode ter mudado (depois de navegar, ou depois de um clique anterior).',
    inputSchema: { type: 'object', properties: { deviceId: DEVICE_ID_FIELD }, required: ['deviceId'] },
    run: (a) => dispatch(a.deviceId, { type: 'snapshot' }),
  },

  browser_find: {
    description: 'Procura um texto (ou regex) na página atual — mais barato que um snapshot inteiro quando você só precisa achar algo específico.',
    inputSchema: {
      type: 'object',
      properties: {
        deviceId: DEVICE_ID_FIELD,
        text: { type: 'string', description: 'Texto a procurar (case-insensitive). Use isto OU regex, não os dois.' },
        regex: { type: 'string', description: 'Expressão regular a procurar. Use isto OU text, não os dois.' },
      },
      required: ['deviceId'],
    },
    run: (a) => dispatch(a.deviceId, { type: 'find', ...(a.text ? { text: a.text } : {}), ...(a.regex ? { regex: a.regex } : {}) }),
  },

  browser_wait_for: {
    description: 'Espera um texto aparecer ou desaparecer na página (ex.: depois de enviar um formulário), ou espera N segundos.',
    inputSchema: {
      type: 'object',
      properties: {
        deviceId: DEVICE_ID_FIELD,
        text: { type: 'string', description: 'Espera este texto APARECER.' },
        textGone: { type: 'string', description: 'Espera este texto DESAPARECER.' },
        seconds: { type: 'number', description: 'Espera fixa, em segundos (máximo 60).' },
      },
      required: ['deviceId'],
    },
    run: (a) => dispatch(a.deviceId, {
      type: 'wait_for',
      ...(a.text ? { text: a.text } : {}),
      ...(a.textGone ? { textGone: a.textGone } : {}),
      ...(typeof a.seconds === 'number' ? { seconds: a.seconds } : {}),
    }),
  },

  browser_list_tabs: {
    description: 'Lista as abas abertas no navegador, com qual está ativa.',
    inputSchema: { type: 'object', properties: { deviceId: DEVICE_ID_FIELD }, required: ['deviceId'] },
    run: (a) => dispatch(a.deviceId, { type: 'list_tabs' }),
  },

  browser_switch_tab: {
    description: 'Troca para outra aba já aberta, pelo índice devolvido por browser_list_tabs.',
    inputSchema: {
      type: 'object',
      properties: { deviceId: DEVICE_ID_FIELD, index: { type: 'number', description: 'Índice da aba (de browser_list_tabs).' } },
      required: ['deviceId', 'index'],
    },
    run: (a) => dispatch(a.deviceId, { type: 'switch_tab', index: a.index }),
  },

  browser_click: {
    description:
      'Clica num elemento da página — a ref precisa vir de um browser_snapshot recente. Pode pedir aprovação do ' +
      'usuário se for a primeira vez interagindo com este site, ou ser recusado sem opção de aprovar (ex.: campo ' +
      'de senha) — nesses casos, avise o usuário em vez de tentar de novo sozinho.',
    inputSchema: {
      type: 'object',
      properties: {
        deviceId: DEVICE_ID_FIELD,
        ref: refField('O botão/link a clicar.'),
        element: { type: 'string', description: 'Descrição humana do elemento (ex.: "botão Enviar") — ajuda a explicar ao usuário o que foi clicado.' },
      },
      required: ['deviceId', 'ref'],
    },
    run: (a) => dispatch(a.deviceId, { type: 'click', ref: a.ref, ...(a.element ? { element: a.element } : {}) }),
  },

  browser_type: {
    description:
      'Digita texto num campo da página — a ref precisa vir de um browser_snapshot recente. NUNCA use isto para ' +
      'senha, número de cartão ou documento — o servidor recusa esses campos incondicionalmente, mas nem tente.',
    inputSchema: {
      type: 'object',
      properties: {
        deviceId: DEVICE_ID_FIELD,
        ref: refField('O campo a preencher.'),
        text: { type: 'string', description: 'Texto a digitar.' },
        submit: { type: 'boolean', description: 'Se true, envia Enter depois de digitar.' },
        element: { type: 'string', description: 'Descrição humana do campo (ex.: "campo Nome").' },
      },
      required: ['deviceId', 'ref', 'text'],
    },
    run: (a) => dispatch(a.deviceId, {
      type: 'type',
      ref: a.ref,
      text: a.text,
      ...(typeof a.submit === 'boolean' ? { submit: a.submit } : {}),
      ...(a.element ? { element: a.element } : {}),
    }),
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

log(`started (api=${MAG_API_URL || 'unset'} tenant=${MAG_TENANT_ID || 'unset'})`);
