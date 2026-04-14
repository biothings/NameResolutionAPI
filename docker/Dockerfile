FROM python:3.12 AS builder

ARG NAMERES_REPO=https://github.com/chevvak2/NameResolutionAPI.git
ARG NAMERES_BRANCH=main

WORKDIR /build/nameres
RUN git clone -b ${NAMERES_BRANCH} --recursive ${NAMERES_REPO} .
RUN git rev-parse HEAD > /build/nameres/version.txt
RUN pip wheel --wheel-dir=/build/wheels /build/nameres

FROM caddy:2.8-builder AS caddy_builder
RUN xcaddy build

FROM python:3.12-slim
RUN apt update -q -y && apt install -y --no-install-recommends git supervisor curl telnet && rm -rf /var/lib/apt/lists/*
RUN useradd -m nameres && usermod -aG sudo nameres
USER nameres
WORKDIR /home/nameres/
RUN python -m venv /home/nameres/venv
COPY --from=builder --chown=nameres:nameres /build/wheels /home/nameres/wheels
RUN /home/nameres/venv/bin/pip install /home/nameres/wheels/*.whl && rm -rf /home/nameres/wheels

WORKDIR /home/nameres/configuration
COPY --from=builder --chown=nameres:nameres /build/nameres/src/nameres/config/config.default.json /home/nameres/configuration
COPY --from=builder --chown=nameres:nameres /build/nameres/version.txt /home/nameres/configuration/version.txt

# Override with corrected configuration for Kubernetes cluster
COPY --chown=nameres:nameres configuration/config.json /home/nameres/configuration/config.json

COPY --chown=nameres:nameres configuration/supervisord.conf /etc/supervisor/supervisord.conf
COPY --chown=nameres:nameres configuration/Caddyfile /etc/caddy/Caddyfile

COPY --from=caddy_builder --chown=nameres:nameres /usr/bin/caddy /usr/bin/caddy

STOPSIGNAL SIGINT
EXPOSE 9000

ENTRYPOINT ["supervisord"]
CMD ["-c", "/etc/supervisor/supervisord.conf"]
