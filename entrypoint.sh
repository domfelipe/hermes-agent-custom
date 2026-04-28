#!/usr/bin/env bash
set -e

HERMES_DATA_DIR="${HERMES_DATA_DIR:-/opt/data}"
HERMES_CONFIG_DIR="${HERMES_DATA_DIR}/.hermes"
SOUL_FILE="${HERMES_CONFIG_DIR}/SOUL.md"

mkdir -p "${HERMES_CONFIG_DIR}"

# Aplica override do SOUL.md se a env estiver setada
if [ -n "${HERMES_SOUL_OVERRIDE:-}" ]; then
  echo "[entrypoint] Applying HERMES_SOUL_OVERRIDE to ${SOUL_FILE}"
  printf '%s' "${HERMES_SOUL_OVERRIDE}" > "${SOUL_FILE}"
fi

# Garante permissões corretas (caso o volume tenha sido montado externamente)
chown -R hermes:hermes "${HERMES_DATA_DIR}" || true

INTERNAL_HOST="127.0.0.1"
INTERNAL_PORT="8000"
PUBLIC_HOST="0.0.0.0"
PUBLIC_PORT="${PORT:-8642}"

echo "[entrypoint] Hermes interno: ${INTERNAL_HOST}:${INTERNAL_PORT}"
echo "[entrypoint] Proxy público:  ${PUBLIC_HOST}:${PUBLIC_PORT}"
echo "[entrypoint] Iniciando Hermes original..."

# Inicia o Hermes original em background como usuário hermes
su -s /bin/bash hermes -c "cd /opt/hermes && python -m hermes_agent serve --host ${INTERNAL_HOST} --port ${INTERNAL_PORT}" &
HERMES_PID=$!

# Aguarda Hermes ficar pronto
echo "[entrypoint] Aguardando Hermes responder em ${INTERNAL_HOST}:${INTERNAL_PORT}..."
for i in $(seq 1 60); do
  if ! kill -0 "${HERMES_PID}" 2>/dev/null; then
    echo "[entrypoint] ERRO: Hermes morreu antes de ficar pronto"
    exit 1
  fi
  if curl -sf "http://${INTERNAL_HOST}:${INTERNAL_PORT}/health" > /dev/null 2>&1; then
    echo "[entrypoint] Hermes pronto!"
    break
  fi
  sleep 1
done

# Inicia o proxy / skills_api público
echo "[entrypoint] Iniciando skills_api em ${PUBLIC_HOST}:${PUBLIC_PORT}..."
exec python -m skills_api \
    --host "${PUBLIC_HOST}" \
    --port "${PUBLIC_PORT}" \
    --upstream "http://${INTERNAL_HOST}:${INTERNAL_PORT}"
