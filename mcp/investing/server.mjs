#!/usr/bin/env node
import { createInterface } from 'node:readline';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';
const RUNTIME_TOKEN = process.env.MAG_INVESTING_RUNTIME_TOKEN || '';
const CVM_ENABLED = process.env.INVESTING_CVM_ENABLED !== 'false';
const B3_ENABLED = process.env.INVESTING_B3_ENABLED === 'true';
const TIMEOUT_MS = 15000;

const tools = {
  resolve_issuer: {
    description: 'Resolve a empresa para perguntas financeiras em linguagem natural: como foi a Ambev, Petrobras deu lucro, balanço da ABEV3, receita da Vale, últimos resultados. Aceita nome, razão social, CNPJ, código CVM ou ticker comum. Ticker é somente um alias para fundamentos e nunca significa cotação. Use antes de get_regulatory_report quando o UUID for desconhecido.',
    inputSchema: {
      type: 'object', required: ['query'], additionalProperties: false,
      properties: { query: { type: 'string', minLength: 2 }, limit: { type: 'integer', minimum: 1, maximum: 20, default: 5 } },
    },
    path: '/internal/investing/resolve-issuer',
  },
  get_regulatory_report: {
    description: 'Consulta resultados financeiros, lucro, receita, balanço e divulgações oficiais da empresa resolvida. Use para pedidos informais ou perguntas de continuidade sobre desempenho empresarial, sem exigir a palavra resultados. Retorna o relatório regulatório compacto da CVM. Preserve warnings, freshness, coverage e datas; cite sourceUrl em toda afirmação material. Não fornece cotação, variação, volume nem market data.',
    inputSchema: {
      type: 'object', required: ['issuerId'], additionalProperties: false,
      properties: { issuerId: { type: 'string', format: 'uuid' } },
    },
    path: '/internal/investing/regulatory-report',
  },
  get_notifications: {
    description: 'Busca divulgações e revisões oficiais da CVM ainda não retornadas para esta MAG. Preserve a fonte e os avisos; entrega não significa confirmação de leitura pelo usuário. Preserve warnings e cite sourceUrl. Não trate divulgações oficiais como notícias editoriais e não use esta ferramenta para cotação.',
    inputSchema: {
      type: 'object', additionalProperties: false,
      properties: { limit: { type: 'integer', minimum: 1, maximum: 50, default: 20 } },
    },
    path: '/internal/investing/notifications',
    bodyDefaults: { consumerKey: 'runtime-default' },
  },
};


if (!CVM_ENABLED) for (const name of ['resolve_issuer', 'get_regulatory_report', 'get_notifications']) delete tools[name];

const assetSchema = { type: 'string', minLength: 1, maxLength: 100 };
const periodSchema = { type: 'string', enum: ['30d', '90d', '1y', 'ytd'] };
const schema = (properties, required) => ({ type: 'object', properties, required, additionalProperties: false });
if (B3_ENABLED) Object.assign(tools, {
  resolve_financial_entity: {
    description: 'Identifica o papel B3 por nome, ticker ou ISIN antes de consultar preços. Use para quanto está PETR4, como a ação da Ambev foi, Vale subiu e comparações de ações. Prefira ticker exato aos aliases parciais; preserve instrumentId para distinguir mercados.',
    inputSchema: schema({ query: { type: 'string', minLength: 2, maxLength: 200 }, limit: { type: 'integer', minimum: 1, maximum: 20 } }, ['query']),
    path: '/internal/investing/resolve-financial-entity',
  },
  get_asset: {
    description: 'Retorna identidade e datas de cobertura de um papel B3. Para quanto está agora, obtenha a última data disponível e consulte o fechamento dessa data; não é tempo real.',
    inputSchema: schema({ asset: assetSchema }, ['asset']), path: '/internal/investing/asset',
  },
  get_asset_history: {
    description: 'Consulta preços diários, fechamento e volume reais persistidos da B3. Use para preço de PETR4, histórico da Vale, fechamento de ontem e gráfico por período. Informe cobertura efetiva, fonte e que o preço é histórico não ajustado. Para preço atual, consulte a última data disponível.',
    inputSchema: schema({ asset: assetSchema, from: { type: 'string', pattern: '^\\d{4}-\\d{2}-\\d{2}$' }, to: { type: 'string', pattern: '^\\d{4}-\\d{2}-\\d{2}$' }, limit: { type: 'integer', minimum: 1, maximum: 1000 } }, ['asset', 'from', 'to']), path: '/internal/investing/asset-history',
  },
  get_asset_performance: {
    description: 'Calcula a variação do preço de fechamento da ação no período. Use para PETR4 subiu, quanto a Vale variou ou desempenho da ação. Não confunda com lucro da empresa, dividendos ou retorno total; informe datas efetivas e avisos.',
    inputSchema: schema({ asset: assetSchema, period: periodSchema }, ['asset', 'period']), path: '/internal/investing/asset-performance',
  },
  compare_assets: {
    description: 'Compara variação de preço histórica de duas a dez ações/instrumentos. Use para compare PETR4 com VALE3 e perguntas de continuidade como e a Ambev quando a conversa for sobre preços. Não compara balanços empresariais; preserve diferenças de cobertura.',
    inputSchema: schema({ assets: { type: 'array', items: assetSchema, minItems: 2, maxItems: 10 }, period: periodSchema }, ['assets', 'period']), path: '/internal/investing/compare-assets',
  },
});

function validate(value, definition) {
  if (definition.type === 'object') {
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Parâmetros inválidos.');
    if (Object.keys(value).some(key => !(key in definition.properties))) throw new Error('Parâmetro não permitido.');
    for (const key of definition.required || []) if (!(key in value)) throw new Error('Parâmetro obrigatório ausente.');
    for (const [key, entry] of Object.entries(value)) validate(entry, definition.properties[key]);
  } else if (definition.type === 'string') {
    if (typeof value !== 'string' || !value.trim() || value.length < (definition.minLength || 0) || value.length > (definition.maxLength || 200) || (definition.pattern && !new RegExp(definition.pattern).test(value)) || (definition.enum && !definition.enum.includes(value))) throw new Error('Texto ou período inválido.');
  } else if (definition.type === 'integer') {
    if (!Number.isInteger(value) || value < definition.minimum || value > definition.maximum) throw new Error('Limite inválido.');
  } else if (definition.type === 'array') {
    if (!Array.isArray(value) || value.length < definition.minItems || value.length > definition.maxItems) throw new Error('Quantidade de ativos inválida.');
    for (const entry of value) validate(entry, definition.items);
  }
}

function send(message) { process.stdout.write(JSON.stringify(message) + '\n'); }
function result(id, value) { send({ jsonrpc: '2.0', id, result: value }); }
function error(id, code, message) { send({ jsonrpc: '2.0', id, error: { code, message } }); }
function textResult(value, isError = false) {
  return { content: [{ type: 'text', text: typeof value === 'string' ? value : JSON.stringify(value) }], ...(isError ? {} : { structuredContent: { result: value } }), isError };
}
function assertConfigured() {
  if (!MAG_API_URL || !(RUNTIME_TOKEN || MAG_INTERNAL_KEY) || !MAG_TENANT_ID) throw new Error('Integração de dados oficiais indisponível neste momento.');
}
async function callInternal(tool, args) {
  assertConfigured();
  let response;
  try {
    response = await fetch(`${MAG_API_URL}${tool.path}`, {
      method: 'POST',
      headers: { ...(RUNTIME_TOKEN ? { 'x-investing-runtime-token': RUNTIME_TOKEN } : { 'x-internal-key': MAG_INTERNAL_KEY }), 'content-type': 'application/json' },
      body: JSON.stringify({ ...args, ...(tool.bodyDefaults || {}), tenantId: MAG_TENANT_ID }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch {
    throw new Error('Não consegui consultar os dados oficiais agora. Tente novamente mais tarde.');
  }
  let body;
  try { body = await response.json(); } catch { throw new Error('A fonte oficial respondeu de forma inválida. Não vou estimar informações ausentes.'); }
  if (!response.ok) throw new Error(response.status === 403 ? 'Este recurso não está habilitado para sua MAG. Fale com o suporte.' : 'Não consegui consultar os dados oficiais agora. Tente novamente mais tarde.');
  if (body.isError) throw new Error('Não encontrei os dados solicitados para esse ativo ou período.');
  if (!Object.hasOwn(body, 'data')) throw new Error('A fonte oficial respondeu de forma inválida.');
  return body.data;
}

async function handle(message) {
  const { id, method, params } = message;
  if (method === 'initialize') {
    return result(id, { protocolVersion: '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: 'mag-investing', version: '1.2.0' } });
  }
  if (method === 'notifications/initialized') return;
  if (method === 'ping') return result(id, {});
  if (method === 'tools/list') {
    return result(id, { tools: Object.entries(tools).map(([name, tool]) => ({ name, description: tool.description, inputSchema: tool.inputSchema })) });
  }
  if (method === 'tools/call') {
    const tool = tools[params?.name];
    if (!tool) return error(id, -32602, 'Ferramenta desconhecida.');
    try { validate(params?.arguments || {}, tool.inputSchema); return result(id, textResult(await callInternal(tool, params?.arguments || {}))); }
    catch (cause) { return result(id, textResult(cause instanceof Error ? cause.message : 'Consulta indisponível.', true)); }
  }
  if (id !== undefined) error(id, -32601, 'Método não suportado.');
}

const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
input.on('line', async (line) => {
  try { await handle(JSON.parse(line)); }
  catch { error(null, -32700, 'Mensagem MCP inválida.'); }
});