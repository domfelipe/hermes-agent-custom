#!/bin/bash
if [ -n "$HERMES_SOUL_OVERRIDE" ]; then
  echo "$HERMES_SOUL_OVERRIDE" > /opt/data/.hermes/SOUL.md
fi
exec "$@"
