#!/usr/bin/env node
// MAG WhatsApp Outbound MCP server (stdio, zero-dependency).
//
// Exposes the `send_whatsapp_message` tool to the Hermes agent so it can
// proactively send WhatsApp messages to authorized contacts when instructed by
// the user (gestor/owner of the MAG account).
//
// Flow:
//   agent calls send_whatsapp_message(phone_number, message, confirmed_by_user=true)
//   → this server validates params
//   → POSTs to the WhatsApp bridge /send endpoint (localhost:WHATSAPP_BRIDGE_PORT)
//   → bridge validates allowlist + sends via Baileys socket
//   → returns {success, messageId} or an error message
//
// Required env (via mcp_servers.whatsapp-outbound.env in config.yaml):
//   WHATSAPP_BRIDGE_PORT  - port the bridge HTTP server listens on (default: 3000)
//   MAG_INTERNAL_KEY      - kept for future auth; not used in bridge call today

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-whatsapp-outbound';
const SERVER_VERSION = '0.1.0';
const PROTOCOL_VERSION = '2025-06-18';

const BRIDGE_PORT = process.env.WHATSAPP_BRIDGE_PORT || '3000';
const BRIDGE_URL  = `http://localhost:${BRIDGE_PORT}`;

function log(...a) { process.stderr.write(`[mag-whatsapp-outbound] ${a.join(' ')}\n`); }
function send(m)   { process.stdout.write(JSON.stringify(m) + '\n'); }
function reply(id, result)         { send({ jsonrpc: '2.0', id, result }); }
function replyError(id, code, msg) { send({ jsonrpc: '2.0', id, error: { code, message: msg } }); }

// ── bridge call ─────────────────────────────────────────────────────────────

async function bridgeSend(chatId, message) {
  const res = await fetch(`${BRIDGE_URL}/send`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ chatId, message, confirmed_by_user: true }),
  });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = { raw: text }; }
  if (!res.ok) {
    const errMsg = data?.error || (typeof data?.raw === 'string' ? data.raw : JSON.stringify(data));
    throw new Error(`Bridge ${res.status}: ${String(errMsg).slice(0, 200)}`);
  }
  return data;
}

// ── tool definition ─────────────────────────────────────────────────────────

const TOOLS = [
  {
    name: 'send_whatsapp_message',
    description:
      'Envia uma mensagem de WhatsApp proativamente para um contato. ' +
      'OBRIGATÓRIO: confirme com o usuário antes de chamar esta ferramenta (confirmed_by_user deve ser true). ' +
      'O número de destino deve estar na lista de envios permitidos configurada pelo gestor.',
    inputSchema: {
      type: 'object',
      properties: {
        phone_number: {
          type: 'string',
          description:
            'Número de telefone do destinatário, em qualquer formato. ' +
            'Ex.: "+55 11 99999-8888", "5511999998888", "11999998888". ' +
            'Sempre inclua o código do país (55 para Brasil).',
        },
        message: {
          type: 'string',
          description: 'Texto da mensagem a enviar.',
        },
        confirmed_by_user: {
          type: 'boolean',
          description:
            'Deve ser true. Confirma que o usuário (gestor) autorizou explicitamente ' +
            'o envio desta mensagem para este contato nesta sessão.',
        },
      },
      required: ['phone_number', 'message', 'confirmed_by_user'],
    },
  },
];

// ── tool execution ───────────────────────────────────────────────────────────

async function callTool(name, args) {
  if (name !== 'send_whatsapp_message') {
    throw new Error(`Tool desconhecida: ${name}`);
  }

  const { phone_number, message, confirmed_by_user } = args || {};

  if (!confirmed_by_user) {
    throw new Error(
      'confirmed_by_user deve ser true. Confirme com o usuário antes de enviar.',
    );
  }

  if (!phone_number || typeof phone_number !== 'string') {
    throw new Error('phone_number é obrigatório.');
  }

  if (!message || typeof message !== 'string' || !message.trim()) {
    throw new Error('message é obrigatório e não pode estar vazio.');
  }

  const digits = phone_number.replace(/\D/g, '');
  if (!digits || digits.length < 8) {
    throw new Error(
      `Número inválido: "${phone_number}". Use o formato completo com código do país, ex.: 5511999998888.`,
    );
  }

  // Baileys requer JID completo com sufixo — dígitos puros causam jidDecode crash.
  // Grupos têm 17+ dígitos → @g.us; DMs → @s.whatsapp.net.
  const jid = digits.length >= 17 ? `${digits}@g.us` : `${digits}@s.whatsapp.net`;

  log(`Sending to ${jid} (from "${phone_number}"): ${message.slice(0, 60)}`);

  const result = await bridgeSend(jid, message);

  log(`OK — messageId: ${result.messageId ?? '(sem id)'}`);
  return `Mensagem enviada com sucesso para ${phone_number}.` +
    (result.messageId ? ` (id: ${result.messageId})` : '');
}

// ── JSON-RPC dispatcher ──────────────────────────────────────────────────────

async function handle(msg) {
  const { id, method, params } = msg;

  if (method === 'initialize') {
    return reply(id, {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: { tools: {} },
      serverInfo: { name: SERVER_NAME, version: SERVER_VERSION },
    });
  }

  if (method === 'notifications/initialized') return; // no response needed

  if (method === 'tools/list') {
    return reply(id, { tools: TOOLS });
  }

  if (method === 'tools/call') {
    const toolName = params?.name;
    const toolArgs = params?.arguments ?? {};
    try {
      const text = await callTool(toolName, toolArgs);
      return reply(id, { content: [{ type: 'text', text }] });
    } catch (err) {
      const msg = (err && err.message) || String(err);
      log(`Error in ${toolName}:`, msg);
      return reply(id, {
        content: [{ type: 'text', text: `Erro: ${msg}` }],
        isError: true,
      });
    }
  }

  // Unknown method — return JSON-RPC error
  if (id !== undefined) {
    replyError(id, -32601, `Method not found: ${method}`);
  }
}

// ── main loop ────────────────────────────────────────────────────────────────

const rl = createInterface({ input: process.stdin, terminal: false });

rl.on('line', (line) => {
  if (!line.trim()) return;
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    log('Invalid JSON:', line.slice(0, 100));
    return;
  }
  handle(msg).catch((err) => {
    log('Unhandled error:', err && err.message);
    if (msg?.id !== undefined) replyError(msg.id, -32603, 'Internal error');
  });
});

rl.on('close', () => process.exit(0));

log(`Started (bridge: ${BRIDGE_URL})`);
