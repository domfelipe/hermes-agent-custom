#!/usr/bin/env bash
set -Eeuo pipefail

INTERNAL_HOST="${INTERNAL_HOST:-127.0.0.1}"
INTERNAL_PORT="${INTERNAL_PORT:-8000}"
PUBLIC_HOST="${PUBLIC_HOST:-0.0.0.0}"
PUBLIC_PORT="${PORT:-${PUBLIC_PORT:-8642}}"

HERMES_HOME="${HERMES_HOME:-/opt/data/.hermes}"
SOUL_PATH="${SOUL_PATH:-$HERMES_HOME/SOUL.md}"

mkdir -p "$HERMES_HOME"

if [ -n "${HERMES_SOUL_OVERRIDE:-}" ]; then
  echo "[entrypoint] Applying HERMES_SOUL_OVERRIDE to $SOUL_PATH"
  printf '%s\n' "$HERMES_SOUL_OVERRIDE" > "$SOUL_PATH"
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
