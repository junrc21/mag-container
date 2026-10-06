#!/usr/bin/env node
// MAG Trello MCP server (stdio, zero-dependency).
//
// Exposes curated Trello tools to Hermes. Credentials stay centralized in the
// MAG control plane: every call fetches the tenant token from MAG and calls
// Trello's API with the platform key + tenant token as query params.
//
// Required env (via mcp_servers.trello.env): MAG_API_URL, MAG_INTERNAL_KEY,
// MAG_TENANT_ID, TRELLO_API_KEY.

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-trello';
const SERVER_VERSION = '0.1.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_INTERNAL_KEY = process.env.MAG_INTERNAL_KEY || '';
const MAG_TENANT_ID = process.env.MAG_TENANT_ID || '';
const TRELLO_API_KEY = process.env.TRELLO_API_KEY || '';
const TRELLO_API = 'https://api.trello.com/1';
const MAX_TEXT = 12000;

function log(...a) { process.stderr.write(`[mag-trello] ${a.join(' ')}\n`); }
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
function csv(value) {
  return Array.isArray(value) ? value.filter(Boolean).join(',') : value;
}
function cardBrief(card) {
  return {
    id: card.id,
    name: card.name,
    closed: card.closed,
    due: card.due,
    dueComplete: card.dueComplete,
    listId: card.idList,
    boardId: card.idBoard,
    url: card.shortUrl || card.url,
    labels: (card.labels || []).map((l) => l.name || l.color).filter(Boolean),
    members: (card.members || []).map((m) => m.fullName || m.username).filter(Boolean),
  };
}

async function getToken() {
  if (!MAG_API_URL || !MAG_INTERNAL_KEY || !MAG_TENANT_ID) {
    throw new Error('MCP não configurado (MAG_API_URL/MAG_INTERNAL_KEY/MAG_TENANT_ID ausentes).');
  }
  if (!TRELLO_API_KEY) {
    throw new Error('Trello não está configurado no runtime (TRELLO_API_KEY ausente).');
  }
  const res = await fetch(
    `${MAG_API_URL}/internal/connectors/by-provider/trello/token?tenantId=${encodeURIComponent(MAG_TENANT_ID)}`,
    { headers: { 'x-internal-key': MAG_INTERNAL_KEY } },
  );
  if (!res.ok) throw new Error(`MAG token ${res.status}: ${(await res.text()).slice(0, 160)}`);
  const body = await res.json();
  if (!body.accessToken) throw new Error('Trello não está conectado. Conecte em Fontes → Integrações → Trello.');
  return body.accessToken;
}

async function trello(path, { method = 'GET', query, body } = {}) {
  const token = await getToken();
  const url = new URL(path.startsWith('http') ? path : `${TRELLO_API}${path}`);
  url.searchParams.set('key', TRELLO_API_KEY);
  url.searchParams.set('token', token);
  for (const [key, value] of Object.entries(query || {})) {
    if (value === undefined || value === null || value === '') continue;
    url.searchParams.set(key, String(value));
  }
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { 'Content-Type': 'application/json' } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data;
  try { data = text ? JSON.parse(text) : {}; } catch { data = text; }
  if (!res.ok) {
    const errorText = typeof data === 'string' ? data : JSON.stringify(data);
    throw new Error(`Trello API ${res.status}: ${errorText.slice(0, 240)}`);
  }
  return data;
}

async function resolveMemberId(boardId, nameOrUsername) {
  const q = String(nameOrUsername || '').trim().toLowerCase();
  if (!q) return null;
  const members = await trello(`/boards/${encodeURIComponent(boardId)}/members`, {
    query: { fields: 'id,username,fullName' },
  });
  const found =
    members.find((m) => (m.id || '').toLowerCase() === q) ||
    members.find((m) => (m.username || '').toLowerCase() === q) ||
    members.find((m) => (m.fullName || '').toLowerCase() === q) ||
    members.find((m) => (m.fullName || '').toLowerCase().includes(q));
  if (!found) {
    throw new Error(`Membro "${nameOrUsername}" não encontrado neste board. Use trello_list_members para ver quem está disponível.`);
  }
  return found.id;
}

async function cardBoardId(cardId) {
  const card = await trello(`/cards/${encodeURIComponent(cardId)}`, { query: { fields: 'idBoard' } });
  return card.idBoard;
}

const tools = {
  trello_me: {
    description: 'Mostra o usuário Trello conectado e informações básicas da conta.',
    inputSchema: { type: 'object', properties: {} },
    async run() {
      return trello('/members/me', { query: { fields: 'id,username,fullName,url' } });
    },
  },

  trello_check_updates: {
    description: 'Consome eventos recentes do webhook do Trello desde a última checagem da MAG. Use no começo de uma nova sessão quando o SOUL pedir.',
    inputSchema: { type: 'object', properties: {} },
    async run() {
      if (!MAG_API_URL || !MAG_INTERNAL_KEY || !MAG_TENANT_ID) {
        throw new Error('MCP não configurado (MAG_API_URL/MAG_INTERNAL_KEY/MAG_TENANT_ID ausentes).');
      }
      const res = await fetch(
        `${MAG_API_URL}/internal/connectors/trello/pending-events?tenantId=${encodeURIComponent(MAG_TENANT_ID)}`,
        { headers: { 'x-internal-key': MAG_INTERNAL_KEY } },
      );
      if (!res.ok) throw new Error(`MAG Trello events ${res.status}: ${(await res.text()).slice(0, 160)}`);
      const body = await res.json();
      const events = body.events || [];
      return events.length ? events : 'Nenhuma novidade pendente no Trello.';
    },
  },

  trello_list_boards: {
    description: 'Lista boards visíveis para a conta Trello conectada.',
    inputSchema: {
      type: 'object',
      properties: {
        includeClosed: { type: 'boolean', description: 'Incluir boards fechados/arquivados. Padrão: false.' },
        limit: { type: 'number', description: 'Máx. (padrão 50).' },
      },
    },
    async run(args) {
      const boards = await trello('/members/me/boards', {
        query: {
          fields: 'id,name,url,closed,dateLastActivity',
          filter: args.includeClosed ? 'all' : 'open',
        },
      });
      return boards.slice(0, Math.min(args.limit || 50, 100)).map((b) => ({
        id: b.id,
        name: b.name,
        closed: b.closed,
        url: b.url,
        dateLastActivity: b.dateLastActivity,
      }));
    },
  },

  trello_list_lists: {
    description: 'Lista listas de um board Trello. Use o id da lista para criar/mover cards.',
    inputSchema: {
      type: 'object',
      properties: { boardId: { type: 'string' }, includeClosed: { type: 'boolean' } },
      required: ['boardId'],
    },
    async run(args) {
      const lists = await trello(`/boards/${encodeURIComponent(args.boardId)}/lists`, {
        query: { fields: 'id,name,closed,pos', filter: args.includeClosed ? 'all' : 'open' },
      });
      return lists.map((l) => ({ id: l.id, name: l.name, closed: l.closed, pos: l.pos }));
    },
  },

  trello_list_members: {
    description: 'Lista membros de um board Trello (nome, username, id) para atribuir cards.',
    inputSchema: {
      type: 'object',
      properties: { boardId: { type: 'string' } },
      required: ['boardId'],
    },
    async run(args) {
      const members = await trello(`/boards/${encodeURIComponent(args.boardId)}/members`, {
        query: { fields: 'id,username,fullName,url' },
      });
      return members.map((m) => ({ id: m.id, username: m.username, fullName: m.fullName, url: m.url }));
    },
  },

  trello_list_cards: {
    description: 'Lista cards de uma lista ou board Trello. Use listId para uma lista específica; use boardId para o board inteiro.',
    inputSchema: {
      type: 'object',
      properties: {
        listId: { type: 'string' },
        boardId: { type: 'string' },
        includeClosed: { type: 'boolean', description: 'Incluir cards arquivados. Padrão: false.' },
        limit: { type: 'number', description: 'Máx. (padrão 30).' },
      },
    },
    async run(args) {
      if (!args.listId && !args.boardId) throw new Error('Informe listId ou boardId.');
      const n = Math.min(args.limit || 30, 100);
      const path = args.listId
        ? `/lists/${encodeURIComponent(args.listId)}/cards`
        : `/boards/${encodeURIComponent(args.boardId)}/cards`;
      const cards = await trello(path, {
        query: {
          fields: 'id,name,closed,due,dueComplete,idList,idBoard,shortUrl,url,dateLastActivity',
          members: true,
          member_fields: 'id,username,fullName',
          labels: true,
          filter: args.includeClosed ? 'all' : 'open',
        },
      });
      return cards.slice(0, n).map(cardBrief);
    },
  },

  trello_search_cards: {
    description: 'Busca cards no Trello por texto.',
    inputSchema: {
      type: 'object',
      properties: {
        query: { type: 'string' },
        boardId: { type: 'string', description: 'Opcional — restringe a um board.' },
        limit: { type: 'number', description: 'Padrão 10, máximo 30.' },
      },
      required: ['query'],
    },
    async run(args) {
      const n = Math.min(args.limit || 10, 30);
      const data = await trello('/search', {
        query: compact({
          query: args.query,
          idBoards: args.boardId,
          modelTypes: 'cards',
          cards_limit: n,
          card_fields: 'id,name,closed,due,dueComplete,idList,idBoard,shortUrl,url',
        }),
      });
      return (data.cards || []).map(cardBrief);
    },
  },

  trello_get_card: {
    description: 'Detalhes de um card Trello, incluindo descrição, checklist resumida, comentários recentes e responsáveis.',
    inputSchema: {
      type: 'object',
      properties: { cardId: { type: 'string' } },
      required: ['cardId'],
    },
    async run(args) {
      const card = await trello(`/cards/${encodeURIComponent(args.cardId)}`, {
        query: {
          fields: 'id,name,desc,closed,due,dueComplete,idList,idBoard,shortUrl,url,dateLastActivity',
          members: true,
          member_fields: 'id,username,fullName',
          labels: true,
          checklists: 'all',
          checklist_fields: 'name',
          checkItem_fields: 'name,state',
        },
      });
      const actions = await trello(`/cards/${encodeURIComponent(args.cardId)}/actions`, {
        query: { filter: 'commentCard', limit: 10, fields: 'data,date', memberCreator_fields: 'username,fullName' },
      }).catch(() => []);
      return {
        ...cardBrief(card),
        description: truncate(card.desc || ''),
        checklists: (card.checklists || []).map((c) => ({
          name: c.name,
          items: (c.checkItems || []).map((i) => ({ name: i.name, state: i.state })),
        })),
        comments: actions.map((a) => ({
          author: a.memberCreator?.fullName || a.memberCreator?.username,
          text: a.data?.text,
          at: a.date,
        })),
      };
    },
  },

  trello_create_card: {
    description: 'Cria um card no Trello. Ação que escreve — confirme antes com o usuário.',
    inputSchema: {
      type: 'object',
      properties: {
        listId: { type: 'string' },
        name: { type: 'string' },
        description: { type: 'string' },
        dueDate: { type: 'string', description: 'Data ISO ou YYYY-MM-DD.' },
        member: { type: 'string', description: 'Nome/username/id de membro do board para atribuir.' },
        position: { type: 'string', description: 'top, bottom ou número. Padrão bottom.' },
      },
      required: ['listId', 'name'],
    },
    async run(args) {
      const query = compact({
        idList: args.listId,
        name: args.name,
        desc: args.description,
        due: args.dueDate,
        pos: args.position || 'bottom',
      });
      if (args.member) {
        const list = await trello(`/lists/${encodeURIComponent(args.listId)}`, { query: { fields: 'idBoard' } });
        query.idMembers = await resolveMemberId(list.idBoard, args.member);
      }
      const card = await trello('/cards', { method: 'POST', query });
      return `Card criado: ${card.name} (${card.id}) — ${card.shortUrl || card.url}`;
    },
  },

  trello_update_card: {
    description: 'Atualiza um card Trello (nome, descrição, vencimento, arquivar/desarquivar, responsável). Ação que escreve.',
    inputSchema: {
      type: 'object',
      properties: {
        cardId: { type: 'string' },
        name: { type: 'string' },
        description: { type: 'string' },
        dueDate: { type: 'string', description: 'Data ISO/YYYY-MM-DD, ou null para limpar.' },
        closed: { type: 'boolean', description: 'true arquiva, false desarquiva.' },
        member: { type: 'string', description: 'Nome/username/id para adicionar ao card.' },
      },
      required: ['cardId'],
    },
    async run(args) {
      const query = {};
      if (args.name) query.name = args.name;
      if (args.description !== undefined) query.desc = args.description;
      if (args.dueDate !== undefined) query.due = args.dueDate || 'null';
      if (typeof args.closed === 'boolean') query.closed = args.closed;
      if (Object.keys(query).length === 0 && !args.member) throw new Error('Nada para atualizar.');
      let card = Object.keys(query).length
        ? await trello(`/cards/${encodeURIComponent(args.cardId)}`, { method: 'PUT', query })
        : await trello(`/cards/${encodeURIComponent(args.cardId)}`, { query: { fields: 'id,name,idBoard' } });
      if (args.member) {
        const memberId = await resolveMemberId(card.idBoard || await cardBoardId(args.cardId), args.member);
        await trello(`/cards/${encodeURIComponent(args.cardId)}/idMembers`, { method: 'POST', query: { value: memberId } });
        card = await trello(`/cards/${encodeURIComponent(args.cardId)}`, { query: { fields: 'id,name,shortUrl,url' } });
      }
      return `Card atualizado: ${card.name} (${card.id}) — ${card.shortUrl || card.url || ''}`.trim();
    },
  },

  trello_move_card: {
    description: 'Move um card Trello para outra lista. Ação que escreve.',
    inputSchema: {
      type: 'object',
      properties: {
        cardId: { type: 'string' },
        listId: { type: 'string' },
        position: { type: 'string', description: 'top, bottom ou número. Opcional.' },
      },
      required: ['cardId', 'listId'],
    },
    async run(args) {
      const card = await trello(`/cards/${encodeURIComponent(args.cardId)}`, {
        method: 'PUT',
        query: compact({ idList: args.listId, pos: args.position }),
      });
      return `Card movido: ${card.name} — ${card.shortUrl || card.url}`;
    },
  },

  trello_comment_card: {
    description: 'Adiciona um comentário a um card Trello. Ação que escreve.',
    inputSchema: {
      type: 'object',
      properties: {
        cardId: { type: 'string' },
        text: { type: 'string' },
      },
      required: ['cardId', 'text'],
    },
    async run(args) {
      await trello(`/cards/${encodeURIComponent(args.cardId)}/actions/comments`, {
        method: 'POST',
        query: { text: args.text },
      });
      return 'Comentário adicionado ao card.';
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
