#!/usr/bin/env node
// MAG Custom Proxy MCP server (stdio, zero-dependency).
//
// Reads CUSTOM_CONNECTOR_CONFIG (JSON with name, baseUrl, apiKey, tools) and
// exposes the defined tools to Hermes. Each tool makes an HTTP request to the
// configured service using the stored API key.
//
// Required env (injected by Hermes via mcp_servers.custom-proxy.env):
//   CUSTOM_CONNECTOR_CONFIG  JSON string with the connector config

import { createInterface } from 'node:readline';

const SERVER_NAME = 'mag-custom-proxy';
const SERVER_VERSION = '0.2.0';
const PROTOCOL_VERSION = '2025-06-18';

const MAX_TEXT = 12000;

let config = null;
let tools = {};

function log(...args) {
  process.stderr.write(`[mag-custom-proxy] ${args.join(' ')}\n`);
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

// Substitute path parameters like {id} with actual values from args
function substitutePath(path, args) {
  if (!path || typeof path !== 'string') return path;
  return path.replace(/\{(\w+)\}/g, (match, param) => {
    return args[param] !== undefined ? String(args[param]) : match;
  });
}

// Make HTTP request based on tool definition
async function makeHttpRequest(toolDef, args) {
  const url = `${config.baseUrl.replace(/\/$/, '')}${substitutePath(toolDef.path, args)}`;
  const headers = {
    'Authorization': `Bearer ${config.apiKey}`,
    'Content-Type': 'application/json',
    ...(toolDef.headers || {}),
  };

  const body = toolDef.method !== 'GET' && toolDef.method !== 'DELETE' ? { body: args.body || {} } : {};

  log(`Calling ${toolDef.method} ${url}`);

  try {
    const response = await fetch(url, {
      method: toolDef.method,
      headers,
      ...(Object.keys(body).length ? { body: JSON.stringify(body.body) } : {}),
    });

    const text = await response.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }

    if (!response.ok) {
      return {
        error: true,
        status: response.status,
        statusText: response.statusText,
        body: truncate(String(data)),
      };
    }

    return {
      ok: true,
      status: response.status,
      statusText: response.statusText,
      body: data,
    };
  } catch (err) {
    return {
      error: true,
      message: err.message,
    };
  }
}

// Parse config from CUSTOM_CONNECTOR_CONFIG env var
function initConfig() {
  const configEnv = process.env.CUSTOM_CONNECTOR_CONFIG;
  if (!configEnv) {
    throw new Error('CUSTOM_CONNECTOR_CONFIG env var not set');
  }

  try {
    config = JSON.parse(configEnv);
  } catch (e) {
    throw new Error(`Invalid CUSTOM_CONNECTOR_CONFIG JSON: ${e.message}`);
  }

  if (!config.baseUrl || !config.apiKey) {
    throw new Error('CUSTOM_CONNECTOR_CONFIG must include baseUrl and apiKey');
  }

  // Build tools from config
  const userTools = (config.tools || []);

  if (userTools.length > 0) {
    // Expose user-defined tools
    tools = {};
    for (const toolDef of userTools) {
      const name = toolDef.name || toolDef.id;
      if (!name) continue;

      // Build input schema based on path params and body
      const pathParams = (toolDef.path || '').match(/\{(\w+)\}/g) || [];
      const properties = {};
      const required = [];

      for (const param of pathParams) {
        const paramName = param.slice(1, -1); // Remove { }
        properties[paramName] = {
          type: 'string',
          description: `Parâmetro: ${paramName}`,
        };
        required.push(paramName);
      }

      if (toolDef.method === 'POST' || toolDef.method === 'PUT' || toolDef.method === 'PATCH') {
        properties.body = {
          type: 'object',
          description: 'Corpo da requisição',
        };
      }

      tools[name] = {
        description: toolDef.description || `${toolDef.method} ${toolDef.path}`,
        inputSchema: {
          type: 'object',
          properties,
          required: required.length ? required : undefined,
        },
        method: toolDef.method,
        path: toolDef.path,
        headers: toolDef.headers,
        async run(args) {
          return await makeHttpRequest(this, args || {});
        },
      };
    }
    log(`Initialized with ${userTools.length} user-defined tools for ${config.name || 'custom'} (${config.baseUrl})`);
  } else {
    // Fallback: expose generic http_request tool
    tools = {
      http_request: {
        description: `Faz uma chamada HTTP para ${config.name || 'o serviço customizado'}.`,
        inputSchema: {
          type: 'object',
          properties: {
            method: {
              type: 'string',
              description: 'Método HTTP (GET, POST, PUT, DELETE, etc.)',
              enum: ['GET', 'POST', 'PUT', 'DELETE', 'PATCH'],
            },
            path: {
              type: 'string',
              description: 'Caminho da API (ex: /clientes, /produtos/123). Não inclua a baseUrl.',
            },
            body: {
              type: 'object',
              description: 'Corpo da requisição (para POST, PUT, PATCH).',
            },
            headers: {
              type: 'object',
              description: 'Headers adicionais além do Authorization.',
            },
          },
          required: ['method', 'path'],
        },
        async run(args) {
          const url = `${config.baseUrl.replace(/\/$/, '')}${args.path}`;
          const headers = {
            'Authorization': `Bearer ${config.apiKey}`,
            'Content-Type': 'application/json',
            ...(args.headers || {}),
          };

          log(`Calling ${args.method} ${url}`);

          try {
            const response = await fetch(url, {
              method: args.method,
              headers,
              body: args.body ? JSON.stringify(args.body) : undefined,
            });

            const text = await response.text();
            let data;
            try {
              data = JSON.parse(text);
            } catch {
              data = text;
            }

            if (!response.ok) {
              return {
                error: true,
                status: response.status,
                statusText: response.statusText,
                body: truncate(String(data)),
              };
            }

            return {
              ok: true,
              status: response.status,
              statusText: response.statusText,
              body: data,
            };
          } catch (err) {
            return {
              error: true,
              message: err.message,
            };
          }
        },
      },
    };
    log(`Initialized with generic http_request tool for ${config.name || 'custom'} (${config.baseUrl})`);
  }
}

// ── MCP protocol handlers ─────────────────────────────────────────────────────

async function handleInitialize(id, params) {
  initConfig();
  reply(id, {
    serverInfo: {
      name: SERVER_NAME,
      version: SERVER_VERSION,
    },
    protocolVersion: PROTOCOL_VERSION,
    capabilities: {
      tools: {},
    },
  });
}

async function handleListTools(id) {
  if (!config) initConfig();

  const toolList = Object.entries(tools).map(([name, tool]) => ({
    name,
    description: tool.description,
    inputSchema: tool.inputSchema,
  }));

  reply(id, { tools: toolList });
}

async function handleCallTool(id, params) {
  if (!config) initConfig();

  const { name, arguments: args } = params;
  const tool = tools[name];

  if (!tool) {
    replyError(id, -32601, `Tool not found: ${name}`);
    return;
  }

  try {
    const result = await tool.run(args || {});
    reply(id, { content: [{ type: 'text', text: truncate(JSON.stringify(result, null, 2)) }] });
  } catch (err) {
    replyError(id, -32603, `Tool error: ${err.message}`);
  }
}

// ── stdio loop ─────────────────────────────────────────────────────────────────

const rl = createInterface({
  input: process.stdin,
  output: process.stdout,
  terminal: false,
});

rl.on('line', (line) => {
  try {
    const msg = JSON.parse(line);
    const { id, method } = msg;

    switch (method) {
      case 'initialize':
        handleInitialize(id, msg.params);
        break;
      case 'tools/list':
        handleListTools(id);
        break;
      case 'tools/call':
        handleCallTool(id, msg.params);
        break;
      case 'notifications/initialized':
        log('MCP session initialized');
        break;
      default:
        replyError(id, -32601, `Method not found: ${method}`);
    }
  } catch (err) {
    log(`Error: ${err.message}`);
    if (msg.id) {
      replyError(msg.id, -32700, `Parse error: ${err.message}`);
    }
  }
});

log('MCP custom-proxy server started');
