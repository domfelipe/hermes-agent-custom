FROM nousresearch/hermes-agent:latest

RUN mkdir -p /opt/data/.hermes

RUN printf 'model:\n  provider: ollama-cloud\n  default: gemma4:31b-cloud\n' > /opt/data/.hermes/config.yaml

CMD ["hermes", "gateway"]
