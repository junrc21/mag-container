#!/usr/bin/env node
// MAG Companion Browser MCP server (stdio, zero-dependency).
//
// This server runs inside the tenant runtime and delegates browser actions to
// the paired MAG Companion through the MAG control plane.

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-companion-browser';
const SERVER_VERSION = '0.1.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';
const MAX_TEXT = 20000;
const DEFAULT_TIMEOUT_MS = 65000;

function log(...args) {
  process.stderr.write(`[mag-companion-browser] ${args.join(' ')}\n`);
}

function send(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

function reply(id, result) {
  send({ jsonrpc: '2.0', id, result });
}

function replyError(id, code, message) {
  send({ jsonrpc: '2.0', id, error: { code, message } });
}

function textResult(value, isError = false) {
  const text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  return {
    content: [{ type: 'text', text: text.length > MAX_TEXT ? `${text.slice(0, MAX_TEXT)}\n...[truncated]` : text }],
    isError,
  };
}

function assertConfigured() {
  if (!MAG_API_URL || !MAG_INTERNAL_KEY || !MAG_TENANT_ID) {
    throw new Error('Controle de navegador indisponivel: runtime sem MAG_API_URL/MAG_INTERNAL_KEY/MAG_TENANT_ID.');
  }
}

function requireDeviceId(args) {
  const deviceId = String(args.deviceId || '').trim();
  if (!deviceId) throw new Error('Informe deviceId do computador pareado.');
  return deviceId;
}

async function dispatch(deviceId, action, sessionId) {
  assertConfigured();
  let response;
  try {
    response = await fetch(`${MAG_API_URL}/internal/browser/actions`, {
      method: 'POST',
      headers: {
        'x-internal-key': MAG_INTERNAL_KEY,
        'content-type': 'application/json',
      },
      body: JSON.stringify({
        tenantId: MAG_TENANT_ID,
        deviceId,
        action,
        ...(sessionId ? { sessionId } : {}),
      }),
      signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
    });
  } catch {
    throw new Error('Nao consegui falar com o MAG Companion agora. Verifique se o app esta aberto e conectado.');
  }

  const text = await response.text();
  let body;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { raw: text };
  }

  if (response.status === 202) {
    return body;
  }
  if (!response.ok) {
    const message = body?.message || body?.error || text || `MAG API ${response.status}`;
    throw new Error(message);
  }
  return body.result ?? body;
}

const commonDevice = {
  deviceId: { type: 'string', description: 'ID do computador pareado no MAG Companion.' },
  sessionId: { type: 'string', description: 'ID opcional da sessao de navegador.' },
};

const tools = {
  browser_navigate: {
    description: 'Abre uma URL no Chrome controlado pelo MAG Companion.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        ...commonDevice,
        url: { type: 'string', description: 'URL completa, incluindo https://.' },
      },
      required: ['deviceId', 'url'],
    },
    run: (args) => dispatch(requireDeviceId(args), { type: 'navigate', url: String(args.url) }, args.sessionId),
  },

  browser_go_back: {
    description: 'Volta uma pagina no Chrome controlado pelo MAG Companion.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: commonDevice,
      required: ['deviceId'],
    },
    run: (args) => dispatch(requireDeviceId(args), { type: 'go_back' }, args.sessionId),
  },

  browser_snapshot: {
    description: 'Le a pagina atual do Chrome e retorna texto e refs de elementos interativos.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: commonDevice,
      required: ['deviceId'],
    },
    run: (args) => dispatch(requireDeviceId(args), { type: 'snapshot' }, args.sessionId),
  },

  browser_tabs: {
    description: 'Lista as abas abertas ou troca para uma aba por indice.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        ...commonDevice,
        action: { type: 'string', enum: ['list', 'select'], description: 'Use list para listar abas ou select para trocar.' },
        index: { type: 'integer', minimum: 0, description: 'Indice da aba quando action=select.' },
      },
      required: ['deviceId', 'action'],
    },
    run(args) {
      const action = args.action === 'select' ? { type: 'switch_tab', index: Number(args.index ?? 0) } : { type: 'list_tabs' };
      return dispatch(requireDeviceId(args), action, args.sessionId);
    },
  },

  browser_click: {
    description: 'Clica em um elemento usando uma ref obtida via browser_snapshot.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        ...commonDevice,
        ref: { type: 'string' },
        element: { type: 'string', description: 'Descricao opcional do elemento.' },
      },
      required: ['deviceId', 'ref'],
    },
    run: (args) => dispatch(requireDeviceId(args), { type: 'click', ref: String(args.ref), ...(args.element ? { element: String(args.element) } : {}) }, args.sessionId),
  },

  browser_type: {
    description: 'Digita texto em um elemento usando uma ref obtida via browser_snapshot.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        ...commonDevice,
        ref: { type: 'string' },
        text: { type: 'string' },
        submit: { type: 'boolean' },
        element: { type: 'string', description: 'Descricao opcional do elemento.' },
      },
      required: ['deviceId', 'ref', 'text'],
    },
    run: (args) =>
      dispatch(
        requireDeviceId(args),
        {
          type: 'type',
          ref: String(args.ref),
          text: String(args.text),
          ...(args.submit !== undefined ? { submit: Boolean(args.submit) } : {}),
          ...(args.element ? { element: String(args.element) } : {}),
        },
        args.sessionId,
      ),
  },

  browser_find: {
    description: 'Procura texto ou regex na pagina atual.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        ...commonDevice,
        text: { type: 'string' },
        regex: { type: 'string' },
      },
      required: ['deviceId'],
    },
    run: (args) =>
      dispatch(
        requireDeviceId(args),
        { type: 'find', ...(args.text ? { text: String(args.text) } : {}), ...(args.regex ? { regex: String(args.regex) } : {}) },
        args.sessionId,
      ),
  },

  browser_wait_for: {
    description: 'Espera texto aparecer, sumir ou aguarda alguns segundos.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        ...commonDevice,
        text: { type: 'string' },
        textGone: { type: 'string' },
        seconds: { type: 'number', minimum: 0, maximum: 60 },
      },
      required: ['deviceId'],
    },
    run: (args) =>
      dispatch(
        requireDeviceId(args),
        {
          type: 'wait_for',
          ...(args.text ? { text: String(args.text) } : {}),
          ...(args.textGone ? { textGone: String(args.textGone) } : {}),
          ...(args.seconds !== undefined ? { seconds: Number(args.seconds) } : {}),
        },
        args.sessionId,
      ),
  },
};

function toolList() {
  return Object.entries(tools).map(([name, tool]) => ({
    name,
    description: tool.description,
    inputSchema: tool.inputSchema,
  }));
}

async function handle(message) {
  const { id, method, params } = message;

  if (method === 'initialize') {
    return reply(id, {
      protocolVersion: params?.protocolVersion || PROTOCOL_VERSION,
      capabilities: { tools: { listChanged: false } },
      serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
    });
  }
  if (method === 'notifications/initialized') return;
  if (method === 'ping') return reply(id, {});
  if (method === 'tools/list') return reply(id, { tools: toolList() });
  if (method === 'tools/call') {
    const tool = tools[params?.name];
    if (!tool) return reply(id, textResult(`Ferramenta desconhecida: ${params?.name}`, true));
    try {
      return reply(id, textResult(await tool.run(params?.arguments || {})));
    } catch (err) {
      return reply(id, textResult(err instanceof Error ? err.message : 'Controle de navegador indisponivel.', true));
    }
  }
  if (id !== undefined && id !== null) replyError(id, -32601, `Metodo nao suportado: ${method}`);
}

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on('line', async (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;
  try {
    await handle(JSON.parse(trimmed));
  } catch (err) {
    log('handler error:', err instanceof Error ? err.message : String(err));
    replyError(null, -32700, 'Mensagem MCP invalida.');
  }
});

log(`started (api=${MAG_API_URL || 'unset'} tenant=${MAG_TENANT_ID || 'unset'})`);
