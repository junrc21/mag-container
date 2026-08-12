#!/usr/bin/env node
import { createInterface } from 'node:readline';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';
const TIMEOUT_MS = 15000;

const tools = {
  resolve_issuer: {
    description: 'Localiza uma empresa na base oficial da CVM por nome, razão social, CNPJ, código CVM ou ticker comum, como PETR4 e VALE3. Ticker é somente um alias para fundamentos e nunca significa cotação. Use antes de get_regulatory_report quando o UUID for desconhecido.',
    inputSchema: {
      type: 'object', required: ['query'], additionalProperties: false,
      properties: { query: { type: 'string', minLength: 2 }, limit: { type: 'integer', minimum: 1, maximum: 20, default: 5 } },
    },
    path: '/internal/investing/resolve-issuer',
  },
  get_regulatory_report: {
    description: 'Obtém relatório regulatório oficial compacto da CVM. Preserve warnings, freshness, coverage e datas; cite sourceUrl em toda afirmação material. Não fornece cotação, variação, volume nem market data.',
    inputSchema: {
      type: 'object', required: ['issuerId'], additionalProperties: false,
      properties: { issuerId: { type: 'string', format: 'uuid' } },
    },
    path: '/internal/investing/regulatory-report',
  },
  get_notifications: {
    description: 'Busca novas divulgações e revisões oficiais da CVM ainda não apresentadas por esta MAG. Preserve warnings e cite sourceUrl. Não trate divulgações oficiais como notícias editoriais e não use esta ferramenta para cotação.',
    inputSchema: {
      type: 'object', additionalProperties: false,
      properties: { limit: { type: 'integer', minimum: 1, maximum: 50, default: 20 } },
    },
    path: '/internal/investing/notifications',
    bodyDefaults: { consumerKey: 'runtime-default' },
  },
};

function send(message) { process.stdout.write(JSON.stringify(message) + '\n'); }
function result(id, value) { send({ jsonrpc: '2.0', id, result: value }); }
function error(id, code, message) { send({ jsonrpc: '2.0', id, error: { code, message } }); }
function textResult(value, isError = false) {
  return { content: [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value, null, 2) }], isError };
}
function assertConfigured() {
  if (!MAG_API_URL || !MAG_INTERNAL_KEY || !MAG_TENANT_ID) throw new Error('Integração de dados oficiais indisponível neste momento.');
}
async function callInternal(tool, args) {
  assertConfigured();
  let response;
  try {
    response = await fetch(`${MAG_API_URL}${tool.path}`, {
      method: 'POST',
      headers: { 'x-internal-key': MAG_INTERNAL_KEY, 'content-type': 'application/json' },
      body: JSON.stringify({ tenantId: MAG_TENANT_ID, ...args, ...(tool.bodyDefaults || {}) }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    throw new Error('Não consegui consultar os dados oficiais da CVM agora. Tente novamente mais tarde.');
  }
  let body;
  try { body = await response.json(); } catch { throw new Error('A fonte oficial respondeu de forma inválida. Não vou estimar informações ausentes.'); }
  if (!response.ok) throw new Error(body?.error || 'Não consegui consultar os dados oficiais da CVM agora. Tente novamente mais tarde.');
  return body.data;
}

async function handle(message) {
  const { id, method, params } = message;
  if (method === 'initialize') {
    return result(id, { protocolVersion: '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: 'mag-investing', version: '1.1.0' } });
  }
  if (method === 'notifications/initialized') return;
  if (method === 'ping') return result(id, {});
  if (method === 'tools/list') {
    return result(id, { tools: Object.entries(tools).map(([name, tool]) => ({ name, description: tool.description, inputSchema: tool.inputSchema })) });
  }
  if (method === 'tools/call') {
    const tool = tools[params?.name];
    if (!tool) return error(id, -32602, 'Ferramenta desconhecida.');
    try { return result(id, textResult(await callInternal(tool, params?.arguments || {}))); }
    catch (cause) { return result(id, textResult(cause instanceof Error ? cause.message : 'Consulta indisponível.', true)); }
  }
  if (id !== undefined) error(id, -32601, 'Método não suportado.');
}

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on('line', async (line) => {
  try { await handle(JSON.parse(line)); }
  catch { error(null, -32700, 'Mensagem MCP inválida.'); }
});