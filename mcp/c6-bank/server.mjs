#!/usr/bin/env node
// MAG C6 Bank MCP server (stdio, zero-dependency).
//
// Exposes Pix billing tools to the Hermes agent. The runtime never receives
// bank credentials: every operation is delegated to the MAG control plane,
// which owns the encrypted C6 credentials and persists charges/receipts.

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-c6-bank';
const SERVER_VERSION = '0.1.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_SLUG = process.env.MAG_TENANT_SLUG || '';

const MAX_TEXT = 12000;

function log(...args) {
  process.stderr.write(`[mag-c6-bank] ${args.join(' ')}\n`);
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

function assertConfigured() {
  if (!MAG_API_URL || !MAG_INTERNAL_KEY || !MAG_TENANT_SLUG) {
    throw new Error('MCP nao configurado (MAG_API_URL/MAG_INTERNAL_KEY/MAG_TENANT_SLUG ausentes).');
  }
}

function magHeaders() {
  return { 'x-internal-key': MAG_INTERNAL_KEY, 'content-type': 'application/json' };
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

function summarizeCharge(charge) {
  return [
    'Cobranca Pix criada/consultada com sucesso.',
    `TXID: ${charge.txid}`,
    `Status: ${charge.status}`,
    `Valor: ${charge.amount ?? 'n/d'}`,
    `Link de pagamento: ${charge.location ?? 'n/d'}`,
    `Pix copia e cola: ${charge.pixCopiaECola ?? 'n/d'}`,
    `Pago em: ${charge.paidAt ?? 'ainda nao pago'}`,
    `EndToEndId: ${charge.endToEndId ?? 'n/d'}`,
  ].join('\n');
}

function summarizeReceipts(receipts) {
  if (!Array.isArray(receipts) || receipts.length === 0) {
    return 'Nenhum recebimento Pix encontrado para o periodo informado.';
  }

  const lines = receipts.map((receipt) =>
    [
      `- TXID: ${receipt.txid ?? 'n/d'}`,
      `  EndToEndId: ${receipt.endToEndId}`,
      `  Valor: ${receipt.amount ?? 'n/d'}`,
      `  Pago em: ${receipt.paidAt ?? 'n/d'}`,
      `  Origem: ${receipt.source}`,
      `  Info pagador: ${receipt.infoPagador ?? 'n/d'}`,
    ].join('\n'),
  );

  return `Recebimentos Pix encontrados: ${receipts.length}\n${lines.join('\n')}`;
}

const tools = {
  'c6_bank.create_pix_charge': {
    description: 'Cria uma cobranca Pix imediata no C6 Bank e devolve TXID, status, link de pagamento e Pix copia e cola.',
    inputSchema: {
      type: 'object',
      properties: {
        amount: { type: 'number', description: 'Valor da cobranca em reais.' },
        description: { type: 'string', description: 'Descricao exibida ao pagador.' },
        payerName: { type: 'string', description: 'Nome do pagador, se houver.' },
        payerTaxId: { type: 'string', description: 'CPF/CNPJ do pagador, se houver.' },
        expirationSeconds: { type: 'number', description: 'Expiracao da cobranca em segundos.' },
        externalReferenceId: { type: 'string', description: 'ID de referencia interno opcional.' },
        txid: { type: 'string', description: 'TXID opcional, caso o fluxo queira definir manualmente.' },
      },
      required: ['amount'],
    },
    async run(args) {
      const payload = {
        amount: Number(args.amount),
        expirationSeconds:
          typeof args.expirationSeconds === 'number' ? args.expirationSeconds : 3600,
        ...(args.description ? { description: String(args.description) } : {}),
        ...(args.payerName ? { payerName: String(args.payerName) } : {}),
        ...(args.payerTaxId ? { payerTaxId: String(args.payerTaxId) } : {}),
        ...(args.externalReferenceId ? { externalReferenceId: String(args.externalReferenceId) } : {}),
        ...(args.txid ? { txid: String(args.txid) } : {}),
      };
      const charge = await magFetch(`/internal/c6-bank/${encodeURIComponent(MAG_TENANT_SLUG)}/pix/charges`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      return summarizeCharge(charge);
    },
  },

  'c6_bank.get_charge_status': {
    description: 'Consulta e sincroniza o status de uma cobranca Pix pelo TXID no C6 Bank.',
    inputSchema: {
      type: 'object',
      properties: {
        txid: { type: 'string', description: 'TXID da cobranca Pix.' },
      },
      required: ['txid'],
    },
    async run(args) {
      const charge = await magFetch(
        `/internal/c6-bank/${encodeURIComponent(MAG_TENANT_SLUG)}/pix/charges/${encodeURIComponent(String(args.txid))}`,
      );
      return summarizeCharge(charge);
    },
  },

  'c6_bank.list_received_pix': {
    description: 'Lista os Pix recebidos em um intervalo e pode filtrar por TXID.',
    inputSchema: {
      type: 'object',
      properties: {
        start: { type: 'string', description: 'Data/hora inicial ISO-8601.' },
        end: { type: 'string', description: 'Data/hora final ISO-8601.' },
        txid: { type: 'string', description: 'Filtra por TXID especifico.' },
        limit: { type: 'number', description: 'Quantidade maxima de itens retornados.' },
      },
      required: ['start', 'end'],
    },
    async run(args) {
      const query = new URLSearchParams({
        start: String(args.start),
        end: String(args.end),
      });
      if (args.txid) query.set('txid', String(args.txid));
      if (typeof args.limit === 'number') query.set('limit', String(args.limit));
      const result = await magFetch(
        `/internal/c6-bank/${encodeURIComponent(MAG_TENANT_SLUG)}/pix/received?${query.toString()}`,
      );
      return summarizeReceipts(result.receipts);
    },
  },
};

function toolList() {
  return Object.entries(tools).map(([name, tool]) => ({
    name,
    description: tool.description,
    inputSchema: tool.inputSchema,
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
      const tool = tools[name];
      if (!tool) {
        return reply(id, {
          content: [{ type: 'text', text: `Ferramenta desconhecida: ${name}` }],
          isError: true,
        });
      }
      try {
        const result = await tool.run(params.arguments || {});
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

log(`started (api=${MAG_API_URL || 'unset'} tenant=${MAG_TENANT_SLUG || 'unset'})`);
