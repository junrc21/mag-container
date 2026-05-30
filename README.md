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

### Conectar provider automaticamente

Se `config.yaml` estiver com `memory.provider: byterover` e existir `GOOGLE_API_KEY` (ou `GEMINI_API_KEY`) nas env vars, a imagem tenta conectar o provider `google` automaticamente no boot.

- Para forçar: `BRV_CONNECT_ON_BOOT=1`
- Para desabilitar totalmente: `BRV_CONNECT_ON_BOOT=0`

### Timeouts do ByteRover (Hermes plugin)

Alguns comandos do ByteRover (especialmente `brv status` e `brv query`) podem levar mais tempo quando a árvore está grande ou com fila de processamento ativa. Esta imagem deixa os timeouts configuráveis via env vars:

- `HERMES_BYTEROVER_QUERY_TIMEOUT_SECONDS` (default `10`)
- `HERMES_BYTEROVER_CURATE_TIMEOUT_SECONDS` (default `120`)
- `HERMES_BYTEROVER_STATUS_TIMEOUT_SECONDS` (default `60`)

Exemplo recomendado para ambientes com import grande:

- `HERMES_BYTEROVER_QUERY_TIMEOUT_SECONDS=120`
- `HERMES_BYTEROVER_STATUS_TIMEOUT_SECONDS=120`

## Edge TTS (edge-tts)

Mesmo quando o TTS funciona no Hermes, scripts manuais que fazem `import edge_tts` podem falhar se o Python do Hermes não tiver `pip/ensurepip`.
Para evitar isso, o `entrypoint.sh` tenta garantir que `edge-tts` esteja importável no Python do Hermes via `uv` (padrão ligado).

Para desabilitar: defina `EDGE_TTS_AUTO_INSTALL=0` nas env vars.
