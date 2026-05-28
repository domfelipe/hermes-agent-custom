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
GATEWAY_ENABLED="${HERMES_GATEWAY_ENABLED:-auto}"
GATEWAY_ARGS="${HERMES_GATEWAY_ARGS:---replace}"
GATEWAY_API_SERVER_ENABLED="${HERMES_GATEWAY_API_SERVER_ENABLED:-false}"
HERMES_ALLOW_ROOT_GATEWAY="${HERMES_ALLOW_ROOT_GATEWAY:-1}"
export HERMES_ALLOW_ROOT_GATEWAY

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

PIDS=()

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  if [ "${#PIDS[@]}" -gt 0 ]; then
    kill "${PIDS[@]}" 2>/dev/null || true
    wait "${PIDS[@]}" 2>/dev/null || true
  fi
  exit "$status"
}

trap cleanup EXIT INT TERM

hermes dashboard \
  --host "$INTERNAL_HOST" \
  --port "$INTERNAL_PORT" \
  --no-open &

HERMES_PID="$!"
PIDS+=("$HERMES_PID")

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

should_start_gateway=false
case "$GATEWAY_ENABLED" in
  true|1|yes|on)
    should_start_gateway=true
    ;;
  false|0|no|off)
    should_start_gateway=false
    ;;
  auto|"")
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
      should_start_gateway=true
    fi
    ;;
  *)
    echo "[entrypoint] HERMES_GATEWAY_ENABLED inválido: $GATEWAY_ENABLED"
    exit 1
    ;;
esac

if [ "$should_start_gateway" = "true" ]; then
  echo "[entrypoint] Iniciando gateway de mensagens Hermes..."
  if [ "$GATEWAY_API_SERVER_ENABLED" = "true" ]; then
    # shellcheck disable=SC2086
    hermes gateway run $GATEWAY_ARGS &
  else
    # The public runtime API is served by skills_api.py. Provisioned Mika
    # instances still receive API_SERVER_KEY for that proxy, so hide it from
    # the gateway process to avoid starting Hermes' native api_server adapter
    # on the same public port.
    # shellcheck disable=SC2086
    API_SERVER_ENABLED=false API_SERVER_KEY= hermes gateway run $GATEWAY_ARGS &
  fi
  GATEWAY_PID="$!"
  PIDS+=("$GATEWAY_PID")
else
  echo "[entrypoint] Gateway de mensagens desabilitado"
fi

echo "[entrypoint] Iniciando proxy público em $PUBLIC_HOST:$PUBLIC_PORT..."

python /opt/hermes-custom/skills_api.py \
  --host "$PUBLIC_HOST" \
  --port "$PUBLIC_PORT" \
  --target "http://$INTERNAL_HOST:$INTERNAL_PORT" &

PROXY_PID="$!"
PIDS+=("$PROXY_PID")

wait -n "${PIDS[@]}"
EXITED_STATUS=$?
echo "[entrypoint] Um processo do runtime encerrou (status=$EXITED_STATUS); finalizando container"
exit "$EXITED_STATUS"
