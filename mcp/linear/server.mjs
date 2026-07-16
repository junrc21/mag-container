#!/usr/bin/env node
// MAG Linear MCP server (stdio, zero-dependency).
//
// Gives the Hermes agent Linear tools (list/search/get/create/update issues +
// comment). It stores no credentials: on every call it fetches the tenant's
// Linear OAuth token from the MAG control plane (the one the user authorized in
// Fontes) and calls Linear's GraphQL API.
//
// Required env (injected via mcp_servers.linear.env): MAG_API_URL,
// MAG_INTERNAL_KEY, MAG_TENANT_ID.

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-linear';
const SERVER_VERSION = '0.2.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';
const LINEAR_GQL = 'https://api.linear.app/graphql';
const MAX_TEXT = 12000;

function log(...a) {
  process.stderr.write(`[mag-linear] ${a.join(' ')}\n`);
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

// ── token + GraphQL ─────────────────────────────────────────────────────────
async function getToken() {
  if (!MAG_API_URL || !MAG_INTERNAL_KEY || !MAG_TENANT_ID) {
    throw new Error('MCP não configurado (MAG_API_URL/MAG_INTERNAL_KEY/MAG_TENANT_ID ausentes).');
  }
  const res = await fetch(
    `${MAG_API_URL}/internal/connectors/by-provider/linear/token?tenantId=${encodeURIComponent(MAG_TENANT_ID)}`,
    { headers: { 'x-internal-key': MAG_INTERNAL_KEY } },
  );
  if (!res.ok) throw new Error(`MAG token ${res.status}: ${(await res.text()).slice(0, 160)}`);
  const body = await res.json();
  if (!body.accessToken) throw new Error('Linear não está conectado. Conecte a conta em Fontes → Integrações → Linear.');
  return body.accessToken;
}

async function gql(query, variables = {}) {
  const token = await getToken();
  const res = await fetch(LINEAR_GQL, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, variables }),
  });
  const body = await res.json().catch(() => ({}));
  if (body.errors) throw new Error(`Linear: ${JSON.stringify(body.errors).slice(0, 240)}`);
  if (!res.ok) throw new Error(`Linear API ${res.status}`);
  return body.data;
}

const isUuid = (s) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s || '');
const ISSUE_FIELDS = 'id identifier title priority state { name } assignee { name } url updatedAt';

async function resolveIssueId(idOrIdentifier) {
  if (isUuid(idOrIdentifier)) return idOrIdentifier;
  // identifier like CX-289 → resolve by team key + number (issueSearch is deprecated)
  const m = /^([A-Za-z][A-Za-z0-9]*)-(\d+)$/.exec((idOrIdentifier || '').trim());
  if (!m) throw new Error(`Identificador inválido: "${idOrIdentifier}". Use TIME-NÚMERO (ex.: CX-290) ou o id.`);
  const team = m[1].toUpperCase();
  const num = Number(m[2]);
  const d = await gql(
    'query($num:Float!,$team:String!){ issues(filter:{ number:{ eq:$num }, team:{ key:{ eq:$team } } }, first:1){ nodes { id identifier } } }',
    { num, team },
  );
  const node = d?.issues?.nodes?.[0];
  if (!node) throw new Error(`Issue "${idOrIdentifier}" não encontrada.`);
  return node.id;
}

async function resolveUserId(nameOrEmail) {
  const d = await gql('{ users(first:100){ nodes { id name email active } } }');
  const users = (d.users?.nodes || []).filter((u) => u.active);
  const q = String(nameOrEmail).toLowerCase();
  const found =
    users.find((u) => (u.email || '').toLowerCase() === q) ||
    users.find((u) => (u.name || '').toLowerCase() === q) ||
    users.find((u) => (u.name || '').toLowerCase().includes(q));
  if (!found) {
    throw new Error(`Pessoa "${nameOrEmail}" não encontrada no Linear. Use linear_list_members pra ver quem está disponível.`);
  }
  return found.id;
}

// ── tools ───────────────────────────────────────────────────────────────────
const tools = {
  linear_me: {
    description: 'Mostra o usuário Linear conectado, a organização e os times disponíveis (para saber teamKey ao criar issues).',
    inputSchema: { type: 'object', properties: {} },
    async run() {
      const d = await gql('{ viewer { name email } organization { name urlKey } teams(first:30){ nodes { key name } } }');
      return {
        viewer: d.viewer,
        organization: d.organization,
        teams: (d.teams?.nodes || []).map((t) => ({ key: t.key, name: t.name })),
      };
    },
  },

  linear_list_issues: {
    description: 'Lista issues do Linear. filter="assigned" (minhas, padrão), "created" (criadas por mim) ou "all" (recentes do workspace).',
    inputSchema: {
      type: 'object',
      properties: {
        filter: { type: 'string', enum: ['assigned', 'created', 'all'], description: 'Padrão: assigned.' },
        limit: { type: 'number', description: 'Máx. 50 (padrão 15).' },
      },
    },
    async run(args) {
      const n = Math.min(args.limit || 15, 50);
      const filter = args.filter || 'assigned';
      let d, nodes;
      if (filter === 'all') {
        d = await gql(`query($n:Int!){ issues(first:$n, orderBy:updatedAt){ nodes { ${ISSUE_FIELDS} } } }`, { n });
        nodes = d.issues?.nodes;
      } else {
        const field = filter === 'created' ? 'createdIssues' : 'assignedIssues';
        d = await gql(`query($n:Int!){ viewer { ${field}(first:$n, orderBy:updatedAt){ nodes { ${ISSUE_FIELDS} } } } }`, { n });
        nodes = d.viewer?.[field]?.nodes;
      }
      const out = (nodes || []).map((i) => ({
        id: i.id,
        identifier: i.identifier,
        title: i.title,
        state: i.state?.name,
        assignee: i.assignee?.name || null,
        url: i.url,
      }));
      return out.length ? out : 'Nenhuma issue encontrada.';
    },
  },

  linear_list_members: {
    description: 'Lista os membros ativos do workspace Linear (nome, e-mail, id) — use pra saber pra quem atribuir uma issue (linear_create_issue/linear_update_issue).',
    inputSchema: { type: 'object', properties: {} },
    async run() {
      const d = await gql('{ users(first:100){ nodes { id name email active } } }');
      const users = (d.users?.nodes || []).filter((u) => u.active);
      return users.length ? users.map((u) => ({ id: u.id, name: u.name, email: u.email })) : 'Nenhum membro encontrado.';
    },
  },

  linear_search_issues: {
    description: 'Busca issues do Linear por texto (título/identificador, ex.: "RBAC" ou "CX-289").',
    inputSchema: {
      type: 'object',
      properties: { query: { type: 'string' }, limit: { type: 'number', description: 'Padrão 10.' } },
      required: ['query'],
    },
    async run(args) {
      const n = Math.min(args.limit || 10, 50);
      const d = await gql(`query($q:String!,$n:Int!){ searchIssues(term:$q, first:$n){ nodes { ${ISSUE_FIELDS} } } }`, {
        q: args.query,
        n,
      });
      const out = (d.searchIssues?.nodes || []).map((i) => ({
        id: i.id,
        identifier: i.identifier,
        title: i.title,
        state: i.state?.name,
        url: i.url,
      }));
      return out.length ? out : 'Nenhuma issue encontrada.';
    },
  },

  linear_get_issue: {
    description: 'Detalhes de uma issue (por identificador tipo "CX-289" ou id), incluindo descrição e comentários.',
    inputSchema: {
      type: 'object',
      properties: { issue: { type: 'string', description: 'Identificador (CX-289) ou id.' } },
      required: ['issue'],
    },
    async run(args) {
      const id = await resolveIssueId(args.issue);
      const d = await gql(
        `query($id:String!){ issue(id:$id){ identifier title description priority state{name} assignee{name} url
           comments(first:15){ nodes { user{name} body createdAt } } } }`,
        { id },
      );
      const i = d.issue;
      if (!i) throw new Error('Issue não encontrada.');
      return {
        identifier: i.identifier,
        title: i.title,
        state: i.state?.name,
        assignee: i.assignee?.name || null,
        priority: i.priority,
        url: i.url,
        description: truncate(i.description || ''),
        comments: (i.comments?.nodes || []).map((c) => ({ author: c.user?.name, body: c.body, at: c.createdAt })),
      };
    },
  },

  linear_create_issue: {
    description: 'Cria uma issue no Linear. Ação que escreve — confirme com o usuário antes.',
    inputSchema: {
      type: 'object',
      properties: {
        title: { type: 'string' },
        description: { type: 'string' },
        teamKey: { type: 'string', description: 'Ex.: "CX". Se omitido e houver só um time, usa ele.' },
        priority: { type: 'number', description: '0=nenhuma,1=urgente,2=alta,3=média,4=baixa.' },
        assignToMe: { type: 'boolean' },
        assignee: { type: 'string', description: 'Nome ou e-mail de quem atribuir (ver linear_list_members). Alternativa a assignToMe.' },
        dueDate: { type: 'string', description: 'Data de vencimento, formato YYYY-MM-DD.' },
      },
      required: ['title'],
    },
    async run(args) {
      const d = await gql('{ viewer { id } teams(first:30){ nodes { id key name } } }');
      const teams = d.teams?.nodes || [];
      let team = teams[0];
      if (args.teamKey) {
        team = teams.find((t) => (t.key || '').toLowerCase() === args.teamKey.toLowerCase());
        if (!team) throw new Error(`Time "${args.teamKey}" não encontrado. Times: ${teams.map((t) => t.key).join(', ')}`);
      }
      if (!team) throw new Error('Nenhum time disponível no Linear.');
      const input = { title: args.title, teamId: team.id };
      if (args.description) input.description = args.description;
      if (typeof args.priority === 'number') input.priority = args.priority;
      if (args.dueDate) input.dueDate = args.dueDate;
      if (args.assignee) {
        input.assigneeId = await resolveUserId(args.assignee);
      } else if (args.assignToMe && d.viewer?.id) {
        input.assigneeId = d.viewer.id;
      }
      const r = await gql(
        'mutation($input:IssueCreateInput!){ issueCreate(input:$input){ success issue { identifier url } } }',
        { input },
      );
      if (!r.issueCreate?.success) throw new Error('Falha ao criar a issue.');
      return `Issue criada: ${r.issueCreate.issue.identifier} — ${r.issueCreate.issue.url}`;
    },
  },

  linear_update_issue: {
    description: 'Atualiza uma issue (mudar estado/título/descrição/prioridade/responsável/vencimento). Ex.: mover para "Done". Ação que escreve.',
    inputSchema: {
      type: 'object',
      properties: {
        issue: { type: 'string', description: 'Identificador (CX-289) ou id.' },
        stateName: { type: 'string', description: 'Nome do estado destino (ex.: "Done", "In Progress").' },
        title: { type: 'string' },
        description: { type: 'string' },
        priority: { type: 'number' },
        assignee: { type: 'string', description: 'Nome ou e-mail de quem atribuir (ver linear_list_members).' },
        dueDate: { type: 'string', description: 'Data de vencimento, formato YYYY-MM-DD.' },
      },
      required: ['issue'],
    },
    async run(args) {
      const id = await resolveIssueId(args.issue);
      const input = {};
      if (args.title) input.title = args.title;
      if (args.description) input.description = args.description;
      if (typeof args.priority === 'number') input.priority = args.priority;
      if (args.dueDate) input.dueDate = args.dueDate;
      if (args.assignee) input.assigneeId = await resolveUserId(args.assignee);
      if (args.stateName) {
        const d = await gql('query($id:String!){ issue(id:$id){ team { states(first:50){ nodes { id name } } } } }', { id });
        const states = d.issue?.team?.states?.nodes || [];
        const st = states.find((s) => (s.name || '').toLowerCase() === args.stateName.toLowerCase());
        if (!st) throw new Error(`Estado "${args.stateName}" não existe. Estados: ${states.map((s) => s.name).join(', ')}`);
        input.stateId = st.id;
      }
      if (Object.keys(input).length === 0) throw new Error('Nada para atualizar.');
      const r = await gql('mutation($id:String!,$input:IssueUpdateInput!){ issueUpdate(id:$id, input:$input){ success issue { identifier state{name} } } }', {
        id,
        input,
      });
      if (!r.issueUpdate?.success) throw new Error('Falha ao atualizar a issue.');
      const i = r.issueUpdate.issue;
      return `Issue ${i.identifier} atualizada (estado: ${i.state?.name}).`;
    },
  },

  linear_archive_issue: {
    description: 'Arquiva uma issue no Linear (reversível — não é uma exclusão permanente). Ação que escreve — confirme com o usuário antes.',
    inputSchema: {
      type: 'object',
      properties: { issue: { type: 'string', description: 'Identificador (CX-289) ou id.' } },
      required: ['issue'],
    },
    async run(args) {
      const id = await resolveIssueId(args.issue);
      const r = await gql('mutation($id:String!){ issueArchive(id:$id){ success } }', { id });
      if (!r.issueArchive?.success) throw new Error('Falha ao arquivar a issue.');
      return 'Issue arquivada.';
    },
  },

  linear_comment: {
    description: 'Adiciona um comentário a uma issue do Linear. Ação que escreve.',
    inputSchema: {
      type: 'object',
      properties: {
        issue: { type: 'string', description: 'Identificador (CX-289) ou id.' },
        body: { type: 'string', description: 'Texto do comentário (markdown).' },
      },
      required: ['issue', 'body'],
    },
    async run(args) {
      const id = await resolveIssueId(args.issue);
      const r = await gql('mutation($input:CommentCreateInput!){ commentCreate(input:$input){ success } }', {
        input: { issueId: id, body: args.body },
      });
      if (!r.commentCreate?.success) throw new Error('Falha ao comentar.');
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
  try {
    msg = JSON.parse(t);
  } catch {
    return;
  }
  handleMessage(msg).catch((e) => log('handler error:', e.message));
});

log(`started (api=${MAG_API_URL || 'unset'} tenant=${MAG_TENANT_ID || 'unset'})`);
