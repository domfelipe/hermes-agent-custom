#!/bin/bash
set -e

# SOUL.md override via env
if [ -n "$HERMES_SOUL_OVERRIDE" ]; then
  echo "$HERMES_SOUL_OVERRIDE" > /opt/data/.hermes/SOUL.md
fi

# Garante diretório de skills
mkdir -p /opt/data/.hermes/skills

exec /opt/hermes/docker/entrypoint.sh "$@"
