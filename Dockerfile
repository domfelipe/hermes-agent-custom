# ============================================================
# Stage 1: Build Hermes a partir do código-fonte oficial
# ============================================================
FROM python:3.12-slim AS hermes-base

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Clona o Hermes oficial da NousResearch
RUN git clone --depth 1 https://github.com/NousResearch/hermes-agent.git /opt/hermes

WORKDIR /opt/hermes

# Instala o Hermes e suas dependências
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e .

# ============================================================
# Stage 2: Customizações (skills_api + entrypoint + proxy)
# ============================================================
FROM hermes-base

# Dependências adicionais para o proxy/skills_api
RUN pip install --no-cache-dir aiohttp

# Copia customizações
COPY patches/skills_api.py /opt/hermes-custom/skills_api.py
COPY entrypoint.sh         /opt/hermes-custom/entrypoint.sh
RUN chmod +x /opt/hermes-custom/entrypoint.sh

# Garante que o Python encontre o módulo customizado
ENV PYTHONPATH=/opt/hermes-custom:/opt/hermes

# Diretório de dados/config do Hermes
RUN mkdir -p /opt/data/.hermes /opt/hermes-custom

# Config padrão (caso HERMES_CONFIG_OVERRIDE não seja definido)
RUN printf '%s\n' \
    'name: hermes-custom' \
    'host: 127.0.0.1' \
    'port: 8000' \
    'data_dir: /opt/data' \
    > /opt/data/.hermes/config.yaml

# SOUL.md padrão (sobrescrito em runtime via HERMES_SOUL_OVERRIDE)
RUN printf '%s\n' \
    '# Hermes Soul' \
    '' \
    'Default soul. Override via HERMES_SOUL_OVERRIDE.' \
    > /opt/data/.hermes/SOUL.md

# Cria o usuário hermes (esperado pelos scripts internos do Hermes)
RUN groupadd -r hermes \
    && useradd -r -g hermes -d /opt/data -s /usr/sbin/nologin hermes \
    && chown -R hermes:hermes /opt/data /opt/hermes-custom /opt/hermes

# Porta pública do proxy/skills_api
EXPOSE 8642

ENTRYPOINT ["/opt/hermes-custom/entrypoint.sh"]
