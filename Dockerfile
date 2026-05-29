# ============================================================
# Stage 1: Build Hermes a partir do código-fonte oficial
# ============================================================
FROM python:3.12-slim AS hermes-base

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        curl \
        ca-certificates \
        build-essential \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Clona o Hermes oficial da NousResearch
RUN git clone --depth 1 https://github.com/NousResearch/hermes-agent.git /opt/hermes
WORKDIR /opt/hermes

# Builda o frontend do dashboard web (necessário para `hermes dashboard`)
WORKDIR /opt/hermes/web
RUN npm install && npm run build

# Volta para a raiz e instala o Hermes + dependências Python
WORKDIR /opt/hermes
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -e ".[all]"

# ============================================================
# Stage 2: Customizações (skills_api + entrypoint + proxy)
# ============================================================
FROM hermes-base

# Dependências adicionais para o proxy/skills_api
RUN pip install --no-cache-dir aiohttp

# Copia customizações
COPY patches/skills_api.py /opt/hermes-custom/skills_api.py
COPY entrypoint.sh         /opt/hermes-custom/entrypoint.sh
COPY plugins/mika_runtime  /opt/hermes/plugins/mika_runtime
RUN chmod +x /opt/hermes-custom/entrypoint.sh

# Garante que o Python encontre o módulo customizado
ENV PYTHONPATH=/opt/hermes-custom:/opt/hermes
ENV HERMES_MODEL_PROVIDER=ollama-cloud
ENV HERMES_MODEL_DEFAULT=gemma4:31b-cloud
ENV HERMES_STT_PROVIDER=local
ENV HERMES_STT_LOCAL_MODEL=base
ENV HERMES_TTS_PROVIDER=disabled

# Diretório de dados/config do Hermes
RUN mkdir -p /opt/data/.hermes /opt/hermes-custom

# Config padrão. O entrypoint reescreve este arquivo em runtime a partir das
# env vars por tenant, mas deixamos um fallback válido já embutido na imagem.
RUN printf '%s\n' \
    'name: hermes-custom' \
    'host: 127.0.0.1' \
    'port: 8000' \
    'data_dir: /opt/data' \
    'model:' \
    '  provider: "ollama-cloud"' \
    '  default: "gemma4:31b-cloud"' \
    'plugins:' \
    '  enabled:' \
    '    - mika_runtime' \
    'platform_toolsets:' \
    '  telegram:' \
    '    - hermes-telegram' \
    '    - mika_integrations' \
    'stt:' \
    '  enabled: true' \
    '  provider: "local"' \
    '  local:' \
    '    model: "base"' \
    > /opt/data/.hermes/config.yaml

# SOUL.md padrão (sobrescrito em runtime via HERMES_SOUL_OVERRIDE)
RUN printf '%s\n' \
    '# Hermes Soul' \
    '' \
    'Default soul. Override via HERMES_SOUL_OVERRIDE.' \
    '' \
    'Quando o usuário pedir para agendar, lembrar ou automatizar algo recorrente, use a tool cronjob_create passando a frase original dele em natural_language_input.' \
    'Quando o usuário pedir para criar, salvar ou ensinar uma skill nova, use a tool skill_create passando a frase original dele em natural_language_input.' \
    > /opt/data/.hermes/SOUL.md

# Cria o usuário hermes (esperado pelos scripts internos do Hermes)
RUN groupadd -r hermes \
    && useradd -r -g hermes -d /opt/data -s /usr/sbin/nologin hermes \
    && chown -R hermes:hermes /opt/data /opt/hermes-custom /opt/hermes

# Porta pública do proxy/skills_api
EXPOSE 8642

ENTRYPOINT ["/opt/hermes-custom/entrypoint.sh"]
