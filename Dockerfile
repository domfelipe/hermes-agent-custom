FROM nousresearch/hermes-agent:latest

USER root

RUN mkdir -p /opt/data/.hermes

RUN printf 'model:\n  provider: ollama-cloud\n  default: gemma4:31b-cloud\n' > /opt/data/.hermes/config.yaml

RUN printf 'Você é Mika, uma assistente pessoal de IA criada pela DomCo.' > /opt/data/.hermes/SOUL.md

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
