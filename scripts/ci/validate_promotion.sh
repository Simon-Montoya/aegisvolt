#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Hace cumplir la cadena lineal de promoción (feature/* -> dev -> test -> prod).
# GitHub no puede restringir la rama ORIGEN de un PR, así que este check
# obligatorio del pipeline lo bloquea: un PR feature/x -> test queda en rojo y
# el botón de Merge no se habilita.
# -----------------------------------------------------------------------------
set -euo pipefail
source "$(dirname "$0")/detect_env.sh"

case "$TF_ENV" in
  dev)
    [[ "$SOURCE_BRANCH" == feature/* || "$SOURCE_BRANCH" == fix/* ]] || {
      echo "❌ Hacia dev solo se aceptan ramas feature/* (o fix/*). Origen: ${SOURCE_BRANCH}"; exit 1; }
    ;;
  test)
    [[ "$SOURCE_BRANCH" == "dev" || "$SOURCE_BRANCH" == "develop" ]] || {
      echo "❌ Hacia test solo se promueve desde dev. Origen: ${SOURCE_BRANCH}"; exit 1; }
    ;;
  prod)
    [[ "$SOURCE_BRANCH" == "test" ]] || {
      echo "❌ Hacia prod solo se promueve desde test. Origen: ${SOURCE_BRANCH}"; exit 1; }
    ;;
esac
echo "✅ Promoción válida: ${SOURCE_BRANCH} -> ${TARGET_BRANCH}"
