# MAG WhatsApp Outbound MCP Server

MCP server que expõe a tool `send_whatsapp_message` para envio proativo de mensagens WhatsApp.

## Funcionalidade

Permite que a AI envie mensagens WhatsApp para números específicos com:

- **Validação de confirmação**: Exige `confirmed_by_user=true` (não envia sem confirmação explícita)
- **Validação de allowlist**: Verifica `WHATSAPP_OUTBOUND_ALLOWED_USERS` (deny-by-default)
- **Normalização de JID**: Aceita números em qualquer formato (raw, com +, formatado)
- **Audit logging**: Todas as tentativas são registradas pelo bridge

## Tool: send_whatsapp_message

### Parâmetros

| Parâmetro | Tipo | Obrigatório | Descrição |
|-----------|------|-------------|-----------|
| `phone_number` | string | Sim | Número de telefone (qualquer formato) |
| `message` | string | Sim | Texto da mensagem |
| `confirmed_by_user` | boolean | Sim | Deve ser `true` (confirmação do usuário) |

### Exemplo de Uso

```json
{
  "name": "send_whatsapp_message",
  "arguments": {
    "phone_number": "+55 11 99999-9999",
    "message": "Olá! Esta é uma mensagem de teste.",
    "confirmed_by_user": true
  }
}
```

## Variáveis de Ambiente

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `WHATSAPP_BRIDGE_PORT` | Porta do bridge WhatsApp | `3000` |
| `MAG_INTERNAL_KEY` | Chave de autenticação interna | *(vazia)* |

## Configuração

Para habilitar este MCP, adicione ao `config.yaml`:

```yaml
mcp_servers:
  whatsapp-outbound:
    command: node
    args:
      - /opt/mag/whatsapp-outbound-mcp/server.mjs
    env:
      WHATSAPP_BRIDGE_PORT: "3000"
      MAG_INTERNAL_KEY: "${MAG_INTERNAL_KEY}"
```

## Segurança

- **Confirmación obrigatória**: O parâmetro `confirmed_by_user` deve ser `true`
- **Allowlist separada**: Usa `WHATSAPP_OUTBOUND_ALLOWED_USERS` (não reutiliza `WHATSAPP_ALLOWED_USERS`)
- **Deny-by-default**: Lista vazia = negar todos os envios
- **Audit logging**: Todas as tentativas são registradas com timestamp, destino e resultado

## Dependências

- [patch_whatsapp_outbound.py](../../bootstrap/patch_whatsapp_outbound.py) - Bridge patch
- [patch_whatsapp_jid_normalization.py](../../bootstrap/patch_whatsapp_jid_normalization.py) - JID normalization
