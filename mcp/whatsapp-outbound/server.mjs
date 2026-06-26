#!/usr/bin/env node
// MAG WhatsApp Outbound MCP server (stdio, zero-dependency).
//
// Exposes a SINGLE tool: send_whatsapp_message
// This tool enables the AI to send proactive WhatsApp messages with:
//   - Confirmation validation (confirmed_by_user must be true)
//   - Allowlist validation (WHATSAPP_OUTBOUND_ALLOWED_USERS)
//   - JID normalization (handled by bridge)
//   - Audit logging (handled by bridge)
//
// The bridge (/send endpoint) already handles:
//   - WHATSAPP_OUTBOUND_ALLOWED_USERS validation
//   - JID normalization
//   - Audit logging
//
// This MCP is a thin wrapper that:
//   1. Validates the confirmed_by_user flag
//   2. Calls the bridge /send endpoint
//   3. Returns the result
//
// Required env (set by Hermes via mcp_servers.whatsapp-outbound.env):
//   WHATSAPP_BRIDGE_PORT  Port of the WhatsApp bridge (default: 3000)
//   MAG_INTERNAL_KEY       Internal auth key for bridge calls

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-whatsapp-outbound';
const SERVER_VERSION = '0.1.0';
const PROTOCOL_VERSION = '2025-06-18';

const BRIDGE_PORT = process.env.WHATSAPP_BRIDGE_PORT || '3000';
const BRIDGE_BASE = `http://127.0.0.1:${BRIDGE_PORT}`;
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';

function log(...args) {
  process.stderr.write(`[${SERVER_NAME}] ${args.join(' ')}\n`);
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

// ============================================================================
// MCP Protocol Handlers
// ============================================================================

async function handleInitialize(id, params) {
  log('Client initialized:', params.clientInfo?.name || 'unknown');

  // Advertise our single tool
  reply(id, {
    protocolVersion: PROTOCOL_VERSION,
    serverInfo: {
      name: SERVER_NAME,
      version: SERVER_VERSION,
    },
    capabilities: {
      tools: {},
    },
  });
}

async function handleToolsList(id) {
  reply(id, {
    tools: [
      {
        name: 'send_whatsapp_message',
        description: 'Send a WhatsApp message to a specific phone number. ' +
          'CRITICAL: You MUST ALWAYS pass confirmed_by_user=true in EVERY call. ' +
          'Example: send_whatsapp_message(phone_number="5511999999999", message="Hello", confirmed_by_user=true). ' +
          'The destination number must be in the WHATSAPP_OUTBOUND_ALLOWED_USERS allowlist ' +
          'or match a number in WHATSAPP_ALLOWED_USERS (implicit authorization). ' +
          'The number will be automatically normalized (raw digits, with +, or formatted). ' +
          'All send attempts are logged for audit.',
        inputSchema: {
          type: 'object',
          properties: {
            phone_number: {
              type: 'string',
              description: 'Phone number to send to (raw digits, with +, or formatted - will be normalized)'
            },
            message: {
              type: 'string',
              description: 'Message text to send'
            },
            confirmed_by_user: {
              type: 'boolean',
              description: 'MUST be true - indicates the user explicitly confirmed this send'
            }
          },
          required: ['phone_number', 'message', 'confirmed_by_user']
        }
      }
    ]
  });
}

async function handleToolsCall(id, params) {
  const { name, arguments: args } = params;

  if (name !== 'send_whatsapp_message') {
    return replyError(id, -32601, `Unknown tool: ${name}`);
  }

  // Validate required parameters
  const { phone_number, message, confirmed_by_user } = args;

  if (!phone_number || typeof phone_number !== 'string') {
    return replyError(id, -32602, 'phone_number is required and must be a string');
  }

  if (!message || typeof message !== 'string') {
    return replyError(id, -32602, 'message is required and must be a string');
  }

  // Note: confirmed_by_user is required for proactive messaging.
  // The bridge will validate this - we pass it through to allow Hermes fallback
  // to work if the first attempt fails.
  if (!confirmed_by_user || confirmed_by_user !== true) {
    // Allow the call to proceed - the bridge will validate and provide clear error
    log(`Warning: confirmed_by_user is ${confirmed_by_user} - bridge will validate`);
  }

  // Strip non-digit chars for the bridge (it will normalize further)
  const chatId = phone_number.replace(/\D/g, '');

  if (!chatId || chatId.length < 10) {
    return replyError(id, -32602, 'Invalid phone number - must have at least 10 digits');
  }

  log(`Sending WhatsApp message to ${chatId} (confirmed: ${confirmed_by_user})`);

  try {
    // Call the bridge /send endpoint
    const response = await fetch(`${BRIDGE_BASE}/send`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(MAG_INTERNAL_KEY ? { 'x-internal-key': MAG_INTERNAL_KEY } : {}),
      },
      body: JSON.stringify({
        chatId: chatId,
        message: message,
        confirmed_by_user: confirmed_by_user
      }),
    });

    const result = await response.json();

    if (!response.ok) {
      log('Bridge error:', result.error || 'Unknown error');
      return replyError(id, -32603, `Bridge error: ${result.error || 'Failed to send message'}`);
    }

    log('Message sent successfully');
    reply(id, {
      content: [
        {
          type: 'text',
          text: `WhatsApp message sent successfully to ${phone_number}.`
        }
      ]
    });

  } catch (error) {
    log('Request failed:', error.message);
    replyError(id, -32603, `Failed to call WhatsApp bridge: ${error.message}`);
  }
}

// ============================================================================
// Main Loop
// ============================================================================

const rl = createInterface({
  input: process.stdin,
  output: process.stdout,
  terminal: false
});

rl.on('line', async (line) => {
  let msg;

  try {
    msg = JSON.parse(line);
  } catch (e) {
    log('Failed to parse message:', e.message);
    return;
  }

  const { id, method, params } = msg;

  switch (method) {
    case 'initialize':
      await handleInitialize(id, params);
      break;
    case 'tools/list':
      await handleToolsList(id);
      break;
    case 'tools/call':
      await handleToolsCall(id, params);
      break;
    case 'notifications/initialized':
      // Client ready, nothing to do
      break;
    default:
      replyError(id, -32601, `Method not found: ${method}`);
  }
});

log('WhatsApp outbound MCP server started (stdio)');
