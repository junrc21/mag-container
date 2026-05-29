ARG BASE_IMAGE=ghcr.io/nousresearch/hermes-agent:latest
FROM ${BASE_IMAGE}

# We keep runtime as user "hermes" (no root at runtime).
# Put bootstrap assets in /opt/hermes/bootstrap (image filesystem).
USER root
RUN mkdir -p /opt/hermes/bootstrap && chown -R hermes:hermes /opt/hermes/bootstrap

COPY --chown=hermes:hermes bootstrap/config.yaml /opt/hermes/bootstrap/config.yaml
COPY --chown=hermes:hermes bootstrap/soul.md /opt/hermes/bootstrap/soul.md
COPY --chown=hermes:hermes entrypoint.sh /opt/hermes/entrypoint.sh

RUN chmod +x /opt/hermes/entrypoint.sh

USER hermes
ENV HOME=/opt/data

ENTRYPOINT ["/opt/hermes/entrypoint.sh"]
