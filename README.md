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

## Importante

- Não embutir tokens/API keys na imagem: configure via env vars no EasyPanel.
- Monte um volume em `/opt/data` para persistência.
- Para clonar “estado”, copie o volume antigo para um volume novo (não compartilhe o mesmo `/opt/data` entre dois serviços).
