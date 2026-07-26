#!/usr/bin/env node
// MAG Mercado Livre MCP server (stdio, zero-dependency).
//
// Exposes curated Mercado Livre tools to Hermes. Credentials stay centralized in
// the MAG control plane: each call fetches the tenant OAuth token from MAG and
// then calls the official Mercado Livre API.
//
// Required env (via mcp_servers.mercado-livre.env): MAG_API_URL,
// MAG_INTERNAL_KEY, MAG_TENANT_ID.

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-mercado-livre';
const SERVER_VERSION = '0.1.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';
const MELI_API = 'https://api.mercadolibre.com';
const DEFAULT_PRODUCT_ID = 'PADS';
const DEFAULT_CAMPAIGN_METRICS = 'clicks,prints,cost,roas,total_amount';
const MAX_TEXT = 12000;

function log(...a) { process.stderr.write(`[mag-mercado-livre] ${a.join(' ')}\n`); }
function send(m) { process.stdout.write(JSON.stringify(m) + '\n'); }
function reply(id, result) { send({ jsonrpc: '2.0', id, result }); }
function replyError(id, code, message) { send({ jsonrpc: '2.0', id, error: { code, message } }); }
function truncate(s) {
  if (typeof s !== 'string') s = JSON.stringify(s, null, 2);
  return s.length > MAX_TEXT ? s.slice(0, MAX_TEXT) + '\n…[truncado]' : s;
}
function compact(value) {
  return Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined && v !== null && v !== ''));
}
function isoDate(value) {
  return value.toISOString().slice(0, 10);
}
function last30Days() {
  const end = new Date();
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 30);
  return { dateFrom: isoDate(start), dateTo: isoDate(end) };
}

async function getToken() {
  if (!MAG_API_URL || !MAG_INTERNAL_KEY || !MAG_TENANT_ID) {
    throw new Error('MCP não configurado (MAG_API_URL/MAG_INTERNAL_KEY/MAG_TENANT_ID ausentes).');
  }
  const res = await fetch(
    `${MAG_API_URL}/internal/connectors/by-provider/mercado_livre/token?tenantId=${encodeURIComponent(MAG_TENANT_ID)}`,
    { headers: { 'x-internal-key': MAG_INTERNAL_KEY } },
  );
  if (!res.ok) throw new Error(`MAG token ${res.status}: ${(await res.text()).slice(0, 160)}`);
  const body = await res.json();
  if (!body.accessToken) {
    throw new Error('Mercado Livre não está conectado. Conecte a conta em Fontes → Integrações → Mercado Livre.');
  }
  return body;
}

async function meli(path, { method = 'GET', query, body, headers } = {}) {
  const token = await getToken();
  const url = new URL(path.startsWith('http') ? path : `${MELI_API}${path}`);
  for (const [key, value] of Object.entries(query || {})) {
    if (value === undefined || value === null || value === '') continue;
    url.searchParams.set(key, String(value));
  }
  const authHeader = token.tokenAuthHeader === 'raw' ? token.accessToken : `Bearer ${token.accessToken}`;
  const res = await fetch(url, {
    method,
    headers: {
      Accept: 'application/json',
      Authorization: authHeader,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(headers || {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });

  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = text; }
  if (!res.ok) {
    const errorText = typeof data === 'string' ? data : JSON.stringify(data);
    throw new Error(`Mercado Livre API ${res.status}: ${errorText.slice(0, 240)}`);
  }
  return data;
}

async function me() {
  return meli('/users/me');
}

async function resolveCampaignContext(args = {}) {
  const account = await me();
  const advertiserSiteId = args.advertiserSiteId || account.site_id;
  if (!advertiserSiteId) throw new Error('Não foi possível inferir o site_id da conta conectada.');
  if (args.advertiserId) {
    return { advertiserId: String(args.advertiserId), advertiserSiteId };
  }
  const advertisers = await meli('/advertising/advertisers', {
    query: { product_id: args.productId || DEFAULT_PRODUCT_ID },
    headers: { 'api-version': '2' },
  });
  const first = Array.isArray(advertisers) ? advertisers[0] : null;
  const advertiserId = first?.advertiser_id || first?.id;
  if (!advertiserId) throw new Error('Nenhum advertiser de Product Ads disponível para a conta conectada.');
  return { advertiserId: String(advertiserId), advertiserSiteId };
}

async function resolveSellerId(sellerId) {
  if (sellerId) return String(sellerId);
  const account = await me();
  return String(account.id);
}

const tools = {
  mercado_livre_me: {
    description: 'Mostra os dados principais da conta Mercado Livre conectada.',
    inputSchema: { type: 'object', properties: {} },
    async run() {
      return me();
    },
  },

  mercado_livre_account_balance: {
    description: 'Traz uma visão de faturamento/saldo do período atual usando os relatórios de billing do Mercado Livre.',
    inputSchema: {
      type: 'object',
      properties: {
        group: { type: 'string', description: 'ML para Mercado Livre ou MP para Mercado Pago. Padrão: ML.' },
        documentType: { type: 'string', description: 'BILL ou CREDIT_NOTE. Padrão: BILL.' },
      },
    },
    async run(args) {
      const group = args.group || 'ML';
      const documentType = args.documentType || 'BILL';
      const periods = await meli('/billing/integration/monthly/periods', {
        query: { group, document_type: documentType },
      });
      const first = periods?.results?.[0] || periods?.results?.[0];
      if (!first?.period?.key && !first?.key) {
        return { periods, summary: null };
      }
      const periodKey = first.period?.key || first.key;
      const summary = await meli(`/billing/integration/periods/key/${encodeURIComponent(periodKey)}/summary/details`, {
        query: { group, document_type: documentType },
      });
      return { currentPeriodKey: periodKey, periods, summary };
    },
  },

  mercado_livre_list_billing_periods: {
    description: 'Lista períodos de faturamento do Mercado Livre/Mercado Pago.',
    inputSchema: {
      type: 'object',
      properties: {
        group: { type: 'string', description: 'ML ou MP. Padrão: ML.' },
        documentType: { type: 'string', description: 'BILL ou CREDIT_NOTE. Padrão: BILL.' },
      },
    },
    async run(args) {
      return meli('/billing/integration/monthly/periods', {
        query: { group: args.group || 'ML', document_type: args.documentType || 'BILL' },
      });
    },
  },

  mercado_livre_get_billing_summary: {
    description: 'Obtém o resumo de faturamento de um período específico.',
    inputSchema: {
      type: 'object',
      properties: {
        periodKey: { type: 'string', description: 'Chave do período, ex.: 2026-07-01.' },
        group: { type: 'string', description: 'ML ou MP. Padrão: ML.' },
        documentType: { type: 'string', description: 'BILL ou CREDIT_NOTE.' },
      },
      required: ['periodKey'],
    },
    async run(args) {
      return meli(`/billing/integration/periods/key/${encodeURIComponent(args.periodKey)}/summary/details`, {
        query: compact({ group: args.group || 'ML', document_type: args.documentType }),
      });
    },
  },

  mercado_livre_get_billing_details: {
    description: 'Obtém o detalhe de faturamento de um período específico.',
    inputSchema: {
      type: 'object',
      properties: {
        periodKey: { type: 'string', description: 'Chave do período, ex.: 2026-07-01.' },
        group: { type: 'string', description: 'ML ou MP. Padrão: ML.' },
        documentType: { type: 'string', description: 'BILL ou CREDIT_NOTE.' },
        limit: { type: 'number', description: 'Padrão: 20.' },
        offset: { type: 'number', description: 'Padrão: 0.' },
        detailType: { type: 'string' },
        orderIds: { type: 'string', description: 'IDs separados por vírgula.' },
        itemIds: { type: 'string', description: 'IDs separados por vírgula.' },
      },
      required: ['periodKey'],
    },
    async run(args) {
      return meli(`/billing/integration/periods/key/${encodeURIComponent(args.periodKey)}/group/${encodeURIComponent(args.group || 'ML')}/details`, {
        query: compact({
          document_type: args.documentType,
          limit: args.limit || 20,
          offset: args.offset || 0,
          detail_type: args.detailType,
          order_ids: args.orderIds,
          item_ids: args.itemIds,
        }),
      });
    },
  },

  mercado_livre_list_items: {
    description: 'Lista anúncios do vendedor conectado.',
    inputSchema: {
      type: 'object',
      properties: {
        sellerId: { type: 'string', description: 'Opcional. Se omitido, usa a conta conectada.' },
        status: { type: 'string' },
        query: { type: 'string', description: 'Busca por texto.' },
        includeDetails: { type: 'boolean', description: 'Padrão: true.' },
        limit: { type: 'number', description: 'Padrão: 20.' },
        offset: { type: 'number', description: 'Padrão: 0.' },
      },
    },
    async run(args) {
      const sellerId = await resolveSellerId(args.sellerId);
      const search = await meli(`/users/${sellerId}/items/search`, {
        query: compact({
          status: args.status,
          q: args.query,
          limit: args.limit || 20,
          offset: args.offset || 0,
        }),
      });
      const itemIds = Array.isArray(search?.results) ? search.results : [];
      const includeDetails = args.includeDetails !== false;
      const details = includeDetails && itemIds.length > 0
        ? await meli('/items', { query: { ids: itemIds.join(',') } })
        : [];
      return { sellerId, paging: search?.paging || null, itemIds, details };
    },
  },

  mercado_livre_get_item: {
    description: 'Obtém um anúncio pelo item_id.',
    inputSchema: {
      type: 'object',
      properties: { itemId: { type: 'string' } },
      required: ['itemId'],
    },
    async run(args) {
      return meli(`/items/${encodeURIComponent(args.itemId)}`);
    },
  },

  mercado_livre_create_item: {
    description: 'Cria um anúncio no Mercado Livre. Ação de escrita.',
    inputSchema: {
      type: 'object',
      properties: {
        body: { type: 'object', description: 'Payload completo do POST /items.' },
      },
      required: ['body'],
    },
    async run(args) {
      return meli('/items', { method: 'POST', body: args.body || {} });
    },
  },

  mercado_livre_update_item: {
    description: 'Atualiza um anúncio existente. Ação de escrita sem delete.',
    inputSchema: {
      type: 'object',
      properties: {
        itemId: { type: 'string' },
        body: { type: 'object', description: 'Payload do PUT /items/{itemId}.' },
      },
      required: ['itemId', 'body'],
    },
    async run(args) {
      if (args.body?.status && !['active', 'paused'].includes(args.body.status)) {
        throw new Error('Por segurança, esta integração só permite status active ou paused na atualização do item.');
      }
      return meli(`/items/${encodeURIComponent(args.itemId)}`, { method: 'PUT', body: args.body || {} });
    },
  },

  mercado_livre_list_orders: {
    description: 'Lista pedidos do vendedor conectado.',
    inputSchema: {
      type: 'object',
      properties: {
        sellerId: { type: 'string', description: 'Opcional. Se omitido, usa a conta conectada.' },
        orderStatus: { type: 'string' },
        sort: { type: 'string' },
        limit: { type: 'number', description: 'Padrão: 20.' },
        offset: { type: 'number', description: 'Padrão: 0.' },
      },
    },
    async run(args) {
      const sellerId = await resolveSellerId(args.sellerId);
      return meli('/orders/search', {
        query: compact({
          seller: sellerId,
          'order.status': args.orderStatus,
          sort: args.sort,
          limit: args.limit || 20,
          offset: args.offset || 0,
        }),
      });
    },
  },

  mercado_livre_get_order: {
    description: 'Obtém um pedido pelo order_id.',
    inputSchema: {
      type: 'object',
      properties: { orderId: { type: 'string' } },
      required: ['orderId'],
    },
    async run(args) {
      return meli(`/orders/${encodeURIComponent(args.orderId)}`);
    },
  },

  mercado_livre_list_questions: {
    description: 'Lista perguntas recebidas pelo vendedor ou por item.',
    inputSchema: {
      type: 'object',
      properties: {
        sellerId: { type: 'string', description: 'Opcional. Se omitido, usa a conta conectada.' },
        itemId: { type: 'string' },
        status: { type: 'string' },
        limit: { type: 'number', description: 'Padrão: 20.' },
        offset: { type: 'number', description: 'Padrão: 0.' },
      },
    },
    async run(args) {
      const sellerId = args.itemId ? undefined : await resolveSellerId(args.sellerId);
      return meli('/questions/search', {
        query: compact({
          item_id: args.itemId,
          seller_id: sellerId,
          status: args.status,
          limit: args.limit || 20,
          offset: args.offset || 0,
          api_version: 4,
        }),
      });
    },
  },

  mercado_livre_get_question: {
    description: 'Obtém os detalhes de uma pergunta.',
    inputSchema: {
      type: 'object',
      properties: { questionId: { type: 'string' } },
      required: ['questionId'],
    },
    async run(args) {
      return meli(`/questions/${encodeURIComponent(args.questionId)}`, { query: { api_version: 4 } });
    },
  },

  mercado_livre_answer_question: {
    description: 'Responde uma pergunta em um anúncio. Ação de escrita.',
    inputSchema: {
      type: 'object',
      properties: {
        questionId: { type: 'string' },
        text: { type: 'string' },
      },
      required: ['questionId', 'text'],
    },
    async run(args) {
      return meli('/answers', {
        method: 'POST',
        body: { question_id: Number(args.questionId), text: args.text },
      });
    },
  },

  mercado_livre_list_campaigns: {
    description: 'Lista campanhas e métricas de Product Ads da conta conectada (somente leitura).',
    inputSchema: {
      type: 'object',
      properties: {
        advertiserId: { type: 'string' },
        advertiserSiteId: { type: 'string' },
        productId: { type: 'string', description: 'Padrão: PADS.' },
        dateFrom: { type: 'string', description: 'Formato YYYY-MM-DD.' },
        dateTo: { type: 'string', description: 'Formato YYYY-MM-DD.' },
        metrics: { type: 'string', description: `Padrão: ${DEFAULT_CAMPAIGN_METRICS}` },
        limit: { type: 'number', description: 'Padrão: 20.' },
        offset: { type: 'number', description: 'Padrão: 0.' },
      },
    },
    async run(args) {
      const ctx = await resolveCampaignContext(args || {});
      const range = args.dateFrom && args.dateTo ? { dateFrom: args.dateFrom, dateTo: args.dateTo } : last30Days();
      return meli(`/advertising/${ctx.advertiserSiteId}/advertisers/${ctx.advertiserId}/product_ads/campaigns/search`, {
        query: {
          date_from: range.dateFrom,
          date_to: range.dateTo,
          metrics: args.metrics || DEFAULT_CAMPAIGN_METRICS,
          limit: args.limit || 20,
          offset: args.offset || 0,
        },
        headers: { 'api-version': '2' },
      });
    },
  },

  mercado_livre_create_seller_campaign: {
    description: 'Cria uma campanha promocional do vendedor no Mercado Livre. Acao de escrita.',
    inputSchema: {
      type: 'object',
      properties: {
        promotionType: { type: 'string', description: 'Padrao: SELLER_CAMPAIGN.' },
        name: { type: 'string' },
        subType: { type: 'string', description: 'Padrao: FLEXIBLE_PERCENTAGE.' },
        startDate: { type: 'string', description: 'Formato ISO 8601.' },
        finishDate: { type: 'string', description: 'Formato ISO 8601.' },
        body: { type: 'object', description: 'Campos adicionais opcionais enviados junto ao payload.' },
      },
      required: ['name', 'startDate', 'finishDate'],
    },
    async run(args) {
      return meli('/seller-promotions/promotions', {
        method: 'POST',
        query: { app_version: 'v2' },
        body: {
          ...(args.body || {}),
          promotion_type: args.promotionType || 'SELLER_CAMPAIGN',
          name: args.name,
          sub_type: args.subType || 'FLEXIBLE_PERCENTAGE',
          start_date: args.startDate,
          finish_date: args.finishDate,
        },
      });
    },
  },

  mercado_livre_list_item_promotions: {
    description: 'Lista promoções associadas a um item.',
    inputSchema: {
      type: 'object',
      properties: { itemId: { type: 'string' } },
      required: ['itemId'],
    },
    async run(args) {
      return meli(`/seller-promotions/items/${encodeURIComponent(args.itemId)}`, {
        query: { app_version: 'v2' },
      });
    },
  },

  mercado_livre_list_promotion_items: {
    description: 'Lista os itens participantes de uma promoção.',
    inputSchema: {
      type: 'object',
      properties: {
        promotionId: { type: 'string' },
        promotionType: { type: 'string' },
        status: { type: 'string' },
        statusItem: { type: 'string' },
        itemId: { type: 'string' },
      },
      required: ['promotionId', 'promotionType'],
    },
    async run(args) {
      return meli(`/seller-promotions/promotions/${encodeURIComponent(args.promotionId)}/items`, {
        query: compact({
          promotion_type: args.promotionType,
          status: args.status,
          status_item: args.statusItem,
          item_id: args.itemId,
          app_version: 'v2',
        }),
      });
    },
  },

  mercado_livre_get_promotion_offer: {
    description: 'Obtém os detalhes de uma oferta/promotional offer pelo offer_id.',
    inputSchema: {
      type: 'object',
      properties: { offerId: { type: 'string' } },
      required: ['offerId'],
    },
    async run(args) {
      return meli(`/seller-promotions/offers/${encodeURIComponent(args.offerId)}`, {
        query: { app_version: 'v2' },
      });
    },
  },

  mercado_livre_create_promotion_offer: {
    description: 'Inclui um item em uma promoção. Ação de escrita.',
    inputSchema: {
      type: 'object',
      properties: {
        itemId: { type: 'string' },
        body: { type: 'object', description: 'Payload do POST /seller-promotions/items/{itemId}?app_version=v2.' },
      },
      required: ['itemId', 'body'],
    },
    async run(args) {
      return meli(`/seller-promotions/items/${encodeURIComponent(args.itemId)}`, {
        method: 'POST',
        query: { app_version: 'v2' },
        body: args.body || {},
      });
    },
  },

  mercado_livre_update_promotion_offer: {
    description: 'Atualiza uma oferta/promotional offer de um item. Ação de escrita.',
    inputSchema: {
      type: 'object',
      properties: {
        itemId: { type: 'string' },
        body: { type: 'object', description: 'Payload do PUT /seller-promotions/items/{itemId}?app_version=v2.' },
      },
      required: ['itemId', 'body'],
    },
    async run(args) {
      return meli(`/seller-promotions/items/${encodeURIComponent(args.itemId)}`, {
        method: 'PUT',
        query: { app_version: 'v2' },
        body: args.body || {},
      });
    },
  },

  mercado_livre_get_shipment: {
    description: 'Obtém os detalhes de um envio.',
    inputSchema: {
      type: 'object',
      properties: { shipmentId: { type: 'string' } },
      required: ['shipmentId'],
    },
    async run(args) {
      return meli(`/shipments/${encodeURIComponent(args.shipmentId)}`, {
        headers: { 'x-format-new': 'true' },
      });
    },
  },

  mercado_livre_get_shipment_items: {
    description: 'Lista os itens associados a um envio.',
    inputSchema: {
      type: 'object',
      properties: { shipmentId: { type: 'string' } },
      required: ['shipmentId'],
    },
    async run(args) {
      return meli(`/shipments/${encodeURIComponent(args.shipmentId)}/items`, {
        headers: { 'x-format-new': 'true' },
      });
    },
  },

  mercado_livre_get_shipment_costs: {
    description: 'Obtém os custos de um envio.',
    inputSchema: {
      type: 'object',
      properties: { shipmentId: { type: 'string' } },
      required: ['shipmentId'],
    },
    async run(args) {
      return meli(`/shipments/${encodeURIComponent(args.shipmentId)}/costs`, {
        headers: { 'x-format-new': 'true' },
      });
    },
  },

  mercado_livre_get_shipment_payments: {
    description: 'Obtém os pagamentos associados a um envio.',
    inputSchema: {
      type: 'object',
      properties: { shipmentId: { type: 'string' } },
      required: ['shipmentId'],
    },
    async run(args) {
      return meli(`/shipments/${encodeURIComponent(args.shipmentId)}/payments`, {
        headers: { 'x-format-new': 'true' },
      });
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

log(`started (api=${MAG_API_URL || 'unset'} tenant=${MAG_TENANT_ID || 'unset'})`);


