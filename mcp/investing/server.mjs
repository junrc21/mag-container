#!/usr/bin/env node
import { createInterface } from 'node:readline';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';
const TIMEOUT_MS = 15000;

const tools = {
  resolve_issuer: {
    description: 'Localiza uma empresa na base oficial da CVM por nome, razão social, CNPJ ou código CVM. Use esta ferramenta antes de get_regulatory_report sempre que o UUID do emissor ainda não estiver disponível. Se houver mais de um candidato plausível, peça ao usuário para escolher.',
    inputSchema: {
      type: 'object', required: ['query'], additionalProperties: false,
      properties: { query: { type: 'string', minLength: 2 }, limit: { type: 'integer', minimum: 1, maximum: 20, default: 5 } },
    },
    path: '/internal/investing/resolve-issuer',
  },
  get_regulatory_report: {
    description: 'Obtém relatório regulatório oficial da CVM para um issuerId já resolvido. Ao responder, preserve warnings, freshness, coverage e datas; cite sourceUrl em toda afirmação material. Esta ferramenta não fornece cotação, PETR3/PETR4, variação diária nem market data: nesses casos explique a limitação e nunca estime preço.',
    inputSchema: {
      type: 'object', required: ['issuerId'], additionalProperties: false,
      properties: { issuerId: { type: 'string', format: 'uuid' } },
    },
    path: '/internal/investing/regulatory-report',
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
async function callInternal(path, args) {
  assertConfigured();
  let response;
  try {
    response = await fetch(`${MAG_API_URL}${path}`, {
      method: 'POST',
      headers: { 'x-internal-key': MAG_INTERNAL_KEY, 'content-type': 'application/json' },
      body: JSON.stringify({ tenantId: MAG_TENANT_ID, ...args }),
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
    return result(id, { protocolVersion: '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: 'mag-investing', version: '1.0.0' } });
  }
  if (method === 'notifications/initialized') return;
  if (method === 'ping') return result(id, {});
  if (method === 'tools/list') {
    return result(id, { tools: Object.entries(tools).map(([name, tool]) => ({ name, description: tool.description, inputSchema: tool.inputSchema })) });
  }
  if (method === 'tools/call') {
    const tool = tools[params?.name];
    if (!tool) return error(id, -32602, 'Ferramenta desconhecida.');
    try { return result(id, textResult(await callInternal(tool.path, params?.arguments || {}))); }
    catch (cause) { return result(id, textResult(cause instanceof Error ? cause.message : 'Consulta indisponível.', true)); }
  }
  if (id !== undefined) error(id, -32601, 'Método não suportado.');
}

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on('line', async (line) => {
  try { await handle(JSON.parse(line)); }
  catch { error(null, -32700, 'Mensagem MCP inválida.'); }
});