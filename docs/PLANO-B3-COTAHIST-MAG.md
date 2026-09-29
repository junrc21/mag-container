# Plano de disponibilização do COTAHIST na MAG

Data: 10/09/2026. Status: planejamento; nenhuma integração ou implantação executada nesta etapa.

## 1. Objetivo e evidências

Permitir que os agentes MAG consultem identificação de instrumentos, preços históricos, variação de fechamento e comparação de ativos B3 usando o backend mag-investing, com controle por plano e cliente.

Estado observado nos checkouts locais:
- mag-investing: download e ingestão COTAHIST, fila BullMQ, armazenamento PostgreSQL e cinco tools de mercado no endpoint /mcp. Piloto real local concluiu com 2.733.374 preços, 248.338 instrumentos e zero registros rejeitados; histórico, desempenho e comparação responderam via HTTP. Isso não valida produção.
- mag-container: mcp/investing/server.mjs já está incluído pelo Dockerfile, mas anuncia apenas resolve_issuer, get_regulatory_report e get_notifications, referentes à CVM. Chama POST /internal/investing/* na MAG.
- MAG: no checkout main d2509c9 não foram encontradas as rotas /internal/investing, módulo Investing ou configuração dedicada desse MCP. Existe geração de config.yaml, configuração MCP por plano/cliente e provisionamento dos runtimes.

Antes de implementar, comparar esses checkouts com as versões efetivamente implantadas e branches relevantes. Reaproveitar uma ponte Investing existente caso seja encontrada, sem duplicar serviços ou regredir CVM.

## 2. Arquitetura proposta

Usuário → agente MAG no runtime → MCP stdio investing (mag-container) → rotas internas da mag-api (MAG) → cliente MCP HTTP autenticado → mag-investing /mcp → PostgreSQL.

Separadamente: agendamento administrativo → fila B3 → worker mag-investing → arquivo oficial → armazenamento bruto e PostgreSQL.

O runtime não recebe token do mag-investing, conexão de banco nem chave administrativa de ingestão. Consultas não baixam arquivos e não iniciam backfill. CVM e mercado continuam com ferramentas específicas, dentro de um único servidor Investing.

## 3. Parâmetros propostos e autorização

Os nomes abaixo são novos, sujeitos à conciliação com eventual implementação em outra versão.

No control plane MAG:
- MAG_INVESTING_URL: endereço interno do serviço mag-investing, sem /mcp; obrigatório ao habilitar Investing.
- MAG_INVESTING_MCP_TOKEN: segredo correspondente ao MCP_SERVICE_TOKEN do backend; sem valor padrão em produção.
- MAG_INVESTING_TIMEOUT_MS: inicialmente 10000; prazo da chamada upstream deve ser menor que os 15000 ms atuais do proxy no runtime.
- MAG_INVESTING_B3_ENABLED: chave global de liberação, inicialmente false; permite interromper a disponibilização da B3 sem desligar CVM.

Por plano e tenant:
- Capacidade de plano b3MarketDataEnabled, padrão false.
- Override de tenant b3MarketDataEnabled, anulável: null herda o plano; false restringe; true não supera um plano que proíbe.
- Regra efetiva: chave global ativa E plano autorizado E tenant não restringido. A configuração de CVM permanece independente.
- Implementar no mecanismo de capacidades existente; se precisar de campos, criar migration aditiva/idempotente. Não usar a semântica legada de lista vazia como autorização implícita para essa nova capacidade.

No runtime:
- Reutilizar MAG_API_URL, MAG_INTERNAL_KEY e MAG_TENANT_ID.
- Emitir INVESTING_B3_ENABLED=true/false no env do MCP como reflexo da autorização efetiva. Este sinal controla descoberta, mas nunca substitui validação na API.
- Sem o novo parâmetro, manter as três tools CVM e não anunciar B3.

A mag-api deve verificar a autorização a cada chamada, inclusive após revogação e com runtime desatualizado. Validar a identidade do runtime/tenant por credencial ou associação confiável do servidor: uma chave global mais tenantId informado no corpo não comprova isolamento entre tenants. Não aceitar tenantId ou campos reservados sobrescritos pelos argumentos da tool. Esta proteção deve ser tratada junto à ponte, preservando compatibilidade dos demais conectores.

## 4. Contratos e ferramentas

Adicionar cinco tools ao proxy Investing. As rotas são propostas na MAG, não endpoints já existentes.

| Tool | POST interno proposto | Entrada |
|---|---|---|
| resolve_financial_entity | /internal/investing/resolve-financial-entity | query; limit 1–20 |
| get_asset | /internal/investing/asset | asset: UUID do instrumento ou ticker |
| get_asset_history | /internal/investing/asset-history | asset, from, to, limit 1–1000 |
| get_asset_performance | /internal/investing/asset-performance | asset, period: 30d/90d/1y/ytd |
| compare_assets | /internal/investing/compare-assets | 2–10 assets, period |

Preservar os nomes e inputs do backend para reduzir tradução. Em buscas ambíguas, preferir o instrumentId resolvido. A consulta PETR4 pode também retornar PETR4F e outros aliases; não tratar toda correspondência como o mesmo instrumento.

A ponte usa allowlist fixa de tools; não aceita URL, nome arbitrário de ferramenta upstream ou comando de ingestão do modelo. Usar cliente MCP com transporte HTTP suportado, sem reinventar negociação de protocolo. Normalizar structuredContent.result do backend para o envelope data usado pelo proxy MAG; preservar isError e diferenciar resposta vazia de indisponibilidade.

Retorno mínimo: identificação, moeda, preços decimais como strings, intervalo pedido e efetivo, cobertura, truncamento, origem B3/COTAHIST, indicação de preço não ajustado, realtime=false e warnings. Acrescentar procedência do arquivo/sourceUrl e datas de checagem/processamento no backend: hoje source tem apenas informações genéricas da fonte. Não confundir último pregão disponível com horário da última sincronização.

Definir schemas de saída concretos no mag-investing, substituindo z.any(). Validar datas reais, from <= to, limites de tamanho/intervalo e resposta upstream. Corrigir truncamento para sinalizar somente quando houver dados adicionais; a igualdade entre quantidade e limit não prova truncamento. Não permitir cálculo silencioso de desempenho com série truncada.

## 5. Mudanças no mag-investing

Arquivos centrais: src/apps/mcp/server.ts; src/contexts/market/public/market-queries.ts; integrações/b3/application/sync-b3-cotahist.ts; integrações/b3/infrastructure/b3-cotahist-processor.ts; src/apps/worker/main.ts; src/shared/config/env.ts.

1. Introduzir DTOs/schemas de mercado, validação de entrada, procedência e frescor descritos acima.
2. Resolver ambiguidades de instrumento com preferência por identidade explícita; não escolher silenciosamente entre mercados quando um ticker tiver múltiplas identidades.
3. Revisar a persistência: createMany(skipDuplicates) evita duplicação, mas não atualiza preços existentes quando a fonte corrige valores. Definir e testar atualização idempotente, proveniência e preservação de cobertura global ao reprocessar arquivos distintos.
4. Revisar fator de cotação: o parser lê quotationFactor, mas a persistência não o conserva/aplica. Antes de liberar todos os instrumentos, normalizar corretamente ou restringir explicitamente os instrumentos suportados. O piloto com ações comuns não prova todas as classes.
5. Validar integridade do arquivo e qualidade: header/trailer, ano, registros inválidos e falha parcial. O código atual pode concluir com rejeições; estabelecer política explícita e evitar publicar ingestão incompleta como saudável.
6. Adicionar agendamento B3 próprio. Hoje o worker registra schedules CVM; os jobs B3 existem, mas não há schedule equivalente. Proposta: checagem diária configurável após a janela de publicação, fuso America/Sao_Paulo; confirmar janela operacional antes de fixar horário.
7. Usar ETag/Last-Modified e checksum, retries limitados e exclusão por arquivo/ano para impedir backfill e agenda concorrentes. Force deve ter comportamento documentado e testado.
8. Backfill inicial do ano anterior e corrente para sustentar períodos de um ano. Expor cobertura real caso falte parte do período.
9. Manter PostgreSQL, raw storage e temporários com espaço adequado; medir tamanho de banco, índices, WAL, disco temporário e memória no ambiente alvo. Não extrapolar o tmpfs do teste para produção.

## 6. Mudanças no MAG

1. Confirmar/reaproveitar a ponte Investing; se ausente, criar api/src/modules/investing com client, service, schemas e testes. Registrar rotas no módulo interno seguindo a convenção existente.
2. Configurar env em api/src/config/env.ts, .env.example e implantação da API. Segredo centralizado e logs com redação de tokens; nunca colocá-lo no bootstrap do cliente.
3. Aplicar autenticação, autorização efetiva por tenant/plano, quotas/rate limit, timeout e auditoria com correlationId. Reutilizar infraestrutura existente onde adequada.
4. Em api/src/modules/internal/internal.service.ts, gerar mcp_servers.investing apontando para /opt/mag/investing-mcp/server.mjs; incluir o sinal B3 e reservar o nome investing contra substituição por MCP arbitrário. Conciliar com a validação de nomes no admin.
5. Adicionar controle administrativo “Dados históricos B3” por plano/cliente e status de disponibilidade/frescor. Não exigir que o cliente cadastre credenciais da B3 para o COTAHIST.
6. O controle admin deve atualizar a política imediatamente e enfileirar runtime-refresh para atualizar descoberta/configuração. Testar com provisioner worker ativo: em dev, enfileirar não significa aplicar.
7. Preservar CVM. Se a ponte CVM precisar ser criada, tratar separadamente o cursor de notificações por tenant/consumer: get_notifications do backend não implementa por si só a promessa de “ainda não apresentadas por esta MAG” do proxy atual.

## 7. Mudanças no mag-container e comportamento do agente

Arquivos centrais: mcp/investing/server.mjs; tests/test_investing_mcp.py; Dockerfile; documentação.

- Anunciar três tools CVM sem o sinal B3 e oito tools quando B3 estiver habilitada, mantendo compatibilidade de nomes.
- Validar argumentos e construir o corpo com tenantId confiável por último, impedindo override; rejeitar campos extras.
- Preservar structuredContent, warnings, fonte e erros de negócio. Não encaminhar texto técnico bruto ou segredos em mensagens ao cliente.
- Generalizar mensagens de indisponibilidade hoje exclusivas da CVM.
- Manter o binário no mesmo caminho já copiado pelo Dockerfile; atualizar versão do servidor e construir imagem por tag imutável.
- Atualizar instruções geradas de uso do Investing: empresa/balanço/divulgação → CVM; preço histórico/volume/desempenho → B3; resolver identidade antes de consultar; declarar último pregão e variação não ajustada; dados ausentes nunca viram valores estimados; não chamar preço histórico de cotação ao vivo.
- Validar descoberta das tools nos canais usados pela MAG. Usar o refresh de sessão existente para aplicar instruções novas sem apagar memória ou históricos.

## 8. Testes e aceite

Testes de contrato rápidos em CI:
- Tools list com B3 ligada/desligada; três tools CVM preservadas.
- Inputs inválidos, datas impossíveis/invertidas, limite e ativos em excesso.
- Tenant autorizado, proibido, revogado e tentativa de trocar tenantId.
- Token inválido, timeout, upstream indisponível e HTTP 429, sem vazamento de credenciais.
- Propagação de structuredContent, origem, cobertura parcial, truncamento e warnings.
- Reprocessamento corrigindo um preço, mantendo identidade e cobertura; fator de cotação; falha estrutural; concorrência.

E2E entre os três projetos:
1. Serviços e banco isolados, imagem do mag-container e API MAG configurada para mag-investing de teste.
2. Ingestão real oficial controlada; fixtures na CI diária para evitar download anual em cada commit.
3. Cliente MCP stdio → proxy do container → MAG → MCP HTTP mag-investing → banco real.
4. Consultar PETR4 por identidade, histórico, desempenho e comparação com VALE3; conferir contra o arquivo e o banco, sem fixar preços futuros como expectativa.
5. Testar CVM, falta de cobertura, revogação e erro de rede.
6. Pergunta em canal de tenant canário: resposta com preço, data, fonte e natureza histórica corretos; sem ferramentas indisponíveis ou instruções antigas.

Aceite: fluxo inteiro real funcionando; política aplicada na API; zero segredos no runtime/payload/log de cliente; fonte e cobertura corretas; CVM preservada; job sem duplicação/perda de correções; atualização periódica e alerta de atraso; rollout/rollback preservando dados.

## 9. Sequência de entrega e rollback

1. Conciliar versões/branches e congelar contratos.
2. Corrigir contratos e confiabilidade B3 no mag-investing; medir ingestão no ambiente alvo.
3. Entregar ponte, parâmetros e capacidades no MAG, com chave global desligada.
4. Entregar as cinco tools no mag-container e construir imagem com tag imutável.
5. Executar E2E integrado local/staging e preparar base com cobertura suficiente.
6. Implantar backend Investing e MAG antes de anunciar as novas tools. Publicar imagem, atualizar apenas um tenant canário, ativar capacidade e conferir canal/sessão.
7. Após validação explícita do canário na tag atual, ampliar por plano e depois demais tenants autorizados.

Rollback: desligar chave global B3 e revogar disponibilidade nas rotas imediatamente; atualizar configuração do runtime; retornar imagem anterior pelo fluxo existente se necessário. Não remover tabelas de mercado nem /opt/data dos clientes. CVM permanece disponível.

Nenhuma alteração manual nos volumes de clientes, substituição de banco ou deploy faz parte deste plano já executado.

## 10. Divisão sugerida de PRs

- PR 1, mag-investing: contratos, integridade e correções de persistência necessários à exposição.
- PR 2, mag-investing: agenda B3, observabilidade e operação/backfill.
- PR 3, MAG: ponte autenticada, capacidades por plano/tenant, configuração e controles admin.
- PR 4, mag-container: tools B3, sinal de habilitação, respostas estruturadas e testes.
- PR 5, repositórios envolvidos: teste integrado, documentação de implantação e evidência do canário.

PR 4 pode ser desenvolvido após congelar os contratos; sua ativação depende dos PRs anteriores. Estimativa deve ser fechada após a conciliação das versões e revisão dos problemas de integridade; não considerar este trabalho uma simples troca de variável.
