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
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

const SERVER_NAME = 'mag-google';
const SERVER_VERSION = '0.3.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';

const GMAIL = 'https://gmail.googleapis.com/gmail/v1/users/me';
const DRIVE = 'https://www.googleapis.com/drive/v3';
const CALENDAR = 'https://www.googleapis.com/calendar/v3';

const MAX_TEXT = 12000; // cap any single tool result body

// Same convention pdf-tools-mcp uses: write the file into the tenant's workspace
// (bind-mounted, readable by the gateway) and tell the agent to include a
// MEDIA:<path> line in its own reply — the channel adapter scans the agent's
// final text for that marker and delivers the file as a real attachment.
const WORKSPACE_DIR = '/opt/data/workspace/google';

const EXT_MIME_TYPES = {
  '.pdf': 'application/pdf',
  '.doc': 'application/msword',
  '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  '.xls': 'application/vnd.ms-excel',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.ppt': 'application/vnd.ms-powerpoint',
  '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  '.txt': 'text/plain',
  '.csv': 'text/csv',
  '.json': 'application/json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.zip': 'application/zip',
};

function mimeTypeFor(filePath) {
  return EXT_MIME_TYPES[path.extname(filePath).toLowerCase()] || 'application/octet-stream';
}

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

// Drive's "create with content in one shot" endpoint (uploadType=multipart) needs a
// hand-rolled multipart/related body: one JSON part (metadata) + one part with the
// raw file content. No multipart library here (zero-dependency server) — this is the
// documented, minimal shape Drive's API expects.
async function driveMultipartUpload(token, url, method, metadata, content, contentMimeType) {
  const boundary = `mag-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const body =
    `--${boundary}\r\n` +
    `Content-Type: application/json; charset=UTF-8\r\n\r\n` +
    `${JSON.stringify(metadata)}\r\n` +
    `--${boundary}\r\n` +
    `Content-Type: ${contentMimeType}; charset=UTF-8\r\n\r\n` +
    `${content}\r\n` +
    `--${boundary}--`;
  const uploadUrl = url.replace('https://www.googleapis.com/', 'https://www.googleapis.com/upload/');
  const qs = `uploadType=multipart&supportsAllDrives=true&fields=${encodeURIComponent('id,name,mimeType,webViewLink')}`;
  return gfetch(token, `${uploadUrl}?${qs}`, {
    method,
    headers: { 'content-type': `multipart/related; boundary=${boundary}` },
    body,
  });
}

function b64urlDecode(data) {
  if (!data) return '';
  return Buffer.from(String(data).replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString('utf8');
}

// Same base64url alphabet fixup as b64urlDecode, but returns the raw Buffer
// instead of a UTF-8 string — required for binary attachment bytes (a PDF/image
// decoded through toString('utf8') would be corrupted).
function b64urlDecodeBuffer(data) {
  if (!data) return Buffer.alloc(0);
  return Buffer.from(String(data).replace(/-/g, '+').replace(/_/g, '/'), 'base64');
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

// Walks a message's MIME tree collecting every part that IS an attachment
// (has a filename and a body.attachmentId — inline text/html parts have neither).
function extractAttachments(payload) {
  const out = [];
  function walk(part) {
    if (!part) return;
    if (part.filename && part.body?.attachmentId) {
      out.push({ filename: part.filename, attachmentId: part.body.attachmentId, mimeType: part.mimeType, size: part.body.size });
    }
    if (part.parts) part.parts.forEach(walk);
  }
  walk(payload);
  return out;
}

async function gmailFindLabelId(token, name) {
  const data = await gfetch(token, `${GMAIL}/labels`);
  const found = (data.labels || []).find((l) => l.name.toLowerCase() === String(name).toLowerCase());
  if (!found) {
    throw new Error(`Label "${name}" não encontrada. Labels existentes: ${(data.labels || []).map((l) => l.name).join(', ')}`);
  }
  return found.id;
}

// Builds a raw RFC 2822 message. With no attachments it's a single text/plain
// body (original gmail_send behavior); with attachments it becomes a
// multipart/mixed message — one text part + one base64 part per attachment.
function buildMimeMessage({ from, to, cc, subject, body, attachments }) {
  const headers = [`From: ${from}`, `To: ${to}`];
  if (cc) headers.push(`Cc: ${cc}`);
  headers.push(`Subject: ${subject}`, 'MIME-Version: 1.0');

  if (!attachments || !attachments.length) {
    headers.push('Content-Type: text/plain; charset="UTF-8"');
    return `${headers.join('\r\n')}\r\n\r\n${body}`;
  }

  const boundary = `mag-mime-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  headers.push(`Content-Type: multipart/mixed; boundary="${boundary}"`);
  let msg = `${headers.join('\r\n')}\r\n\r\n--${boundary}\r\nContent-Type: text/plain; charset="UTF-8"\r\n\r\n${body}\r\n\r\n`;
  for (const att of attachments) {
    msg +=
      `--${boundary}\r\n` +
      `Content-Type: ${att.mimeType}; name="${att.filename}"\r\n` +
      `Content-Disposition: attachment; filename="${att.filename}"\r\n` +
      `Content-Transfer-Encoding: base64\r\n\r\n` +
      `${att.content.toString('base64')}\r\n\r\n`;
  }
  msg += `--${boundary}--`;
  return msg;
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
      const attachments = extractAttachments(msg.payload);
      return {
        from: gmailHeader(h, 'From'),
        to: gmailHeader(h, 'To'),
        subject: gmailHeader(h, 'Subject'),
        date: gmailHeader(h, 'Date'),
        body: truncate(extractPlainText(msg.payload) || msg.snippet || ''),
        attachments: attachments.length
          ? attachments
          : undefined, // omit the field entirely when there are none, cleaner for the model
      };
    },
  },

  gmail_get_attachment: {
    description: 'Baixa um anexo de e-mail (pelo messageId + attachmentId de gmail_get_message) e salva no workspace do tenant. Inclua uma linha MEDIA:<caminho> na sua resposta pra entregar o arquivo ao usuário no chat.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        messageId: { type: 'string', description: 'ID da mensagem (ver gmail_search/gmail_get_message).' },
        attachmentId: { type: 'string', description: 'ID do anexo (ver gmail_get_message.attachments).' },
        filename: { type: 'string', description: 'Nome do arquivo a salvar (ver gmail_get_message.attachments).' },
      },
      required: ['messageId', 'attachmentId', 'filename'],
    },
    async run(args) {
      const { token } = await resolveToken(args.account);
      const data = await gfetch(
        token,
        `${GMAIL}/messages/${encodeURIComponent(args.messageId)}/attachments/${encodeURIComponent(args.attachmentId)}`,
      );
      const buf = b64urlDecodeBuffer(data.data);
      await mkdir(WORKSPACE_DIR, { recursive: true });
      const safeName = String(args.filename).replace(/[/\\]/g, '_');
      const outPath = path.join(WORKSPACE_DIR, `${Date.now()}-${safeName}`);
      await writeFile(outPath, buf);
      return `Anexo salvo. Para enviar ao usuário, inclua na sua resposta:\nMEDIA:${outPath}`;
    },
  },

  gmail_send: {
    description: 'Envia um e-mail a partir da conta Google, opcionalmente com anexos (arquivos já no workspace do tenant). ATENÇÃO: ação que escreve — confirme com o usuário antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        to: { type: 'string', description: 'Destinatário(s), separados por vírgula.' },
        subject: { type: 'string' },
        body: { type: 'string', description: 'Corpo do e-mail (texto).' },
        cc: { type: 'string' },
        attachmentPaths: { type: 'array', items: { type: 'string' }, description: 'Caminhos de arquivo (ex.: /opt/data/workspace/...) pra anexar.' },
      },
      required: ['to', 'subject', 'body'],
    },
    async run(args) {
      const { token, email } = await resolveToken(args.account);
      let attachments;
      if (args.attachmentPaths?.length) {
        attachments = [];
        for (const p of args.attachmentPaths) {
          const content = await readFile(p);
          attachments.push({ filename: path.basename(p), mimeType: mimeTypeFor(p), content });
        }
      }
      const raw = b64urlEncode(buildMimeMessage({ from: email, to: args.to, cc: args.cc, subject: args.subject, body: args.body, attachments }));
      const res = await gfetch(token, `${GMAIL}/messages/send`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ raw }),
      });
      return `E-mail enviado (id ${res.id}).`;
    },
  },

  gmail_create_draft: {
    description: 'Cria um rascunho de e-mail (não envia — fica salvo no Gmail pra revisão/envio manual depois).',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        to: { type: 'string' },
        subject: { type: 'string' },
        body: { type: 'string' },
        cc: { type: 'string' },
      },
      required: ['to', 'subject', 'body'],
    },
    async run(args) {
      const { token, email } = await resolveToken(args.account);
      const raw = b64urlEncode(buildMimeMessage({ from: email, to: args.to, cc: args.cc, subject: args.subject, body: args.body }));
      const res = await gfetch(token, `${GMAIL}/drafts`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ message: { raw } }),
      });
      return `Rascunho criado (id ${res.id}).`;
    },
  },

  gmail_list_drafts: {
    description: 'Lista os rascunhos de e-mail salvos (destinatário e assunto).',
    inputSchema: {
      type: 'object',
      properties: { account: { type: 'string' }, maxResults: { type: 'number', description: 'Padrão 10.' } },
    },
    async run(args) {
      const { token } = await resolveToken(args.account);
      const max = Math.min(args.maxResults || 10, 25);
      const list = await gfetch(token, `${GMAIL}/drafts?maxResults=${max}`);
      const ids = (list.drafts || []).map((d) => d.id);
      const out = [];
      for (const id of ids) {
        const d = await gfetch(token, `${GMAIL}/drafts/${id}?format=metadata&metadataHeaders=To&metadataHeaders=Subject`);
        const h = d.message?.payload?.headers;
        out.push({ id, to: gmailHeader(h, 'To'), subject: gmailHeader(h, 'Subject'), snippet: d.message?.snippet });
      }
      return out.length ? out : 'Nenhum rascunho encontrado.';
    },
  },

  gmail_create_label: {
    description: 'Cria uma label (etiqueta) personalizada no Gmail. Depois use gmail_update_message com addLabel pra aplicá-la a um e-mail.',
    inputSchema: {
      type: 'object',
      properties: { account: { type: 'string' }, name: { type: 'string', description: 'Nome da label.' } },
      required: ['name'],
    },
    async run(args) {
      const { token } = await resolveToken(args.account);
      const label = await gfetch(token, `${GMAIL}/labels`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name: args.name, labelListVisibility: 'labelShow', messageListVisibility: 'show' }),
      });
      return { id: label.id, name: label.name };
    },
  },

  gmail_update_message: {
    description: 'Atualiza o estado de um e-mail existente: marca como lido/não lido, favorita/desfavorita, arquiva/desarquiva, e/ou aplica/remove uma label personalizada (deve já existir — ver gmail_create_label). Informe só os campos que quer mudar. Ação que escreve — confirme com o usuário antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        id: { type: 'string', description: 'ID da mensagem do Gmail (ver gmail_search).' },
        markRead: { type: 'boolean', description: 'true = marca como lido, false = marca como não lido.' },
        star: { type: 'boolean', description: 'true = favorita, false = remove o favorito.' },
        archive: { type: 'boolean', description: 'true = arquiva (remove da caixa de entrada), false = volta pra caixa de entrada.' },
        addLabel: { type: 'string', description: 'Nome de uma label existente pra aplicar.' },
        removeLabel: { type: 'string', description: 'Nome de uma label existente pra remover.' },
      },
      required: ['id'],
    },
    async run(args) {
      if (
        args.markRead === undefined &&
        args.star === undefined &&
        args.archive === undefined &&
        !args.addLabel &&
        !args.removeLabel
      ) {
        throw new Error('Informe ao menos um de: markRead, star, archive, addLabel, removeLabel.');
      }
      const { token } = await resolveToken(args.account);
      const addLabelIds = [];
      const removeLabelIds = [];
      if (args.markRead === true) removeLabelIds.push('UNREAD');
      if (args.markRead === false) addLabelIds.push('UNREAD');
      if (args.star === true) addLabelIds.push('STARRED');
      if (args.star === false) removeLabelIds.push('STARRED');
      if (args.archive === true) removeLabelIds.push('INBOX');
      if (args.archive === false) addLabelIds.push('INBOX');
      if (args.addLabel) addLabelIds.push(await gmailFindLabelId(token, args.addLabel));
      if (args.removeLabel) removeLabelIds.push(await gmailFindLabelId(token, args.removeLabel));
      const msg = await gfetch(
        token,
        `${GMAIL}/messages/${encodeURIComponent(args.id)}/modify`,
        { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ addLabelIds, removeLabelIds }) },
      );
      return { id: msg.id, labelIds: msg.labelIds };
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

  drive_create_file: {
    description: 'Cria um arquivo no Google Drive com o conteúdo fornecido. Por padrão cria um arquivo de texto simples; use asGoogleDoc=true para criar um Google Docs nativo (editável direto no Drive). Ação que escreve — confirme com o usuário antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        name: { type: 'string', description: 'Nome do arquivo.' },
        content: { type: 'string', description: 'Conteúdo em texto do arquivo.' },
        mimeType: { type: 'string', description: 'MIME type do conteúdo enviado (padrão text/plain). Ignorado se asGoogleDoc=true.' },
        asGoogleDoc: { type: 'boolean', description: 'Se true, converte o conteúdo pra um Google Docs nativo em vez de um arquivo de texto simples.' },
        parentFolderId: { type: 'string', description: 'ID da pasta de destino (opcional; padrão raiz do Drive do usuário).' },
      },
      required: ['name', 'content'],
    },
    async run(args) {
      const { token } = await resolveToken(args.account);
      const sourceMime = args.mimeType || 'text/plain';
      const metadata = {
        name: String(args.name),
        ...(args.parentFolderId ? { parents: [String(args.parentFolderId)] } : {}),
        // Setting mimeType to a Google Docs type on create makes Drive CONVERT the
        // uploaded content into a native, editable Google Doc instead of storing it
        // as opaque bytes — this is the documented Drive API conversion behavior.
        ...(args.asGoogleDoc ? { mimeType: 'application/vnd.google-apps.document' } : {}),
      };
      const file = await driveMultipartUpload(token, `${DRIVE}/files`, 'POST', metadata, String(args.content), sourceMime);
      return { id: file.id, name: file.name, mimeType: file.mimeType, webViewLink: file.webViewLink };
    },
  },

  drive_update_file: {
    description: 'Atualiza o conteúdo e/ou o nome de um arquivo existente no Google Drive (pelo fileId, ver drive_search). Só funciona para arquivos de texto/binário simples — NÃO edita o conteúdo de um Google Docs/Sheets/Slides nativo (só o nome desses). Ação que escreve — confirme com o usuário antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        fileId: { type: 'string' },
        content: { type: 'string', description: 'Novo conteúdo — substitui o conteúdo atual do arquivo.' },
        name: { type: 'string', description: 'Novo nome, se quiser renomear.' },
        mimeType: { type: 'string', description: 'MIME type do novo conteúdo (padrão text/plain).' },
      },
      required: ['fileId'],
    },
    async run(args) {
      if (args.content === undefined && !args.name) {
        throw new Error('Informe "content" e/ou "name" para atualizar o arquivo.');
      }
      const { token } = await resolveToken(args.account);
      const fileUrl = `${DRIVE}/files/${encodeURIComponent(args.fileId)}`;
      let file;
      if (args.content !== undefined) {
        // Simple media upload replaces the raw bytes — works for plain/binary files,
        // but Drive rejects it for native Google Docs/Sheets/Slides (they have no
        // "raw bytes" to overwrite; editing those needs the separate Docs/Sheets API,
        // out of scope here — the tool description above warns about this). Like
        // create, media upload goes through the /upload/ path, not the plain API path.
        const uploadUrl = fileUrl.replace('https://www.googleapis.com/', 'https://www.googleapis.com/upload/');
        file = await gfetch(
          token,
          `${uploadUrl}?uploadType=media&supportsAllDrives=true&fields=${encodeURIComponent('id,name,mimeType,webViewLink')}`,
          { method: 'PATCH', headers: { 'content-type': args.mimeType || 'text/plain' }, body: String(args.content) },
        );
      }
      if (args.name) {
        file = await gfetch(
          token,
          `${fileUrl}?supportsAllDrives=true&fields=${encodeURIComponent('id,name,mimeType,webViewLink')}`,
          { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ name: args.name }) },
        );
      }
      return { id: file.id, name: file.name, mimeType: file.mimeType, webViewLink: file.webViewLink };
    },
  },

  drive_delete_file: {
    description: 'Move um arquivo do Google Drive para a lixeira (reversível pelo usuário na lixeira do Drive — não é uma exclusão permanente). Ação que escreve — confirme com o usuário antes.',
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
      const file = await gfetch(
        token,
        `${DRIVE}/files/${encodeURIComponent(args.fileId)}?supportsAllDrives=true&fields=${encodeURIComponent('id,name,trashed')}`,
        { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ trashed: true }) },
      );
      return { id: file.id, name: file.name, trashed: file.trashed };
    },
  },

  drive_share_file: {
    description: 'Compartilha um arquivo do Google Drive — com uma pessoa específica (por e-mail) e/ou gera um link acessível a qualquer um que o tiver. Informe "email" e/ou "anyoneWithLink":true (pelo menos um dos dois). Ação que escreve — confirme com o usuário antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        fileId: { type: 'string' },
        email: { type: 'string', description: 'E-mail da pessoa pra compartilhar diretamente.' },
        role: { type: 'string', description: '"reader" (só vê), "commenter" ou "writer" (edita). Padrão "reader".' },
        anyoneWithLink: { type: 'boolean', description: 'Se true, cria um link acessível a qualquer pessoa que o tiver (sem precisar estar logada).' },
        notify: { type: 'boolean', description: 'Se true (padrão), avisa a pessoa por e-mail ao compartilhar diretamente.' },
      },
      required: ['fileId'],
    },
    async run(args) {
      if (!args.email && !args.anyoneWithLink) {
        throw new Error('Informe "email" (compartilhar com uma pessoa) e/ou "anyoneWithLink": true (link público) — pelo menos um dos dois.');
      }
      const { token } = await resolveToken(args.account);
      const role = args.role || 'reader';
      const result = {};
      if (args.email) {
        const notify = args.notify !== false;
        const perm = await gfetch(
          token,
          `${DRIVE}/files/${encodeURIComponent(args.fileId)}/permissions?sendNotificationEmail=${notify}&supportsAllDrives=true`,
          { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ role, type: 'user', emailAddress: args.email }) },
        );
        result.sharedWith = { email: args.email, role, permissionId: perm.id };
      }
      if (args.anyoneWithLink) {
        await gfetch(
          token,
          `${DRIVE}/files/${encodeURIComponent(args.fileId)}/permissions?supportsAllDrives=true`,
          { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ role: 'reader', type: 'anyone' }) },
        );
      }
      const meta = await gfetch(token, `${DRIVE}/files/${encodeURIComponent(args.fileId)}?fields=webViewLink&supportsAllDrives=true`);
      result.link = meta.webViewLink;
      return result;
    },
  },

  drive_copy_file: {
    description: 'Duplica um arquivo no Google Drive, opcionalmente com novo nome e/ou pasta de destino. Ação que escreve — confirme com o usuário antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        fileId: { type: 'string' },
        name: { type: 'string', description: 'Nome da cópia (padrão: Drive gera "Cópia de <original>").' },
        parentFolderId: { type: 'string', description: 'ID da pasta de destino (opcional).' },
      },
      required: ['fileId'],
    },
    async run(args) {
      const { token } = await resolveToken(args.account);
      const body = {};
      if (args.name) body.name = args.name;
      if (args.parentFolderId) body.parents = [args.parentFolderId];
      const file = await gfetch(
        token,
        `${DRIVE}/files/${encodeURIComponent(args.fileId)}/copy?supportsAllDrives=true&fields=${encodeURIComponent('id,name,webViewLink')}`,
        { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) },
      );
      return { id: file.id, name: file.name, webViewLink: file.webViewLink };
    },
  },

  calendar_list_calendars: {
    description: 'Lista todas as agendas (calendários) que essa conta Google tem acesso — não só a principal. Use pra descobrir o calendarId de uma agenda específica antes de listar/criar/atualizar eventos nela.',
    inputSchema: { type: 'object', properties: { account: { type: 'string' } } },
    async run(args) {
      const { token } = await resolveToken(args.account);
      const data = await gfetch(token, `${CALENDAR}/users/me/calendarList`);
      const cals = (data.items || []).map((c) => ({ id: c.id, summary: c.summary, primary: !!c.primary, accessRole: c.accessRole }));
      return cals.length ? cals : 'Nenhuma agenda encontrada.';
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
        recurrence: {
          type: 'array',
          items: { type: 'string' },
          description: 'Regra(s) de recorrência no formato RRULE (RFC 5545), ex.: ["RRULE:FREQ=WEEKLY;BYDAY=MO"] pra toda segunda-feira, ["RRULE:FREQ=DAILY;COUNT=5"] pra 5 dias seguidos. Omita pra um evento único.',
        },
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
        ...(args.recurrence?.length ? { recurrence: args.recurrence } : {}),
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

  calendar_update_event: {
    description: 'Atualiza um evento existente na agenda (remarca horário, muda título/descrição/convidados). Informe só os campos que quer mudar — o resto do evento fica como está. Ação que escreve — confirme com o usuário antes.',
    inputSchema: {
      type: 'object',
      properties: {
        account: { type: 'string' },
        eventId: { type: 'string', description: 'ID do evento (ver calendar_list_events).' },
        calendarId: { type: 'string', description: 'Padrão "primary".' },
        summary: { type: 'string' },
        description: { type: 'string' },
        start: { type: 'string', description: 'ISO8601 do novo horário de início.' },
        end: { type: 'string', description: 'ISO8601 do novo horário de fim.' },
        attendees: { type: 'array', items: { type: 'string' }, description: 'Substitui a lista de convidados por essa (e-mails).' },
        recurrence: {
          type: 'array',
          items: { type: 'string' },
          description: 'Substitui a regra de recorrência (RRULE, RFC 5545). Envie um array vazio [] pra tornar o evento único de novo.',
        },
      },
      required: ['eventId'],
    },
    async run(args) {
      const body = {};
      if (args.summary !== undefined) body.summary = args.summary;
      if (args.description !== undefined) body.description = args.description;
      if (args.start !== undefined) body.start = { dateTime: args.start };
      if (args.end !== undefined) body.end = { dateTime: args.end };
      if (args.attendees !== undefined) body.attendees = args.attendees.map((email) => ({ email }));
      if (args.recurrence !== undefined) body.recurrence = args.recurrence;
      if (!Object.keys(body).length) {
        throw new Error('Informe ao menos um campo pra atualizar (summary, description, start, end, attendees, recurrence).');
      }
      const { token } = await resolveToken(args.account);
      const cal = encodeURIComponent(args.calendarId || 'primary');
      const params = new URLSearchParams();
      if (args.attendees !== undefined) params.set('sendUpdates', 'all');
      const qs = params.toString();
      const ev = await gfetch(
        token,
        `${CALENDAR}/calendars/${cal}/events/${encodeURIComponent(args.eventId)}${qs ? `?${qs}` : ''}`,
        { method: 'PATCH', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) },
      );
      return { id: ev.id, summary: ev.summary, start: ev.start, end: ev.end, htmlLink: ev.htmlLink };
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
