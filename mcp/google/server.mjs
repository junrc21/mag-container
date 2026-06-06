#!/usr/bin/env node
// MAG Google Workspace MCP server (stdio, zero-dependency).
//
// Exposes Gmail / Drive / Calendar (Meet) tools to the Hermes agent. It never
// stores Google credentials itself: on every call it asks the MAG control plane
// for a fresh, valid access token for the requested account and then calls the
// Google REST APIs directly. This keeps tokens (and refresh) centralized in MAG
// and supports MULTIPLE Google accounts per tenant.
//
// Required env (injected by Hermes via mcp_servers.google.env, interpolated from
// the runtime container env):
//   MAG_API_URL       e.g. http://host.docker.internal:3005
//   MAG_INTERNAL_KEY  service-to-service key (x-internal-key)
//   MAG_TENANT_ID     tenant uuid
//
// Protocol: MCP over stdio = newline-delimited JSON-RPC 2.0.

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-google';
const SERVER_VERSION = '0.1.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';

const GMAIL = 'https://gmail.googleapis.com/gmail/v1/users/me';
const DRIVE = 'https://www.googleapis.com/drive/v3';
const CALENDAR = 'https://www.googleapis.com/calendar/v3';

const MAX_TEXT = 12000; // cap any single tool result body

function log(...args) {
  process.stderr.write(`[mag-google] ${args.join(' ')}\n`);
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
  return s.length > MAX_TEXT ? s.slice(0, MAX_TEXT) + '\n…[truncated]' : s;
}

// ── MAG control-plane calls ────────────────────────────────────────────────
function magHeaders() {
  return { 'x-internal-key': MAG_INTERNAL_KEY, 'content-type': 'application/json' };
}

async function magListAccounts() {
  if (!MAG_API_URL || !MAG_INTERNAL_KEY || !MAG_TENANT_ID) {
    throw new Error('MCP não configurado (MAG_API_URL/MAG_INTERNAL_KEY/MAG_TENANT_ID ausentes).');
  }
  const res = await fetch(`${MAG_API_URL}/internal/google/accounts?tenantId=${encodeURIComponent(MAG_TENANT_ID)}`, {
    headers: magHeaders(),
  });
  if (!res.ok) throw new Error(`MAG accounts ${res.status}: ${await res.text()}`);
  return res.json();
}

async function magTokenFor(accountId) {
  const res = await fetch(
    `${MAG_API_URL}/internal/google/accounts/${encodeURIComponent(accountId)}/token?tenantId=${encodeURIComponent(MAG_TENANT_ID)}`,
    { headers: magHeaders() },
  );
  if (!res.ok) throw new Error(`MAG token ${res.status}: ${await res.text()}`);
  return res.json(); // { accountId, email, accessToken, expiresAt, scopes }
}

// Resolve which Google account to use. `account` may be an email (or partial).
async function resolveToken(account) {
  const accounts = await magListAccounts();
  if (!accounts.length) throw new Error('Nenhuma conta Google conectada. Conecte uma em Fontes.');
  let chosen;
  if (account) {
    const q = String(account).toLowerCase();
    chosen = accounts.find((a) => a.email.toLowerCase() === q) || accounts.find((a) => a.email.toLowerCase().includes(q));
    if (!chosen) {
      throw new Error(`Conta "${account}" não encontrada. Disponíveis: ${accounts.map((a) => a.email).join(', ')}`);
    }
  } else if (accounts.length === 1) {
    chosen = accounts[0];
  } else {
    throw new Error(
      `Há ${accounts.length} contas conectadas; informe "account". Disponíveis: ${accounts.map((a) => a.email).join(', ')}`,
    );
  }
  const tok = await magTokenFor(chosen.id);
  return { token: tok.accessToken, email: tok.email };
}

// ── Google API helper ──────────────────────────────────────────────────────
async function gfetch(token, url, opts = {}) {
  const res = await fetch(url, {
    ...opts,
    headers: { Authorization: `Bearer ${token}`, ...(opts.headers || {}) },
  });
  const text = await res.text();
  let body;
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = text;
  }
  if (!res.ok) {
    const msg = body?.error?.message || (typeof body === 'string' ? body : JSON.stringify(body));
    throw new Error(`Google API ${res.status}: ${msg}`);
  }
  return body;
}

function b64urlDecode(data) {
  if (!data) return '';
  return Buffer.from(String(data).replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8');
}

function b64urlEncode(str) {
  return Buffer.from(str, 'utf8').toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function gmailHeader(headers, name) {
  const h = (headers || []).find((x) => x.name.toLowerCase() === name.toLowerCase());
  return h ? h.value : '';
}

function extractPlainText(payload) {
  if (!payload) return '';
  if (payload.mimeType === 'text/plain' && payload.body?.data) return b64urlDecode(payload.body.data);
  if (payload.parts) {
    for (const p of payload.parts) {
      const t = extractPlainText(p);
      if (t) return t;
    }
  }
  if (payload.body?.data) return b64urlDecode(payload.body.data);
  return '';
}

// ── Tool implementations ───────────────────────────────────────────────────
const tools = {
  google_list_accounts: {
    description: 'Lista as contas Google conectadas a este MAG (e-mail, nome, status, permissões).',
    inputSchema: { type: 'object', properties: {} },
    async run() {
      const accounts = await magListAccounts();
      const lines = accounts.map((a) => `- ${a.email}${a.name ? ` (${a.name})` : ''} — ${a.status} — escopos: ${a.scopes?.length ?? 0}`);
      return accounts.length ? `Contas Google conectadas:\n${lines.join('\n')}` : 'Nenhuma conta Google conectada.';
    },
  },

  gmail_search: {
    description: 'Busca e-mails no Gmail. Retorna remetente, assunto, data e trecho. Use "query" no formato do Gmail (ex.: "from:foo is:unread").',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string', description: 'E-mail da conta Google (opcional se só houver uma).' },
        query: { type: 'string', description: 'Query de busca do Gmail.' },
        maxResults: { type: 'number', description: 'Máx. de mensagens (padrão 10).' },
      },
    },
    async run(args) {
      const { token } = await resolveToken(args.account);
      const max = Math.min(args.maxResults || 10, 25);
      const list = await gfetch(
        token,
        `${GMAIL}/messages?maxResults=${max}&q=${encodeURIComponent(args.query || '')}`,
      );
      const ids = (list.messages || []).map((m) => m.id);
      const out = [];
      for (const id of ids) {
        const msg = await gfetch(token, `${GMAIL}/messages/${id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date`);
        const h = msg.payload?.headers;
        out.push({ id, from: gmailHeader(h, 'From'), subject: gmailHeader(h, 'Subject'), date: gmailHeader(h, 'Date'), snippet: msg.snippet });
      }
      return out.length ? out : 'Nenhuma mensagem encontrada.';
    },
  },

  gmail_get_message: {
    description: 'Lê o conteúdo completo (texto) de um e-mail pelo id.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        id: { type: 'string', description: 'ID da mensagem do Gmail.' },
      },
      required: ['id'],
    },
    async run(args) {
      const { token } = await resolveToken(args.account);
      const msg = await gfetch(token, `${GMAIL}/messages/${encodeURIComponent(args.id)}?format=full`);
      const h = msg.payload?.headers;
      return {
        from: gmailHeader(h, 'From'),
        to: gmailHeader(h, 'To'),
        subject: gmailHeader(h, 'Subject'),
        date: gmailHeader(h, 'Date'),
        body: truncate(extractPlainText(msg.payload) || msg.snippet || ''),
      };
    },
  },

  gmail_send: {
    description: 'Envia um e-mail a partir da conta Google. ATENÇÃO: ação que escreve — confirme com o usuário antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        to: { type: 'string', description: 'Destinatário(s), separados por vírgula.' },
        subject: { type: 'string' },
        body: { type: 'string', description: 'Corpo do e-mail (texto).' },
        cc: { type: 'string' },
      },
      required: ['to', 'subject', 'body'],
    },
    async run(args) {
      const { token, email } = await resolveToken(args.account);
      const headers = [`From: ${email}`, `To: ${args.to}`];
      if (args.cc) headers.push(`Cc: ${args.cc}`);
      headers.push(`Subject: ${args.subject}`, 'Content-Type: text/plain; charset="UTF-8"', 'MIME-Version: 1.0');
      const raw = b64urlEncode(`${headers.join('\r\n')}\r\n\r\n${args.body}`);
      const res = await gfetch(token, `${GMAIL}/messages/send`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ raw }),
      });
      return `E-mail enviado (id ${res.id}).`;
    },
  },

  drive_search: {
    description: 'Busca arquivos no Google Drive por nome/conteúdo. Retorna id, nome, tipo e link.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        query: { type: 'string', description: 'Texto a buscar no nome/conteúdo dos arquivos.' },
        pageSize: { type: 'number', description: 'Máx. de arquivos (padrão 15).' },
      },
    },
    async run(args) {
      const { token } = await resolveToken(args.account);
      const size = Math.min(args.pageSize || 15, 50);
      let q = "trashed = false";
      if (args.query) {
        const esc = String(args.query).replace(/'/g, "\\'");
        q = `(name contains '${esc}' or fullText contains '${esc}') and trashed = false`;
      }
      const data = await gfetch(
        token,
        `${DRIVE}/files?q=${encodeURIComponent(q)}&pageSize=${size}&fields=${encodeURIComponent('files(id,name,mimeType,modifiedTime,webViewLink,owners(emailAddress))')}&supportsAllDrives=true&includeItemsFromAllDrives=true`,
      );
      return (data.files && data.files.length) ? data.files : 'Nenhum arquivo encontrado.';
    },
  },

  drive_get_file: {
    description: 'Obtém metadados e (quando possível) o texto de um arquivo do Drive. Exporta Google Docs como texto.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        fileId: { type: 'string' },
      },
      required: ['fileId'],
    },
    async run(args) {
      const { token } = await resolveToken(args.account);
      const meta = await gfetch(
        token,
        `${DRIVE}/files/${encodeURIComponent(args.fileId)}?fields=${encodeURIComponent('id,name,mimeType,modifiedTime,size,webViewLink')}&supportsAllDrives=true`,
      );
      let content = '';
      try {
        if (meta.mimeType?.startsWith('application/vnd.google-apps.')) {
          if (meta.mimeType === 'application/vnd.google-apps.spreadsheet') {
            content = await fetchText(token, `${DRIVE}/files/${args.fileId}/export?mimeType=text/csv`);
          } else {
            content = await fetchText(token, `${DRIVE}/files/${args.fileId}/export?mimeType=text/plain`);
          }
        } else if (meta.mimeType?.startsWith('text/') || meta.mimeType === 'application/json') {
          content = await fetchText(token, `${DRIVE}/files/${args.fileId}?alt=media&supportsAllDrives=true`);
        } else {
          content = '[conteúdo binário não extraído — use webViewLink]';
        }
      } catch (e) {
        content = `[falha ao extrair conteúdo: ${e.message}]`;
      }
      return { ...meta, content: truncate(content) };
    },
  },

  calendar_list_events: {
    description: 'Lista eventos da agenda (Google Calendar) num intervalo. Útil para reuniões/Meet.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        timeMin: { type: 'string', description: 'ISO8601 (padrão: agora).' },
        timeMax: { type: 'string', description: 'ISO8601 (opcional).' },
        maxResults: { type: 'number', description: 'Padrão 10.' },
        calendarId: { type: 'string', description: 'Padrão "primary".' },
      },
    },
    async run(args) {
      const { token } = await resolveToken(args.account);
      const cal = encodeURIComponent(args.calendarId || 'primary');
      const params = new URLSearchParams({
        singleEvents: 'true',
        orderBy: 'startTime',
        maxResults: String(Math.min(args.maxResults || 10, 50)),
        timeMin: args.timeMin || new Date().toISOString(),
      });
      if (args.timeMax) params.set('timeMax', args.timeMax);
      const data = await gfetch(token, `${CALENDAR}/calendars/${cal}/events?${params.toString()}`);
      const events = (data.items || []).map((e) => ({
        id: e.id,
        summary: e.summary,
        start: e.start?.dateTime || e.start?.date,
        end: e.end?.dateTime || e.end?.date,
        attendees: (e.attendees || []).map((a) => a.email),
        meetLink: e.hangoutLink || null,
        htmlLink: e.htmlLink,
      }));
      return events.length ? events : 'Nenhum evento no intervalo.';
    },
  },

  calendar_create_event: {
    description: 'Cria um evento/reunião na agenda (opcionalmente com link do Google Meet). Se houver convidados (attendees), envia o convite por e-mail a eles. Ação que escreve — confirme antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        summary: { type: 'string' },
        start: { type: 'string', description: 'ISO8601 de início.' },
        end: { type: 'string', description: 'ISO8601 de fim.' },
        description: { type: 'string' },
        attendees: { type: 'array', items: { type: 'string' }, description: 'E-mails dos convidados.' },
        addMeet: { type: 'boolean', description: 'Se true, cria link do Google Meet.' },
        calendarId: { type: 'string' },
      },
      required: ['summary', 'start', 'end'],
    },
    async run(args) {
      const { token } = await resolveToken(args.account);
      const cal = encodeURIComponent(args.calendarId || 'primary');
      const body = {
        summary: args.summary,
        description: args.description,
        start: { dateTime: args.start },
        end: { dateTime: args.end },
      };
      const params = new URLSearchParams();
      if (args.attendees?.length) {
        body.attendees = args.attendees.map((email) => ({ email }));
        // Actually email the invite/update to the guests (default would add them
        // silently). Lets the agent "agendar e convidar" in one step.
        params.set('sendUpdates', 'all');
      }
      if (args.addMeet) {
        body.conferenceData = { createRequest: { requestId: `mag-${Date.now()}`, conferenceSolutionKey: { type: 'hangoutsMeet' } } };
        params.set('conferenceDataVersion', '1');
      }
      const qs = params.toString();
      const url = `${CALENDAR}/calendars/${cal}/events${qs ? `?${qs}` : ''}`;
      const ev = await gfetch(token, url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      return { id: ev.id, htmlLink: ev.htmlLink, meetLink: ev.hangoutLink || null };
    },
  },
};

async function fetchText(token, url) {
  const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
  const text = await res.text();
  if (!res.ok) throw new Error(`Google API ${res.status}: ${text.slice(0, 200)}`);
  return text;
}

// ── JSON-RPC dispatch ──────────────────────────────────────────────────────
function toolList() {
  return Object.entries(tools).map(([name, t]) => ({
    name,
    description: t.description,
    inputSchema: t.inputSchema,
  }));
}

async function handleMessage(msg) {
  const { id, method, params } = msg;

  // Notifications (no id) — never reply.
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
      const t = tools[name];
      if (!t) return reply(id, { content: [{ type: 'text', text: `Ferramenta desconhecida: ${name}` }], isError: true });
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

log(`started (api=${MAG_API_URL || 'unset'} tenant=${MAG_TENANT_ID || 'unset'})`);
