#!/usr/bin/env node
// MAG Teammates MCP server (stdio, zero-dependency).
//
// Gives the Hermes agent tools to identify and message OTHER employees of the
// same tenant who also use MAG (Companion, Telegram or WhatsApp) — the roster
// lives in the MAG control plane (Equipe → Colegas), not here. Two tools:
// list_teammates (read-only, so the model can judge a name match itself — no
// deterministic fuzzy-matching on purpose) and message_teammate (delivers a
// real message, costs the recipient 1 credit action same as any other turn).
//
// Different problem than the pre-existing `send_message` tool: that one
// resolves EXTERNAL contacts who already wrote to the bot (channel_directory),
// this one resolves INTERNAL teammates from the tenant's own roster.
//
// Required env (injected via mcp_servers.teammates.env): MAG_API_URL,
// MAG_INTERNAL_KEY, MAG_TENANT_ID.

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-teammates';
const SERVER_VERSION = '0.1.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';
const MAX_TEXT = 12000;

function log(...a) {
  process.stderr.write(`[mag-teammates] ${a.join(' ')}\n`);
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
    const message = typeof body.error === 'string' ? body.error : `MAG API ${res.status}`;
    throw new Error(message);
  }
  return body;
}

// ── tools ───────────────────────────────────────────────────────────────────
const tools = {
  list_teammates: {
    description:
      'Lista os colegas deste tenant que também usam a MAG (Companion, Telegram ou WhatsApp) — use ANTES de ' +
      'message_teammate pra decidir com quem o usuário quer falar (ex.: "fala com o Carlos" → procure o nome ' +
      'mais parecido na lista, é você quem julga o match, não existe busca automática por trás).',
    inputSchema: { type: 'object', properties: {} },
    async run() {
      const body = await callMag(`/internal/teammates?tenantId=${encodeURIComponent(MAG_TENANT_ID)}`);
      const teammates = Array.isArray(body.teammates) ? body.teammates : [];
      if (!teammates.length) return 'Nenhum colega cadastrado no roster deste tenant ainda.';
      return teammates.map((t) => ({
        id: t.id,
        name: t.name,
        online: t.online,
        canal: t.hasCompanion ? 'Companion' : t.hasTelegram ? 'Telegram' : t.hasWhatsapp ? 'WhatsApp' : 'nenhum vinculado',
      }));
    },
  },

  message_teammate: {
    description:
      'Manda uma mensagem de verdade pra um colega deste tenant (identificado via list_teammates). Use tanto pra ' +
      'INICIAR um encaminhamento (ex.: "fala com o Carlos que a reunião mudou") quanto pra RESPONDER algo que um ' +
      'colega acabou de mandar pra este usuário (ex.: "manda isso pro Junior"). Se a MAG do colega estiver ' +
      'aberta (Companion online), a entrega é imediata; senão cai pro Telegram/WhatsApp dele, se tiver vinculado. ' +
      'Se list_teammates não trouxer ninguém parecido, NÃO adivinhe — peça ao usuário o contato direto (telefone ' +
      'ou usuário do Telegram) dessa pessoa.',
    inputSchema: {
      type: 'object',
      properties: {
        teammateId: { type: 'string', description: 'id do colega, obtido via list_teammates.' },
        message: { type: 'string', description: 'Texto a entregar, em linguagem natural — não precisa reformatar.' },
        senderName: {
          type: 'string',
          description: 'Nome de quem está mandando (o usuário atual desta conversa) — obrigatório, pro colega saber quem falou.',
        },
        attachmentPath: {
          type: 'string',
          description:
            'Path absoluto de um arquivo já gerado nesta sessão (ex.: um PDF que você acabou de criar) pra ' +
            'encaminhar junto — mesmo path que entraria numa tag MEDIA:. Opcional.',
        },
      },
      required: ['teammateId', 'message', 'senderName'],
    },
    async run(args) {
      const body = await callMag(`/internal/teammates/${encodeURIComponent(args.teammateId)}/relay`, {
        method: 'POST',
        body: JSON.stringify({
          tenantId: MAG_TENANT_ID,
          senderName: args.senderName,
          message: args.message,
          ...(args.attachmentPath ? { attachmentPath: args.attachmentPath } : {}),
        }),
      });
      if (body.via === 'companion') return 'Entregue no Companion do colega agora mesmo.';
      if (body.via === 'telegram') return 'Entregue no Telegram do colega agora mesmo.';
      if (body.via === 'whatsapp_cloud') return 'Entregue no WhatsApp do colega agora mesmo.';
      return 'Mensagem entregue.';
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
