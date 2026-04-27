FROM nousresearch/hermes-agent:latest

USER root

RUN mkdir -p /opt/data/.hermes /opt/data/.hermes/skills /opt/hermes-custom

# Config + SOUL default
RUN printf 'model:\n  provider: ollama-cloud\n  default: gemma4:31b-cloud\n' > /opt/data/.hermes/config.yaml
RUN printf 'Você é Mika, uma assistente pessoal de IA criada pela DomCo.' > /opt/data/.hermes/SOUL.md

# Patches
COPY patches/skills_api.py   /opt/hermes-custom/skills_api.py
COPY patches/apply_patch.py  /opt/hermes-custom/apply_patch.py

# Disponibiliza skills_api no PYTHONPATH e injeta o include_router no api_server.py
ENV PYTHONPATH="/opt/hermes-custom:${PYTHONPATH}"
RUN python3 /opt/hermes-custom/apply_patch.py /opt/hermes/gateway/platforms/api_server.py

# Entrypoint custom
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gateway"]
