#!/usr/bin/env node
// Read-only iFood MCP. Tokens and merchant bindings never leave the MAG API.
import { createInterface } from 'node:readline';

const API = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const KEY = process.env.MAG_INTERNAL_KEY || '';
const TENANT = process.env.MAG_TENANT_ID || '';
const VERSION = '2025-06-18';
function send(value) { process.stdout.write(`${JSON.stringify(value)}\n`); }
function result(id, value) { send({ jsonrpc: '2.0', id, result: value }); }
function error(id, code, message) { send({ jsonrpc: '2.0', id, error: { code, message } }); }

function defaultRange() {
  const end = new Date(Date.now() - 86400000);
  const start = new Date(end.getTime() - 6 * 86400000);
  return { dateFrom: start.toISOString().slice(0, 10), dateTo: end.toISOString().slice(0, 10) };
}

async function query(path, args) {
  if (!API || !KEY || !TENANT) throw new Error('Conexão iFood indisponível. Fale com o suporte.');
  const url = new URL(`${API}${path}`);
  const range = defaultRange();
  url.searchParams.set('tenantId', TENANT);
  url.searchParams.set('dateFrom', args.dateFrom || range.dateFrom);
  url.searchParams.set('dateTo', args.dateTo || range.dateTo);
  if (args.page !== undefined) url.searchParams.set('page', String(args.page));
  const response = await fetch(url, { headers: { 'x-internal-key': KEY }, signal: AbortSignal.timeout(25000) });
  if (!response.ok) throw new Error(`Não foi possível consultar o iFood (${response.status}). Verifique a conexão em Fontes.`);
  return response.json();
}

const dates = {
  dateFrom: { type: 'string', description: 'Data inicial YYYY-MM-DD; padrão: sete dias até ontem.' },
  dateTo: { type: 'string', description: 'Data final YYYY-MM-DD; padrão: ontem.' },
};
const tools = {
  ifood_analytics_kpis: {
    description: 'Consulta indicadores históricos agregados da loja iFood: GMV, ticket médio e distribuição por status, canal, pagamento e entrega. Dados fechados até D-1; nunca em tempo real.',
    inputSchema: { type: 'object', properties: dates },
    run: (args) => query('/ifood/internal/analytics/kpis', args),
  },
  ifood_financial_sales: {
    description: 'Consulta vendas financeiras v3 da loja iFood em um período. Retorno vazio ou 204 significa que não houve vendas no período.',
    inputSchema: { type: 'object', properties: { ...dates, page: { type: 'integer', minimum: 1, maximum: 1000, description: 'Página; padrão 1.' } } },
    run: (args) => query('/ifood/internal/financial/sales', args),
  },
  ifood_financial_settlements: {
    description: 'Consulta repasses/conciliação v3 da loja iFood por data de cálculo.',
    inputSchema: { type: 'object', properties: dates },
    run: (args) => query('/ifood/internal/financial/settlements', args),
  },
  ifood_financial_events: {
    description: 'Consulta eventos financeiros v3 da loja iFood em um período, incluindo ajustes e movimentações para conciliação.',
    inputSchema: { type: 'object', properties: { ...dates, page: { type: 'integer', minimum: 1, maximum: 1000, description: 'Página; padrão 1.' } } },
    run: (args) => query('/ifood/internal/financial/events', args),
  },
  ifood_financial_anticipations: {
    description: 'Consulta antecipações financeiras v3 da loja iFood por data de cálculo.',
    inputSchema: { type: 'object', properties: dates },
    run: (args) => query('/ifood/internal/financial/anticipations', args),
  },
};

async function handle(msg) {
  const { id, method, params } = msg;
  if (id === undefined || id === null) return;
  if (method === 'initialize') return result(id, { protocolVersion: params?.protocolVersion || VERSION, capabilities: { tools: { listChanged: false } }, serverInfo: { name: 'mag-ifood', version: '0.1.0' } });
  if (method === 'ping') return result(id, {});
  if (method === 'tools/list') return result(id, { tools: Object.entries(tools).map(([name, tool]) => ({ name, description: tool.description, inputSchema: tool.inputSchema })) });
  if (method === 'tools/call') {
    const tool = tools[params?.name];
    if (!tool) return result(id, { content: [{ type: 'text', text: 'Ferramenta desconhecida.' }], isError: true });
    try {
      const value = await tool.run(params?.arguments || {});
      const content = JSON.stringify(value);
      return result(id, { content: [{ type: 'text', text: content.length > 20000 ? `${content.slice(0, 20000)}...[truncado]` : content }] });
    } catch (cause) {
      return result(id, { content: [{ type: 'text', text: cause instanceof Error ? cause.message : 'Falha na consulta ao iFood.' }], isError: true });
    }
  }
  error(id, -32601, `Method not found: ${method}`);
}

createInterface({ input: process.stdin }).on('line', (line) => {
  try { void handle(JSON.parse(line)).catch(() => undefined); } catch { /* invalid JSON */ }
});
