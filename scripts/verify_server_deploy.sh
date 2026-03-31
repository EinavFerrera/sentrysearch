#!/usr/bin/env bash
# Run on the VPS from the app directory, e.g.:
#   cd /opt/sentrysearch && bash scripts/verify_server_deploy.sh
# If .env is root-owned and not readable by you, use:
#   cd /opt/sentrysearch && sudo bash scripts/verify_server_deploy.sh
# Paste the output into chat for a review (secrets stay redacted).

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Read .env via sudo if needed (common after sudo nano / sudo cp)
_env_cat() {
  if [[ -r .env ]]; then
    cat .env
  else
    sudo cat .env
  fi
}

echo "========== Path =========="
pwd

echo "========== .env permissions =========="
ls -la .env 2>/dev/null || echo "missing .env"
if [[ -f .env ]] && [[ ! -r .env ]]; then
  echo "NOTE: .env is not readable by this user. Compose may fail without 'sudo docker compose'."
  echo "Fix (optional): sudo chown \$USER:\$USER .env && chmod 600 .env"
fi

echo "========== docker-compose.yml =========="
cat docker-compose.yml

echo "========== Resolved compose: ports only (never share full 'docker compose config' — it embeds secrets) =========="
if sudo docker compose config >/tmp/_cc.yml 2>/dev/null; then
  awk '/^    ports:$/ { p=1; next } p && /^    [a-z_][a-z0-9_]*:$/ { exit } p { print }' /tmp/_cc.yml
  rm -f /tmp/_cc.yml
else
  echo "(sudo docker compose config failed — run from /opt/sentrysearch)"
fi

echo "========== Listeners on 7778 =========="
sudo ss -tlnp | grep 7778 || echo "(nothing listening on 7778)"

echo "========== Docker containers (optimus) =========="
sudo docker ps -a 2>/dev/null | grep -E 'CONTAINER|optimus' || true

_ENV_TMP=$(mktemp)
trap 'rm -f "$_ENV_TMP"' EXIT
if [[ -f .env ]]; then
  _env_cat >"$_ENV_TMP"
else
  : >"$_ENV_TMP"
fi

echo "========== .env variable names (values redacted) =========="
if [[ -f .env ]]; then
  grep -v '^#' "$_ENV_TMP" | grep -v '^[[:space:]]*$' | sed 's/^\([A-Za-z_][A-Za-z0-9_]*\)=.*/\1=(set)/' | sort
else
  echo "ERROR: .env missing"
fi

echo "========== Required / SSO presence =========="
check_var() {
  local k="$1"
  local req="$2"
  local line v trimmed
  line=$(grep "^${k}=" "$_ENV_TMP" 2>/dev/null | head -1 || true)
  if [[ -n "$line" ]] && [[ "$line" != "${k}=" ]]; then
    v="${line#*=}"
    trimmed="${v//[[:space:]]/}"
    if [[ -n "$trimmed" ]]; then
      echo "  OK   $k"
      return
    fi
  fi
  if [[ "$req" == "req" ]]; then
    echo "  WARN $k empty or missing (required for app)"
  else
    printf '  optional: %s\n' "$k"
  fi
}
if [[ -f .env ]]; then
  check_var GEMINI_API_KEY req
  check_var OPTIMUS_SESSION_SECRET req
  check_var OPTIMUS_SESSION_HTTPS_ONLY opt
  for k in OIDC_ISSUER OIDC_CLIENT_ID OIDC_CLIENT_SECRET OIDC_REDIRECT_URI TRUST_PROXY FORWARDED_ALLOW_IPS OIDC_BOOTSTRAP_ADMIN_EMAIL; do
    check_var "$k" opt
  done
fi

echo "========== Caddyfile (non-comment lines) =========="
if [[ -f /etc/caddy/Caddyfile ]]; then
  sudo grep -v '^[[:space:]]*#' /etc/caddy/Caddyfile | grep -v '^[[:space:]]*$' | head -40
else
  echo "(no /etc/caddy/Caddyfile)"
fi

echo "========== Done =========="
