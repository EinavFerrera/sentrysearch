#!/usr/bin/env bash
# Run on the VPS as the same user GitHub Actions will SSH in as.
#   cd /opt/sentrysearch && bash scripts/vps_check_deploy_ready.sh
# Or:  APP_DIR=/srv/app bash scripts/vps_check_deploy_ready.sh
#
# Exits 1 if a hard requirement for CI deploy is missing.

set -euo pipefail
APP_DIR="${APP_DIR:-${1:-/opt/sentrysearch}}"
ERR=0

warn() { echo "WARN: $*"; }
bad() { echo "FAIL: $*"; ERR=1; }
ok() { echo "OK:  $*"; }

echo "========== VPS deploy readiness (APP_DIR=$APP_DIR) =========="
echo "User: $(whoami)  Groups: $(id -nG | tr ' ' ',')"
echo

if [[ ! -d "$APP_DIR" ]]; then
  bad "Directory missing: $APP_DIR"
  exit 1
fi

cd "$APP_DIR"

if ! git rev-parse --is-inside-work-tree &>/dev/null; then
  bad "Not a git repository: $APP_DIR"
  exit 1
fi
ok "Git repository"

ORIGIN=$(git remote get-url origin 2>/dev/null || echo "")
if [[ -z "$ORIGIN" ]]; then
  bad "No git remote 'origin'"
else
  echo "    origin = $ORIGIN"
  if [[ "$ORIGIN" == https://github.com/* ]]; then
    warn "HTTPS origin — ensure a credential helper or token allows non-interactive git pull (SSH URL is simpler)."
  fi
fi

if ! git show-ref --verify --quiet refs/heads/master; then
  bad "Local branch 'master' missing (create or: git fetch origin master && git checkout -b master origin/master)"
else
  ok "Branch master exists"
fi

echo
echo "--- Docker (CI runs: docker compose up -d --build) ---"
if id -nG | grep -qw docker; then
  ok "User is in group 'docker'"
else
  bad "User NOT in group 'docker' — run: sudo usermod -aG docker $(whoami)  then log out and back in"
fi

if ! command -v docker &>/dev/null; then
  bad "docker CLI not found"
else
  if docker info &>/dev/null; then
    ok "docker info works"
  else
    bad "docker info failed (permission or daemon)"
  fi
fi

if docker compose version &>/dev/null; then
  ok "docker compose (v2 plugin) available"
else
  bad "docker compose not found"
fi

if [[ -f docker-compose.yml ]] && docker compose config &>/dev/null; then
  ok "docker compose config succeeds"
else
  bad "docker compose config fails (fix docker-compose.yml or .env)"
fi

echo
echo "--- .env (compose reads it here) ---"
if [[ -f .env ]]; then
  if [[ -r .env ]]; then
    ok ".env exists and is readable"
  else
    bad ".env exists but is NOT readable — chown $(whoami):$(whoami) .env && chmod 600 .env"
  fi
else
  warn ".env missing — copy from .env.example before first deploy"
fi

echo
echo "--- docker-compose ports (avoid duplicate host bindings) ---"
# Count publish lines that target container port 7778 (should be exactly one)
NMAP=$(grep -E '7778:7778' docker-compose.yml 2>/dev/null | grep -v '^#' | wc -l | tr -d ' ')
if [[ "${NMAP:-0}" -gt 1 ]]; then
  bad "Multiple lines map host → 7778 in docker-compose.yml — use a single ports: entry"
elif [[ "${NMAP:-0}" -eq 0 ]]; then
  warn "No 7778:7778 mapping found — confirm ports: is set for optimus-vision"
else
  ok "One host→7778 port mapping"
fi

echo
echo "--- Git pull (same as CI) ---"
if git fetch origin master 2>/tmp/_gf.err; then
  ok "git fetch origin master"
  rm -f /tmp/_gf.err
else
  bad "git fetch origin master failed — add Deploy key or SSH access for this user (see DEPLOY-HETZNER.md)"
  sed 's/^/    /' /tmp/_gf.err || true
  rm -f /tmp/_gf.err
fi

echo
echo "========== Summary =========="
if [[ "$ERR" -ne 0 ]]; then
  echo "Fix FAIL lines above, then re-run this script."
  exit 1
fi
echo "All checks passed. Configure GitHub Secrets + DEPLOY_SSH_CONFIGURED variable, then push to master."
exit 0
