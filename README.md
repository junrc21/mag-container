# mag-container (Hermes MAG)

Este repositório NÃO “armazena uma imagem Docker” (isso fica em um registry, ex. GHCR).
Ele armazena o **recipe** (Dockerfile + entrypoint + bootstrap) para você **reconstruir** uma imagem idêntica e criar novas MAGs rapidamente.

## Como usar

1) Defina a imagem base do Hermes (a mesma que você usa hoje no EasyPanel):

```sh
docker build --build-arg BASE_IMAGE=<sua-imagem-atual> -t hermes-mag:clone .
```

2) Publique em um registry (GHCR/DockerHub/privado) e aponte o EasyPanel para essa nova imagem.

## Publicação automática (GHCR)

Este repo inclui um workflow que publica automaticamente uma imagem no GHCR a cada push no `main`:

- Image: `ghcr.io/<owner>/mag-container:latest`
- Também publica tag por commit: `ghcr.io/<owner>/mag-container:<sha>`

Arquivo: `.github/workflows/publish-ghcr.yml`

Observação: ajuste `BASE_IMAGE` no workflow para a mesma imagem/tag do Hermes que você usa no EasyPanel.

## Clonar `/opt/data` (memória/estado) para uma nova MAG

Não coloque `/opt/data` dentro da imagem (GHCR) — isso contém estado, memória e possíveis credenciais.
O caminho correto é clonar o storage persistente e montar no novo serviço.

Script (HOST):
- `scripts/clone_opt_data_on_host.sh`

Uso (no servidor, como root):

```sh
sudo ./scripts/clone_opt_data_on_host.sh --service cyriusx_hermes-mag
```

O script imprime o path/volume clonado que você deve montar como `/opt/data` no novo serviço.

## Importante

- Não embutir tokens/API keys na imagem: configure via env vars no EasyPanel.
- Monte um volume em `/opt/data` para persistência.
- Para clonar “estado”, copie o volume antigo para um volume novo (não compartilhe o mesmo `/opt/data` entre dois serviços).

## ByteRover (brv)

O `entrypoint.sh` faz auto-install do ByteRover CLI (`brv`) no primeiro boot se ele não existir no volume.
Isso instala em `/opt/data/.brv-cli/bin/brv` e cria um symlink em `/opt/data/.local/bin/brv` (persistente).

Para desabilitar: defina `BRV_AUTO_INSTALL=0` nas env vars.

## Edge TTS (edge-tts)

Mesmo quando o TTS funciona no Hermes, scripts manuais que fazem `import edge_tts` podem falhar se o Python do Hermes não tiver `pip/ensurepip`.
Para evitar isso, o `entrypoint.sh` tenta garantir que `edge-tts` esteja importável no Python do Hermes via `uv` (padrão ligado).

Para desabilitar: defina `EDGE_TTS_AUTO_INSTALL=0` nas env vars.

## Migrar Neo4j -> ByteRover (export JSONL + import markdown)

Pré-requisitos:
- Acesso ao Neo4j via HTTP transactional endpoint (tx/commit)
- Variáveis de ambiente configuradas (NUNCA commite senha no Git)

Env vars:
- `NEO4J_URL` (ex: `https://<host>/db/neo4j/tx/commit`)
- `NEO4J_USER`
- `NEO4J_PASSWORD`

Rodar dentro do container (como usuário `hermes`):

```sh
export NEO4J_URL="https://<host>/db/neo4j/tx/commit"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="***"

sh /opt/hermes/scripts/neo4j_export_and_import_byterover.sh
```

Isso cria:
- Export JSONL: `/opt/data/exports/neo4j/{nodes.jsonl,rels.jsonl}`
- Import markdown: `/opt/data/byterover/.brv/context-tree/imports/neo4j/{nodes,rels}/*.md`

Depois (opcional), rode `brv curate` para consolidar e gerar abstracts/overviews.
