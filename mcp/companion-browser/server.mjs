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
const MAX_TEXT = 20000;
// Acima do maior timeout de ação que a rota interna usa (wait_for = 60s) com folga de
// rede — sem isto, uma conexão que trava (não um erro, um hang de verdade) deixaria
// este processo pendurado até o tool_timeout de 300s do próprio Hermes, sem nenhuma
// mensagem útil no meio.
const FETCH_TIMEOUT_MS = 65_000;

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

/**
 * A rota interna devolve erro em DUAS formas diferentes, dependendo de ONDE ele nasce
 * (achado ao reconciliar duas implementações independentes desta MCP — nenhuma das duas
 * cobria as duas formas sozinha):
 *   1. Erro lançado antes do dispatch (device não encontrado, controle desligado, ação
 *      recusada pela política) passa pelo errorHandler GLOBAL do mag-api, que sempre
 *      devolve `{ error: { code, message } }` — um OBJETO.
 *   2. Erro do dispatch em si (offline, timeout, abortado pelo usuário) é capturado à
 *      mão na própria rota e devolvido como `{ status: 'error', code, message }` — aqui
 *      `message` já é STRING, direto na raiz do corpo.
 * Checar as duas, nesta ordem, é o que garante a frase de verdade (ex.: "A pessoa pediu
 * para parar.") chegando ao modelo nos dois casos, em vez de um genérico "MAG API 503".
 */
function extractErrorMessage(body, res) {
  if (typeof body.message === 'string' && body.message) return body.message;
  if (typeof body.error === 'string' && body.error) return body.error;
  if (body.error && typeof body.error === 'object' && typeof body.error.message === 'string') return body.error.message;
  return `MAG API ${res.status}`;
}

async function callMag(path, options = {}) {
  assertConfigured();
  let res;
  try {
    res = await fetch(`${MAG_API_URL}${path}`, {
      ...options,
      headers: { 'x-internal-key': MAG_INTERNAL_KEY, 'content-type': 'application/json', ...(options.headers || {}) },
      signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
    });
  } catch (err) {
    throw new Error(err?.name === 'TimeoutError' ? 'O MAG API não respondeu a tempo.' : 'Não consegui falar com o MAG API agora.');
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractErrorMessage(body, res));
  return body;
}

/** Despacha uma ação canônica e devolve texto pronto pro modelo — as três formas de resposta
 *  da rota (`done`/`pending_approval`/erro lançado) viram, cada uma, uma frase clara. */
async function dispatch(deviceId, action, sessionId) {
  const body = await callMag('/internal/browser/actions', {
    method: 'POST',
    body: JSON.stringify({ tenantId: MAG_TENANT_ID, deviceId, action, ...(sessionId ? { sessionId } : {}) }),
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
// não uma configuração que promete escondê-las. `additionalProperties: false` em cada
// schema é a mesma disciplina um nível abaixo: um argumento extra que o modelo inventar
// é rejeitado na hora, não silenciosamente ignorado.
const DEVICE_ID_FIELD = { type: 'string', description: 'O deviceId informado no início desta conversa — nunca invente nem reaproveite de outra.' };
const SESSION_ID_FIELD = { type: 'string', description: 'Opcional. Só use se uma chamada anterior nesta mesma tarefa tiver devolvido um sessionId explícito.' };

const tools = {
  browser_navigate: {
    description:
      'Abre uma URL no navegador real do computador do usuário — sempre o navegador padrão do sistema dele, ' +
      'abrindo-o se estiver fechado. Se o padrão não for Chrome nem Safari, a chamada falha avisando qual é o ' +
      'padrão detectado e quais dessas duas opções estão instaladas: pergunte ao usuário qual prefere e repita a ' +
      'chamada com o parâmetro browser preenchido — nunca escolha sozinho nem insista sem perguntar.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        deviceId: DEVICE_ID_FIELD,
        url: { type: 'string', description: 'URL completa, com https://.' },
        browser: {
          type: 'string',
          enum: ['safari', 'chrome'],
          description: 'Só preencha depois que uma chamada anterior recusou o navegador padrão e o usuário escolheu entre as opções oferecidas.',
        },
        sessionId: SESSION_ID_FIELD,
      },
      required: ['deviceId', 'url'],
    },
    run: (a) => dispatch(a.deviceId, { type: 'navigate', url: a.url, ...(a.browser ? { browser: a.browser } : {}) }, a.sessionId),
  },

  browser_go_back: {
    description: 'Volta para a página anterior no histórico do navegador.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: { deviceId: DEVICE_ID_FIELD, sessionId: SESSION_ID_FIELD },
      required: ['deviceId'],
    },
    run: (a) => dispatch(a.deviceId, { type: 'go_back' }, a.sessionId),
  },

  browser_snapshot: {
    description:
      'Lê a página atual: devolve o texto e a lista de elementos interativos (botões, links, campos), cada um com ' +
      'uma referência (ref) — use essa ref em browser_click/browser_type. SEMPRE tire um snapshot novo antes de ' +
      'clicar ou digitar se a página pode ter mudado (depois de navegar, ou depois de um clique anterior).',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: { deviceId: DEVICE_ID_FIELD, sessionId: SESSION_ID_FIELD },
      required: ['deviceId'],
    },
    run: (a) => dispatch(a.deviceId, { type: 'snapshot' }, a.sessionId),
  },

  browser_find: {
    description: 'Procura um texto (ou regex) na página atual — mais barato que um snapshot inteiro quando você só precisa achar algo específico.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        deviceId: DEVICE_ID_FIELD,
        text: { type: 'string', description: 'Texto a procurar (case-insensitive). Use isto OU regex, não os dois.' },
        regex: { type: 'string', description: 'Expressão regular a procurar. Use isto OU text, não os dois.' },
        sessionId: SESSION_ID_FIELD,
      },
      required: ['deviceId'],
    },
    run: (a) => dispatch(a.deviceId, { type: 'find', ...(a.text ? { text: a.text } : {}), ...(a.regex ? { regex: a.regex } : {}) }, a.sessionId),
  },

  browser_wait_for: {
    description: 'Espera um texto aparecer ou desaparecer na página (ex.: depois de enviar um formulário), ou espera N segundos.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        deviceId: DEVICE_ID_FIELD,
        text: { type: 'string', description: 'Espera este texto APARECER.' },
        textGone: { type: 'string', description: 'Espera este texto DESAPARECER.' },
        seconds: { type: 'number', description: 'Espera fixa, em segundos (máximo 60).' },
        sessionId: SESSION_ID_FIELD,
      },
      required: ['deviceId'],
    },
    run: (a) => dispatch(a.deviceId, {
      type: 'wait_for',
      ...(a.text ? { text: a.text } : {}),
      ...(a.textGone ? { textGone: a.textGone } : {}),
      ...(typeof a.seconds === 'number' ? { seconds: a.seconds } : {}),
    }, a.sessionId),
  },

  browser_list_tabs: {
    description: 'Lista as abas abertas no navegador, com qual está ativa.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: { deviceId: DEVICE_ID_FIELD, sessionId: SESSION_ID_FIELD },
      required: ['deviceId'],
    },
    run: (a) => dispatch(a.deviceId, { type: 'list_tabs' }, a.sessionId),
  },

  browser_switch_tab: {
    description: 'Troca para outra aba já aberta, pelo índice devolvido por browser_list_tabs.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        deviceId: DEVICE_ID_FIELD,
        index: { type: 'number', description: 'Índice da aba (de browser_list_tabs).' },
        sessionId: SESSION_ID_FIELD,
      },
      required: ['deviceId', 'index'],
    },
    run: (a) => dispatch(a.deviceId, { type: 'switch_tab', index: a.index }, a.sessionId),
  },

  browser_click: {
    description:
      'Clica num elemento da página — a ref precisa vir de um browser_snapshot recente. Pode pedir aprovação do ' +
      'usuário se for a primeira vez interagindo com este site, ou ser recusado sem opção de aprovar (ex.: campo ' +
      'de senha) — nesses casos, avise o usuário em vez de tentar de novo sozinho.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        deviceId: DEVICE_ID_FIELD,
        ref: refField('O botão/link a clicar.'),
        element: { type: 'string', description: 'Descrição humana do elemento (ex.: "botão Enviar") — ajuda a explicar ao usuário o que foi clicado.' },
        sessionId: SESSION_ID_FIELD,
      },
      required: ['deviceId', 'ref'],
    },
    run: (a) => dispatch(a.deviceId, { type: 'click', ref: a.ref, ...(a.element ? { element: a.element } : {}) }, a.sessionId),
  },

  browser_type: {
    description:
      'Digita texto num campo da página — a ref precisa vir de um browser_snapshot recente. NUNCA use isto para ' +
      'senha, número de cartão ou documento — o servidor recusa esses campos incondicionalmente, mas nem tente.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        deviceId: DEVICE_ID_FIELD,
        ref: refField('O campo a preencher.'),
        text: { type: 'string', description: 'Texto a digitar.' },
        submit: { type: 'boolean', description: 'Se true, envia Enter depois de digitar.' },
        element: { type: 'string', description: 'Descrição humana do campo (ex.: "campo Nome").' },
        sessionId: SESSION_ID_FIELD,
      },
      required: ['deviceId', 'ref', 'text'],
    },
    run: (a) => dispatch(a.deviceId, {
      type: 'type',
      ref: a.ref,
      text: a.text,
      ...(typeof a.submit === 'boolean' ? { submit: a.submit } : {}),
      ...(a.element ? { element: a.element } : {}),
    }, a.sessionId),
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

// `crlfDelay: Infinity` trata `\r\n` como um delimitador só — sem isto, um pipe no
// Windows (onde CRLF é comum) pode entregar uma linha vazia a mais entre mensagens.
const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
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
