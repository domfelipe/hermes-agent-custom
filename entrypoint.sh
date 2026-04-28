#!/bin/bash
set -e

# ===========================================================
# Hermes Fork Entrypoint — Reverse Proxy Strategy
# ===========================================================
# 1) Hermes original roda em 127.0.0.1:8000 (interno)
# 2) skills_api.py roda em 0.0.0.0:$PORT (público)
#    - intercepta /api/skills/*
#    - proxy todo o resto para o Hermes
# ===========================================================

# Suspensão (mantém compatibilidade com a flag HERMES_SUSPENDED)
if [ "$HERMES_SUSPENDED" = "true" ]; then
  echo "[entrypoint] Agent suspended — sleeping forever"
  exec sleep infinity
fi

# Aplica SOUL override se presente
if [ -n "$HERMES_SOUL_OVERRIDE" ]; then
  echo "[entrypoint] Applying HERMES_SOUL_OVERRIDE to /opt/data/SOUL.md"
  mkdir -p /opt/data
  echo "$HERMES_SOUL_OVERRIDE" > /opt/data/SOUL.md
fi

# Porta interna do Hermes (não exposta publicamente)
export HERMES_INTERNAL_PORT="${HERMES_INTERNAL_PORT:-8000}"

# Porta pública (Railway define $PORT automaticamente)
export PUBLIC_PORT="${PORT:-8642}"

echo "[entrypoint] Hermes interno: 127.0.0.1:${HERMES_INTERNAL_PORT}"
echo "[entrypoint] Proxy público:  0.0.0.0:${PUBLIC_PORT}"

# Força o Hermes a escutar só no localhost numa porta interna
# Sobrescreve qualquer PORT que o Hermes leia
export API_SERVER_PORT="${HERMES_INTERNAL_PORT}"
export API_SERVER_HOST="127.0.0.1"

# Inicia Hermes em background
echo "[entrypoint] Iniciando Hermes original..."
PORT="${HERMES_INTERNAL_PORT}" /opt/hermes/docker/entrypoint.sh gateway run &
HERMES_PID=$!

# Aguarda Hermes ficar pronto (até 60s)
echo "[entrypoint] Aguardando Hermes responder em 127.0.0.1:${HERMES_INTERNAL_PORT}..."
for i in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${HERMES_INTERNAL_PORT}/" > /dev/null 2>&1 \
     || curl -sf "http://127.0.0.1:${HERMES_INTERNAL_PORT}/health" > /dev/null 2>&1; then
    echo "[entrypoint] Hermes está pronto!"
    break
  fi
  if ! kill -0 $HERMES_PID 2>/dev/null; then
    echo "[entrypoint] ERRO: Hermes morreu antes de ficar pronto"
    exit 1
  fi
  sleep 1
done

# Inicia o proxy + skills_api no foreground (na porta pública)
echo "[entrypoint] Iniciando proxy + skills_api em 0.0.0.0:${PUBLIC_PORT}..."
exec python3 /opt/hermes-custom/skills_api.py
