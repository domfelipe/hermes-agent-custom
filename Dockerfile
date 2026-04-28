FROM ghcr.io/nousresearch/hermes:latest

# Instala aiohttp para o servidor proxy + skills_api
RUN pip install --no-cache-dir aiohttp

# Copia os arquivos customizados
COPY patches/skills_api.py /opt/hermes-custom/skills_api.py
COPY entrypoint.sh /opt/hermes-custom/entrypoint.sh
RUN chmod +x /opt/hermes-custom/entrypoint.sh

# Garante que o Python encontra os módulos do Hermes
ENV PYTHONPATH=/opt/hermes:/opt/hermes-custom

# Config + SOUL default (embutidos na imagem)
RUN mkdir -p /opt/data/.hermes && \
    printf 'model:\n  provider: ollama-cloud\n  default: gemma4:31b-cloud\n' > /opt/data/.hermes/config.yaml && \
    printf 'Você é Mika, uma assistente pessoal de IA criada pela DomCo.' > /opt/data/.hermes/SOUL.md

# A porta pública é definida pelo Railway via $PORT
EXPOSE 8642

ENTRYPOINT ["/opt/hermes-custom/entrypoint.sh"]
