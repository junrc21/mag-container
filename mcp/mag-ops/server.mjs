#!/usr/bin/env node
// MAG Ops MCP server (stdio, zero-dependency).
//
// A plataforma inteira como ferramenta, para a MAG de Operação da CyriusX — o tenant de
// staff. É o que transforma "um bot que cospe alerta no Telegram" em alguém com quem dá
// para conversar: "quem está parado agora?", "o que houve com a padaria?", "reinicia a
// MAG dela".
//
// ## O que ela pode, e o que NÃO pode
//
// Ler tudo. Agir só no reversível: reiniciar um runtime, responder um chamado.
//
// Bloquear, desativar e excluir cliente NÃO são ferramentas daqui, de propósito. Isso
// passa pelo painel, com um humano confirmando — é o primitivo certo e já auditado, e
// evita a pior versão disto: um mal-entendido numa frase apagando um cliente.
//
// ## A chave
//
// MAG_OPS_KEY é derivada por HMAC do segredo mestre e injetada SÓ no .env deste
// container. Não basta um container declarar o MAG_TENANT_ID da staff — o servidor
// confere a chave. Sem isso, um container de cliente comprometido viraria god-mode sobre
// a plataforma inteira, e a peça de suporte seria a maior escalada de privilégio do
// sistema.
//
// Env obrigatório (via mcp_servers.mag-ops.env): MAG_API_URL, MAG_OPS_KEY.

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-ops';
const SERVER_VERSION = '0.1.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAG_API_URL = (process.env.MAG_API_URL || '').replace(/\/$/, '');
const MAG_OPS_KEY = process.env.MAG_OPS_KEY || '';
const MAX_TEXT = 12000;

function log(...a) {
  process.stderr.write(`[mag-ops] ${a.join(' ')}\n`);
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

async function callOps(path, options = {}) {
  if (!MAG_API_URL || !MAG_OPS_KEY) {
    // Mensagem específica de propósito: este MCP só existe no container da staff, então
    // "não configurado" aqui significa que o .env não foi re-sincronizado — não que a
    // pessoa esqueceu de conectar alguma coisa.
    throw new Error('Sem acesso de operação (MAG_API_URL/MAG_OPS_KEY ausentes no container).');
  }
  const res = await fetch(`${MAG_API_URL}${path}`, {
    ...options,
    headers: { 'x-ops-key': MAG_OPS_KEY, 'content-type': 'application/json', ...(options.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const message = typeof body.error === 'string' ? body.error : body?.error?.message || `MAG API ${res.status}`;
    throw new Error(message);
  }
  return body;
}

const tools = {
  platform_pulse: {
    description:
      'O estado da plataforma AGORA: quantos clientes existem, quantos estão no ar, quantos problemas críticos e ' +
      'de atenção estão abertos, quantos chamados esperam resposta e quantas exclusões estão agendadas. Use ' +
      'quando a equipe perguntar "como estamos?", "tá tudo bem?", "tem algo pegando fogo?".',
    inputSchema: { type: 'object', properties: {} },
    async run() {
      return callOps('/internal/ops/pulse');
    },
  },

  list_problems: {
    description:
      'Os problemas abertos de todos os clientes, do mais grave para o menos. Cada um traz o cliente, o plano, o ' +
      'problema em português e desde quando. Use para "quem está com problema?", "o que quebrou?", "quem está ' +
      'parado?". Se três ou mais clientes tiverem o MESMO problema, diga isso — quase sempre significa que a ' +
      'causa é nossa, não deles.',
    inputSchema: {
      type: 'object',
      properties: {
        severity: { type: 'string', enum: ['critical', 'warning', 'info'], description: 'Filtra por gravidade. Omita para ver tudo.' },
      },
    },
    async run(args) {
      const qs = args.severity ? `?severity=${encodeURIComponent(args.severity)}` : '';
      return callOps(`/internal/ops/problems${qs}`);
    },
  },

  customer_dossier: {
    description:
      'Tudo sobre um cliente: plano, estado, runtime, créditos, canais e os problemas abertos dele. Use quando a ' +
      'equipe perguntar por um cliente pelo nome ("o que houve com a padaria?") — se não souber o identificador ' +
      'exato, chame list_problems antes para descobrir o slug.',
    inputSchema: {
      type: 'object',
      properties: { slug: { type: 'string', description: 'O identificador do cliente (ex.: padaria-do-ze).' } },
      required: ['slug'],
    },
    async run(args) {
      return callOps(`/internal/ops/customers/${encodeURIComponent(args.slug)}`);
    },
  },

  plan_credential_health: {
    description:
      'A saúde da credencial de LLM de cada plano — a que, quando morre, derruba TODOS os clientes daquele plano ' +
      'de uma vez. Use quando vários clientes do mesmo plano estiverem parados, e sempre que a equipe perguntar ' +
      'sobre credencial, chave, token ou login de provedor.',
    inputSchema: { type: 'object', properties: {} },
    async run() {
      return callOps('/internal/ops/plan-credentials');
    },
  },

  restart_runtime: {
    description:
      'Reinicia a MAG de um cliente. É reversível e barato: o container é recriado e NENHUM dado é perdido — ' +
      'memória, conversas, sessões e canais ficam intactos. Leva alguns segundos até ele voltar. Use quando o ' +
      'runtime estiver travado ou depois de uma correção de configuração.',
    inputSchema: {
      type: 'object',
      properties: { slug: { type: 'string', description: 'O identificador do cliente.' } },
      required: ['slug'],
    },
    async run(args) {
      const r = await callOps(`/internal/ops/customers/${encodeURIComponent(args.slug)}/restart`, { method: 'POST' });
      return r.mensagem || 'Reinício enfileirado.';
    },
  },

  list_tickets: {
    description:
      'Os chamados de suporte que esperam resposta NOSSA (não os que estão com o cliente). Traz assunto, cliente, ' +
      'prioridade e se já passou do prazo sem uma palavra. Use para "tem chamado aberto?", "o que o suporte ' +
      'precisa responder?".',
    inputSchema: { type: 'object', properties: {} },
    async run() {
      return callOps('/internal/ops/tickets');
    },
  },

  reply_ticket: {
    description:
      'Responde um chamado. A resposta chega no painel do cliente E no canal da MAG dele, LITERAL — o texto sai ' +
      'exatamente como você escrever, sem reescrita. Por isso: escreva a resposta final, em português claro, sem ' +
      'jargão técnico e sem prometer prazo que não foi combinado. Se não tiver certeza do que responder, PERGUNTE ' +
      'à equipe antes de mandar.',
    inputSchema: {
      type: 'object',
      properties: {
        ticketId: { type: 'string', description: 'O id do chamado, obtido via list_tickets.' },
        texto: { type: 'string', description: 'A resposta final para o cliente, já pronta.' },
      },
      required: ['ticketId', 'texto'],
    },
    async run(args) {
      const r = await callOps(`/internal/ops/tickets/${encodeURIComponent(args.ticketId)}/reply`, {
        method: 'POST',
        body: JSON.stringify({ texto: args.texto }),
      });
      return `Respondido (${r.entregue || 'sino'}). O chamado agora está: ${r.situacao}.`;
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

log(`started (api=${MAG_API_URL || 'unset'} key=${MAG_OPS_KEY ? 'ok' : 'MISSING'})`);
