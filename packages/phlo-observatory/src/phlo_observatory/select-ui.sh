#!/bin/sh
# Select the legacy or replacement Observatory working directory before execution.
set -eu

case "${OBSERVATORY_UI:-legacy}" in
  legacy) cd "$(dirname "$0")" ;;
  replacement) cd "$(dirname "$0")/replacement" ;;
  *) echo 'OBSERVATORY_UI must be legacy or replacement.' >&2; exit 64 ;;
esac

exec "$@"
