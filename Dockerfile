# Estágio 1: builda o Hermes a partir do código-fonte oficial
FROM python:3.12-slim AS hermes-base

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 https://github.com/NousResearch/hermes-agent.git /opt/hermes
WORKDIR /opt/hermes
RUN pip install --no-cache-dir -e .

# ===== Suas customizações Mika =====
FROM hermes-base

RUN pip install --no-cache-dir aiohttp

# Skills API + entrypoint
COPY patches/skills_api.py /opt/hermes-custom/skills_api.py
COPY entrypoint.sh         /opt/hermes-custom/entrypoint.sh
RUN chmod +x /opt/hermes-custom/entrypoint.sh

ENV PYTHONPATH=/opt/hermes-custom:/opt/hermes

# Config + SOUL default
RUN mkdir -p /opt/data/.hermes && \
    printf 'model:\n  provider: ollama-cloud\n  default: gemma4:31b-cloud\n' > /opt/data/.hermes/config.yaml && \
    printf 'Você é Mika, uma assistente pessoal de IA criada pela DomCo.' > /opt/data/.hermes/SOUL.md

EXPOSE 8642
ENTRYPOINT ["/opt/hermes-custom/entrypoint.sh"]
