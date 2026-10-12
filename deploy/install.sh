#!/usr/bin/env bash
# Installs or updates an embco server: the dashboard, the hub and the shops' agents.
# Run it from a clone of the repository: sudo bash deploy/install.sh
# It asks only for the settings that are still empty, and running it again updates the server.
set -euo pipefail

APP=/opt/embco
CONF=/etc/embco
DATA=/var/lib/embco
CADDY_ENV=/etc/caddy/embco.env

say() { printf '\n== %s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "run it with sudo: sudo bash deploy/install.sh"
command -v apt-get >/dev/null || fail "this script needs Debian or Ubuntu (apt-get)"
command -v systemctl >/dev/null || fail "this script needs systemd"

SRC=$(cd "$(dirname "$0")/.." && pwd)
git_src() { git -c safe.directory='*' -C "$SRC" "$@"; }
REPO=$(git_src remote get-url origin) || fail "run it from a clone of the repository"
BRANCH=$(git_src rev-parse --abbrev-ref HEAD)

# Reads the value of KEY in an env file; empty if missing.
get() { sed -n "s/^$2=//p" "$1" | tail -n 1; }

# Writes KEY=VALUE in an env file, replacing the line if it is there.
put() {
  python3 - "$1" "$2" "$3" <<'PY'
import pathlib, sys
path, key, value = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = path.read_text().splitlines()
if any(line.startswith(key + "=") for line in lines):
    lines = [f"{key}={value}" if line.startswith(key + "=") else line for line in lines]
else:
    lines.append(f"{key}={value}")
path.write_text("\n".join(lines) + "\n")
PY
}

# Asks for KEY only if it is still empty. secret=1 hides what is typed; optional=1 allows empty.
ask() {
  local file=$1 key=$2 question=$3 secret=${4:-0} optional=${5:-0} value
  [ -n "$(get "$file" "$key")" ] && return 0
  while true; do
    if [ "$secret" = 1 ]; then
      read -rsp "$question: " value </dev/tty; echo
    else
      read -rp "$question: " value </dev/tty
    fi
    [ -n "$value" ] || [ "$optional" = 1 ] && break
  done
  [ -n "$value" ] && put "$file" "$key" "$value"
  return 0
}

say "1/6 Tools"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl caddy polkitd python3 >/dev/null
if ! command -v uv >/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh >/dev/null
fi

say "2/6 Service user and code ($BRANCH)"
id embco >/dev/null 2>&1 ||
  useradd --system --home-dir "$DATA" --create-home --shell /usr/sbin/nologin embco
if [ -d "$APP/.git" ]; then
  sudo -u embco git -C "$APP" pull -q --ff-only
else
  install -d -o embco -g embco "$APP"
  sudo -u embco git clone -q --branch "$BRANCH" "$REPO" "$APP"
fi
(cd "$APP" && sudo -u embco -H uv sync -q --frozen --no-dev)
install -d -m 700 -o embco -g embco "$DATA/shops"

say "3/6 Settings (only what is still empty; Enter skips the optional ones)"
install -d -m 750 -o root -g embco "$CONF"
[ -f "$CONF/embco.env" ] || install -m 640 -o root -g embco "$APP/deploy/embco.env.example" "$CONF/embco.env"
[ -f "$CADDY_ENV" ] || install -m 640 -o root -g caddy "$APP/deploy/caddy.env.example" "$CADDY_ENV"
# The examples hold placeholders: a fresh copy asks for them.
[ "$(get "$CADDY_ENV" EMBCO_SITE)" = "app.example.com" ] && put "$CADDY_ENV" EMBCO_SITE ""
[ "$(get "$CONF/embco.env" EMBCO_PUBLIC_URL)" = "https://app.example.com" ] && put "$CONF/embco.env" EMBCO_PUBLIC_URL ""
[ "$(get "$CONF/embco.env" ARC_TESTNET_RPC_URL)" = "https://rpc.testnet.arc.io" ] && put "$CONF/embco.env" ARC_TESTNET_RPC_URL ""

ask "$CADDY_ENV" EMBCO_SITE "Dashboard address (a domain like app.example.com, or http://localhost)"
site=$(get "$CADDY_ENV" EMBCO_SITE)
case $site in http://*|https://*) public=$site ;; *) public="https://$site" ;; esac
[ -n "$(get "$CONF/embco.env" EMBCO_PUBLIC_URL)" ] || put "$CONF/embco.env" EMBCO_PUBLIC_URL "$public"
ask "$CONF/embco.env" ARC_TESTNET_RPC_URL "Arc Testnet node URL" 1
ask "$CONF/embco.env" CIRCLE_API_KEY "Circle API key" 1
ask "$CONF/embco.env" CIRCLE_ENTITY_SECRET "Circle entity secret" 1
ask "$CONF/embco.env" ANTHROPIC_API_KEY "Anthropic API key" 1
ask "$CONF/embco.env" TELEGRAM_BOT_TOKEN "Telegram bot token (optional)" 1 1
[ -z "$(get "$CONF/embco.env" TELEGRAM_BOT_TOKEN)" ] ||
  ask "$CONF/embco.env" TELEGRAM_BOT_USERNAME "Telegram bot username"

# The dashboard reads the same node through /rpc: Caddy needs it as host and path.
rpc=$(get "$CONF/embco.env" ARC_TESTNET_RPC_URL)
read -r upstream path < <(python3 - "$rpc" <<'PY'
import sys
from urllib.parse import urlsplit
url = urlsplit(sys.argv[1])
path = (url.path or "/") + (f"?{url.query}" if url.query else "")
print(f"{url.scheme}://{url.netloc}", path)
PY
)
put "$CADDY_ENV" EMBCO_RPC_UPSTREAM "$upstream"
put "$CADDY_ENV" EMBCO_RPC_PATH "$path"

if [ ! -f "$CONF/shops.key" ]; then
  "$APP/.venv/bin/python" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
    > "$CONF/shops.key"
  chgrp embco "$CONF/shops.key"
  chmod 640 "$CONF/shops.key"
  echo "Created $CONF/shops.key. Keep a copy: without it, every shop has to connect its ERPNext again."
fi

say "4/6 Services"
cp "$APP"/deploy/systemd/embco-*.service /etc/systemd/system/
cp "$APP/deploy/polkit/50-embco-shops.rules" /etc/polkit-1/rules.d/
install -d /etc/systemd/system/caddy.service.d
cp "$APP/deploy/systemd/caddy-embco.conf" /etc/systemd/system/caddy.service.d/embco.conf
systemctl daemon-reload
systemctl enable -q embco-hub
systemctl restart embco-hub
if [ -n "$(get "$CONF/embco.env" TELEGRAM_BOT_TOKEN)" ]; then
  systemctl enable -q embco-telegram
  systemctl restart embco-telegram
fi
# Shops already connected get the new code too.
for unit in $(systemctl list-units --plain --no-legend 'embco-shop@*' | awk '{print $1}'); do
  systemctl restart "$unit"
done

say "5/6 Dashboard"
import="import $APP/deploy/Caddyfile"
if grep -q "The Caddyfile is an easy way" /etc/caddy/Caddyfile 2>/dev/null; then
  echo "$import" > /etc/caddy/Caddyfile  # the package's welcome page: replaced
elif ! grep -qxF "$import" /etc/caddy/Caddyfile; then
  echo "$import" >> /etc/caddy/Caddyfile  # other sites already there: kept
fi
systemctl enable -q caddy
systemctl restart caddy

say "6/6 Check"
sleep 3
for unit in embco-hub caddy; do
  systemctl is-active -q "$unit" || fail "$unit did not start: journalctl -u $unit -n 50"
done
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/api/supplier/me || true)
[ "$code" = 401 ] || fail "the hub does not answer (got $code): journalctl -u embco-hub -n 50"
chain=$(curl -s -X POST -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' "$rpc" || true)
case $chain in *0x4cef52*) ;; *) fail "the Arc node did not answer as Arc Testnet: check ARC_TESTNET_RPC_URL" ;; esac
echo "Done. Open $public"
