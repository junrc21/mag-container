# mag-custom-proxy MCP Server

MCP server que expõe ferramentas HTTP para serviços customizados conectados via connector `custom`.

## Configuração

O MCP lê a env var `CUSTOM_CONNECTOR_CONFIG` com o seguinte formato JSON:

```json
{
  "name": "Meu CRM",
  "baseUrl": "https://api.meucrm.com",
  "apiKey": "abc123...",
  "tools": []
}
```

## Ferramentas expostas

### `http_request`

Faz chamadas HTTP ao serviço configurado.

**Parâmetros:**
- `method` (string, obrigatório): Método HTTP (GET, POST, PUT, DELETE, PATCH)
- `path` (string, obrigatório): Caminho da API (ex: `/clientes`, `/produtos/123`)
- `body` (object, opcional): Corpo da requisição (para POST, PUT, PATCH)
- `headers` (object, opcional): Headers adicionais

**Resposta:**
```json
{
  "ok": true,
  "status": 200,
  "statusText": "OK",
  "body": { ... }
}
```

## Integração com Hermes

No `config.yaml` do Hermes:

```yaml
mcp_servers:
  custom-proxy:
    env:
      CUSTOM_CONNECTOR_CONFIG: "${CUSTOM_CONNECTOR_CONFIG}"
    transport: stdio
    command: node
    args:
      - "/mag/bootstrap/mcp/custom-proxy/server.mjs"
```

A env var `CUSTOM_CONNECTOR_CONFIG` é injetada pelo provisioner MAG quando um connector `custom` está conectado.
