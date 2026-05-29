# mag-container (Hermes MAG)

Este repositório NÃO “armazena uma imagem Docker” (isso fica em um registry, ex. GHCR).
Ele armazena o **recipe** (Dockerfile + entrypoint + bootstrap) para você **reconstruir** uma imagem idêntica e criar novas MAGs rapidamente.

## Como usar

1) Defina a imagem base do Hermes (a mesma que você usa hoje no EasyPanel):

```sh
docker build --build-arg BASE_IMAGE=<sua-imagem-atual> -t hermes-mag:clone .
```

2) Publique em um registry (GHCR/DockerHub/privado) e aponte o EasyPanel para essa nova imagem.

## Importante

- Não embutir tokens/API keys na imagem: configure via env vars no EasyPanel.
- Monte um volume em `/opt/data` para persistência.
- Para clonar “estado”, copie o volume antigo para um volume novo (não compartilhe o mesmo `/opt/data` entre dois serviços).
