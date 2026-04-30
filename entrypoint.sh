#!/usr/bin/env bash
set -Eeuo pipefail

INTERNAL_HOST="${INTERNAL_HOST:-127.0.0.1}"
INTERNAL_PORT="${INTERNAL_PORT:-8000}"
PUBLIC_HOST="${PUBLIC_HOST:-0.0.0.0}"
PUBLIC_PORT="${PORT:-${PUBLIC_PORT:-8642}}"

HERMES_HOME="${HERMES_HOME:-/opt/data/.hermes}"
CONFIG_PATH="${CONFIG_PATH:-$HERMES_HOME/config.yaml}"
SOUL_PATH="${SOUL_PATH:-$HERMES_HOME/SOUL.md}"
MODEL_PROVIDER="${HERMES_MODEL_PROVIDER:-ollama-cloud}"
MODEL_DEFAULT="${HERMES_MODEL_DEFAULT:-gemma4:31b-cloud}"
STT_PROVIDER="${HERMES_STT_PROVIDER:-local}"
STT_LOCAL_MODEL="${HERMES_STT_LOCAL_MODEL:-base}"
STT_OPENAI_MODEL="${HERMES_STT_OPENAI_MODEL:-whisper-1}"
TTS_PROVIDER="${HERMES_TTS_PROVIDER:-disabled}"

mkdir -p "$HERMES_HOME"

write_config() {
  cat > "$CONFIG_PATH" <<EOF
name: hermes-custom
host: ${INTERNAL_HOST}
port: ${INTERNAL_PORT}
data_dir: /opt/data
model:
  provider: "${MODEL_PROVIDER}"
  default: "${MODEL_DEFAULT}"
EOF

  case "${STT_PROVIDER}" in
    ""|disabled|none)
      cat >> "$CONFIG_PATH" <<EOF
stt:
  enabled: false
EOF
      ;;
    *)
      cat >> "$CONFIG_PATH" <<EOF
stt:
  enabled: true
  provider: "${STT_PROVIDER}"
  local:
    model: "${STT_LOCAL_MODEL}"
  openai:
    model: "${STT_OPENAI_MODEL}"
EOF
      ;;
  esac

  case "${TTS_PROVIDER}" in
    ""|disabled|none)
      ;;
    *)
      cat >> "$CONFIG_PATH" <<EOF
tts:
  provider: "${TTS_PROVIDER}"
EOF
      ;;
  esac
}

write_config

echo "[entrypoint] Config: $CONFIG_PATH"
echo "[entrypoint] Model provider: $MODEL_PROVIDER"
echo "[entrypoint] Model default:  $MODEL_DEFAULT"
echo "[entrypoint] STT provider:   $STT_PROVIDER"
echo "[entrypoint] TTS provider:   $TTS_PROVIDER"

if [ -n "${HERMES_SOUL_OVERRIDE:-}" ]; then
  echo "[entrypoint] Applying HERMES_SOUL_OVERRIDE to $SOUL_PATH"
  printf '%s\n' "$HERMES_SOUL_OVERRIDE" > "$SOUL_PATH"
fi

if [ "${HERMES_SUSPENDED:-}" = "true" ]; then
  echo "[entrypoint] Agent suspended via HERMES_SUSPENDED=true"
  exec sleep infinity
fi

echo "[entrypoint] Hermes interno: $INTERNAL_HOST:$INTERNAL_PORT"
echo "[entrypoint] Proxy público:  $PUBLIC_HOST:$PUBLIC_PORT"
echo "[entrypoint] Iniciando Hermes original..."

hermes dashboard \
  --host "$INTERNAL_HOST" \
  --port "$INTERNAL_PORT" \
  --no-open &

HERMES_PID="$!"

echo "[entrypoint] Aguardando Hermes responder em $INTERNAL_HOST:$INTERNAL_PORT..."

for i in $(seq 1 60); do
  if ! kill -0 "$HERMES_PID" 2>/dev/null; then
    echo "[entrypoint] ERRO: Hermes morreu antes de ficar pronto"
    wait "$HERMES_PID"
    exit 1
  fi

  if curl -fsS "http://$INTERNAL_HOST:$INTERNAL_PORT" >/dev/null 2>&1; then
    echo "[entrypoint] Hermes pronto"
    break
  fi

  sleep 1
done

if ! kill -0 "$HERMES_PID" 2>/dev/null; then
  echo "[entrypoint] ERRO: Hermes morreu antes de ficar pronto"
  wait "$HERMES_PID"
  exit 1
fi

echo "[entrypoint] Iniciando proxy público em $PUBLIC_HOST:$PUBLIC_PORT..."

exec python /opt/hermes-custom/skills_api.py \
  --host "$PUBLIC_HOST" \
  --port "$PUBLIC_PORT" \
  --target "http://$INTERNAL_HOST:$INTERNAL_PORT"
