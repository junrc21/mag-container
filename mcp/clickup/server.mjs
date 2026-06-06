#!/usr/bin/env node
// MAG ClickUp MCP server (stdio, zero-dependency).
//
// Gives the Hermes agent ClickUp tools (workspaces/spaces/lists, list/get/create/
// update tasks + comment). Stores no credentials: per call it fetches the tenant's
// ClickUp OAuth token from the MAG control plane (authorized in Fontes) and calls
// ClickUp's v2 API.
//
// Required env (via mcp_servers.clickup.env): MAG_API_URL, MAG_INTERNAL_KEY, MAG_TENANT_ID.

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-clickup';
const SERVER_VERSION = '0.1.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';
const CU = 'https://api.clickup.com/api/v2';
const MAX_TEXT = 12000;

function log(...a) { process.stderr.write(`[mag-clickup] ${a.join(' ')}\n`); }
function send(m) { process.stdout.write(JSON.stringify(m) + '\n'); }
function reply(id, result) { send({ jsonrpc: '2.0', id, result }); }
function replyError(id, code, message) { send({ jsonrpc: '2.0', id, error: { code, message } }); }
function truncate(s) {
  if (typeof s !== 'string') s = JSON.stringify(s, null, 2);
  return s.length > MAX_TEXT ? s.slice(0, MAX_TEXT) + '\n…[truncado]' : s;
}

// ── token + ClickUp API ─────────────────────────────────────────────────────
async function getToken() {
  if (!MAG_API_URL || !MAG_INTERNAL_KEY || !MAG_TENANT_ID) {
    throw new Error('MCP não configurado (MAG_API_URL/MAG_INTERNAL_KEY/MAG_TENANT_ID ausentes).');
  }
  const res = await fetch(
    `${MAG_API_URL}/internal/connectors/by-provider/clickup/token?tenantId=${encodeURIComponent(MAG_TENANT_ID)}`,
    { headers: { 'x-internal-key': MAG_INTERNAL_KEY } },
  );
  if (!res.ok) throw new Error(`MAG token ${res.status}: ${(await res.text()).slice(0, 160)}`);
  const body = await res.json();
  if (!body.accessToken) throw new Error('ClickUp não está conectado. Conecte em Fontes → Integrações → ClickUp.');
  return body.accessToken;
}

async function cu(path, { method = 'GET', body } = {}) {
  const token = await getToken();
  const res = await fetch(`${CU}${path}`, {
    method,
    // ClickUp uses the raw token in Authorization (no "Bearer").
    headers: { Authorization: token, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = text; }
  if (!res.ok) {
    const msg = data?.err || (typeof data === 'string' ? data : JSON.stringify(data));
    throw new Error(`ClickUp API ${res.status}: ${String(msg).slice(0, 200)}`);
  }
  return data;
}

let _ctx = null; // { userId, teamId, teamName }
async function ctx() {
  if (_ctx) return _ctx;
  const u = await cu('/user');
  const t = await cu('/team');
  const team = (t.teams || [])[0];
  if (!team) throw new Error('Nenhum workspace ClickUp encontrado.');
  _ctx = { userId: u.user?.id, userName: u.user?.username, teamId: team.id, teamName: team.name };
  return _ctx;
}

function taskBrief(t) {
  return {
    id: t.id,
    name: t.name,
    status: t.status?.status,
    list: t.list?.name,
    assignees: (t.assignees || []).map((a) => a.username),
    url: t.url,
  };
}

// ── tools ───────────────────────────────────────────────────────────────────
const tools = {
  clickup_me: {
    description: 'Mostra o usuário ClickUp conectado e os workspaces (teams) disponíveis.',
    inputSchema: { type: 'object', properties: {} },
    async run() {
      const u = await cu('/user');
      const t = await cu('/team');
      return {
        user: { id: u.user?.id, username: u.user?.username, email: u.user?.email },
        workspaces: (t.teams || []).map((w) => ({ id: w.id, name: w.name })),
      };
    },
  },

  clickup_list_tasks: {
    description: 'Lista tarefas do ClickUp. Sem listId, traz as MINHAS tarefas (atribuídas a mim) no workspace. Com listId, traz as tarefas daquela lista.',
    inputSchema: {
      type: 'object',
      properties: {
        listId: { type: 'string', description: 'Opcional — id da lista.' },
        limit: { type: 'number', description: 'Máx. (padrão 25).' },
      },
    },
    async run(args) {
      const n = Math.min(args.limit || 25, 100);
      let tasks;
      if (args.listId) {
        const d = await cu(`/list/${encodeURIComponent(args.listId)}/task?subtasks=true&include_closed=false`);
        tasks = d.tasks || [];
      } else {
        const c = await ctx();
        const d = await cu(`/team/${c.teamId}/task?assignees[]=${c.userId}&subtasks=true&include_closed=false`);
        tasks = d.tasks || [];
      }
      const out = tasks.slice(0, n).map(taskBrief);
      return out.length ? out : 'Nenhuma tarefa encontrada.';
    },
  },

  clickup_list_spaces: {
    description: 'Lista os spaces do workspace ClickUp (para navegar até listas).',
    inputSchema: { type: 'object', properties: { teamId: { type: 'string' } } },
    async run(args) {
      const teamId = args.teamId || (await ctx()).teamId;
      const d = await cu(`/team/${encodeURIComponent(teamId)}/space?archived=false`);
      return (d.spaces || []).map((s) => ({ id: s.id, name: s.name }));
    },
  },

  clickup_list_lists: {
    description: 'Lista as listas de um space (inclui as de dentro de pastas). Use o id de uma lista para criar tarefas.',
    inputSchema: {
      type: 'object',
      properties: { spaceId: { type: 'string' } },
      required: ['spaceId'],
    },
    async run(args) {
      const sid = encodeURIComponent(args.spaceId);
      const folderless = await cu(`/space/${sid}/list?archived=false`);
      const out = (folderless.lists || []).map((l) => ({ id: l.id, name: l.name, folder: null }));
      const folders = await cu(`/space/${sid}/folder?archived=false`);
      for (const f of folders.folders || []) {
        for (const l of f.lists || []) out.push({ id: l.id, name: l.name, folder: f.name });
      }
      return out.length ? out : 'Nenhuma lista neste space.';
    },
  },

  clickup_get_task: {
    description: 'Detalhes de uma tarefa do ClickUp (descrição, status, responsáveis) + comentários.',
    inputSchema: {
      type: 'object',
      properties: { taskId: { type: 'string' } },
      required: ['taskId'],
    },
    async run(args) {
      const t = await cu(`/task/${encodeURIComponent(args.taskId)}`);
      let comments = [];
      try {
        const c = await cu(`/task/${encodeURIComponent(args.taskId)}/comment`);
        comments = (c.comments || []).map((x) => ({ author: x.user?.username, text: x.comment_text, at: x.date }));
      } catch { /* ignore */ }
      return {
        id: t.id,
        name: t.name,
        status: t.status?.status,
        priority: t.priority?.priority || null,
        assignees: (t.assignees || []).map((a) => a.username),
        url: t.url,
        description: truncate(t.description || t.text_content || ''),
        comments,
      };
    },
  },

  clickup_create_task: {
    description: 'Cria uma tarefa numa lista do ClickUp. Precisa do listId (use clickup_list_spaces/clickup_list_lists para achar). Ação que escreve — confirme antes.',
    inputSchema: {
      type: 'object',
      properties: {
        listId: { type: 'string' },
        name: { type: 'string' },
        description: { type: 'string' },
        status: { type: 'string', description: 'Nome do status (opcional).' },
        priority: { type: 'number', description: '1=urgente,2=alta,3=normal,4=baixa.' },
        assignToMe: { type: 'boolean' },
      },
      required: ['listId', 'name'],
    },
    async run(args) {
      const body = { name: args.name };
      if (args.description) body.description = args.description;
      if (args.status) body.status = args.status;
      if (typeof args.priority === 'number') body.priority = args.priority;
      if (args.assignToMe) body.assignees = [(await ctx()).userId];
      const t = await cu(`/list/${encodeURIComponent(args.listId)}/task`, { method: 'POST', body });
      return `Tarefa criada: ${t.name} (${t.id}) — ${t.url}`;
    },
  },

  clickup_update_task: {
    description: 'Atualiza uma tarefa do ClickUp (status, nome, descrição, prioridade). Ação que escreve.',
    inputSchema: {
      type: 'object',
      properties: {
        taskId: { type: 'string' },
        status: { type: 'string', description: 'Nome do status destino (ex.: "complete", "in progress").' },
        name: { type: 'string' },
        description: { type: 'string' },
        priority: { type: 'number' },
      },
      required: ['taskId'],
    },
    async run(args) {
      const body = {};
      if (args.status) body.status = args.status;
      if (args.name) body.name = args.name;
      if (args.description) body.description = args.description;
      if (typeof args.priority === 'number') body.priority = args.priority;
      if (Object.keys(body).length === 0) throw new Error('Nada para atualizar.');
      const t = await cu(`/task/${encodeURIComponent(args.taskId)}`, { method: 'PUT', body });
      return `Tarefa ${t.id} atualizada (status: ${t.status?.status}).`;
    },
  },

  clickup_comment: {
    description: 'Adiciona um comentário a uma tarefa do ClickUp. Ação que escreve.',
    inputSchema: {
      type: 'object',
      properties: { taskId: { type: 'string' }, comment: { type: 'string' } },
      required: ['taskId', 'comment'],
    },
    async run(args) {
      await cu(`/task/${encodeURIComponent(args.taskId)}/comment`, {
        method: 'POST',
        body: { comment_text: args.comment, notify_all: false },
      });
      return 'Comentário adicionado.';
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
  try { msg = JSON.parse(t); } catch { return; }
  handleMessage(msg).catch((e) => log('handler error:', e.message));
});

log(`started (api=${MAG_API_URL || 'unset'} tenant=${MAG_TENANT_ID || 'unset'})`);
