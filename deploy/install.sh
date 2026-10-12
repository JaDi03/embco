#!/usr/bin/env bash
# Installs or updates an embco server: the dashboard, the hub and the shops' agents.
# From the folder you cloned, the same command installs, updates and applies new settings:
#   sudo bash deploy/install.sh
set -euo pipefail

APP=/opt/embco
CONF=/etc/embco
SETTINGS=$CONF/embco.env
DATA=/var/lib/embco
CADDY_ENV=/etc/caddy/embco.env

say() { printf '\n== %s\n' "$*"; }
fail() { printf '\nerror: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "run it with sudo: sudo bash deploy/install.sh"
command -v apt-get >/dev/null || fail "this script needs Debian or Ubuntu (apt-get)"
command -v systemctl >/dev/null || fail "this script needs systemd"

SRC=$(cd "$(dirname "$0")/.." && pwd)

# Run from a clone: bring it up to date first, then run its new copy of this script.
if [ "$SRC" != "$APP" ] && [ -z "${EMBCO_CLONE_UPDATED:-}" ]; then
  sudo -u "$(stat -c %U "$SRC")" git -C "$SRC" pull -q --ff-only ||
    fail "could not update $SRC with git pull"
  EMBCO_CLONE_UPDATED=1 exec bash "$SRC/deploy/install.sh"
fi

# The value of KEY in an env file, without quotes or a Windows line end; empty if missing.
get() {
  sed -n "s/^$2=//p" "$1" | tail -n 1 | tr -d '\r' | sed -E "s/^['\"]//; s/['\"]\$//"
}

# Writes KEY=VALUE in an env file, replacing the line if it is there.
put() {
  python3 - "$1" "$2" "$3" <<'PY'
import pathlib, sys
path, key, value = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
lines = [line for line in path.read_text().splitlines() if not line.startswith(key + "=")]
path.write_text("\n".join(lines + [f"{key}={value}"]) + "\n")
PY
}

# A new .env next to the code is installed; without one, the installed settings are kept.
NEW=""
if [ "$SRC" != "$APP" ] && [ -f "$SRC/.env" ]; then
  NEW=$SRC/.env
elif [ ! -f "$SETTINGS" ]; then
  fail "copy .env.example to .env in $SRC, fill it in, and run this again"
fi
FROM=${NEW:-$SETTINGS}

missing=()
for key in EMBCO_SITE ARC_TESTNET_RPC_URL CIRCLE_API_KEY CIRCLE_ENTITY_SECRET ANTHROPIC_API_KEY; do
  [ -n "$(get "$FROM" "$key")" ] || missing+=("$key")
done
if [ -n "$(get "$FROM" TELEGRAM_BOT_TOKEN)" ] && [ -z "$(get "$FROM" TELEGRAM_BOT_USERNAME)" ]; then
  missing+=(TELEGRAM_BOT_USERNAME)
fi
[ ${#missing[@]} -eq 0 ] || fail "fill in ${missing[*]} in $FROM, then run this again"

site=$(get "$FROM" EMBCO_SITE)
case $site in http://*|https://*) public=$site ;; *) public="https://$site" ;; esac
if [ -f /etc/caddy/Caddyfile ] && grep -qE "^[[:space:]]*${site//./\\.}[[:space:]]*\{" /etc/caddy/Caddyfile; then
  fail "/etc/caddy/Caddyfile already has a block for $site: remove it, the install adds its own"
fi

say "1/6 Tools"
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git curl caddy polkitd python3 >/dev/null
command -v uv >/dev/null ||
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh >/dev/null

rpc=$(get "$FROM" ARC_TESTNET_RPC_URL)
chain=$(curl -s -m 15 -X POST -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' "$rpc" || true)
case $chain in *0x4cef52*) ;; *) fail "ARC_TESTNET_RPC_URL does not answer as Arc Testnet" ;; esac

say "2/6 Code"
id embco >/dev/null 2>&1 ||
  useradd --system --home-dir "$DATA" --create-home --shell /usr/sbin/nologin embco
install -d -o embco -g embco "$DATA"  # its home, also when a backup created it first
# The installed code follows the branch of the folder this runs from.
branch=$(git -c safe.directory='*' -C "$SRC" rev-parse --abbrev-ref HEAD)
if [ ! -d "$APP/.git" ]; then
  repo=$(git -c safe.directory='*' -C "$SRC" remote get-url origin)
  install -d -o embco -g embco "$APP"
  sudo -u embco git clone -q --branch "$branch" "$repo" "$APP"
fi
sudo -u embco git -C "$APP" fetch -q origin
sudo -u embco git -C "$APP" checkout -q -B "$branch" "origin/$branch"
(cd "$APP" && sudo -u embco -H uv sync -q --frozen --no-dev)
install -d -m 700 -o embco -g embco "$DATA/shops"
chown -R embco:embco "$DATA/shops"  # shops restored from a backup belong to the agent too

say "3/6 Settings"
install -d -m 750 -o root -g embco "$CONF"
if [ -n "$NEW" ]; then
  # moved, not copied: no second file with the keys is left in the clone
  tr -d '\r' < "$NEW" > "$SETTINGS.tmp"
  install -m 640 -o root -g embco "$SETTINGS.tmp" "$SETTINGS"
  rm -f "$SETTINGS.tmp" "$NEW"
  echo "Settings moved to $SETTINGS. To change them, edit that file and run this again."
fi
put "$SETTINGS" EMBCO_PUBLIC_URL "$public"

# Caddy needs the node as host and path, so a key in the node URL stays on the server.
read -r upstream path < <(python3 - "$rpc" <<'PY'
import sys
from urllib.parse import urlsplit
url = urlsplit(sys.argv[1])
print(f"{url.scheme}://{url.netloc}", (url.path or "/") + (f"?{url.query}" if url.query else ""))
PY
)
printf 'EMBCO_SITE=%s\nEMBCO_RPC_UPSTREAM=%s\nEMBCO_RPC_PATH=%s\n' "$site" "$upstream" "$path" \
  > "$CADDY_ENV.tmp"
install -m 640 -o root -g caddy "$CADDY_ENV.tmp" "$CADDY_ENV"
rm -f "$CADDY_ENV.tmp"

KEY_CREATED=0
if [ ! -f "$CONF/shops.key" ]; then
  "$APP/.venv/bin/python" -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" \
    > "$CONF/shops.key"
  KEY_CREATED=1
fi
chgrp embco "$CONF/shops.key"  # also a key restored from a backup
chmod 640 "$CONF/shops.key"

say "4/6 Services"
cp "$APP"/deploy/systemd/embco-*.service /etc/systemd/system/
cp "$APP/deploy/polkit/50-embco-shops.rules" /etc/polkit-1/rules.d/
install -d /etc/systemd/system/caddy.service.d
cp "$APP/deploy/systemd/caddy-embco.conf" /etc/systemd/system/caddy.service.d/embco.conf
systemctl daemon-reload
systemctl enable -q embco-hub
systemctl restart embco-hub
if [ -n "$(get "$SETTINGS" TELEGRAM_BOT_TOKEN)" ]; then
  systemctl enable -q embco-telegram
  systemctl restart embco-telegram
else
  systemctl disable -q --now embco-telegram 2>/dev/null || true
fi
for unit in $(systemctl list-units --plain --no-legend 'embco-shop@*' | awk '{print $1}'); do
  systemctl restart "$unit"  # shops already connected get the new code too
done

say "5/6 Dashboard"
import="import $APP/deploy/Caddyfile"
if grep -q "The Caddyfile is an easy way" /etc/caddy/Caddyfile 2>/dev/null; then
  echo "$import" > /etc/caddy/Caddyfile  # the package's welcome page: replaced
elif ! grep -qxF "$import" /etc/caddy/Caddyfile 2>/dev/null; then
  echo "$import" >> /etc/caddy/Caddyfile  # other sites already there: kept
fi
systemctl enable -q caddy
systemctl restart caddy || fail "Caddy did not start: journalctl -u caddy -n 50"

say "6/6 Check"
for _ in $(seq 30); do  # the hub takes a few seconds to start
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/api/supplier/me || true)
  [ "$code" = 401 ] && break
  sleep 1
done
[ "$code" = 401 ] || fail "the hub does not answer: journalctl -u embco-hub -n 50"
for _ in $(seq 60); do  # Caddy may still be getting the HTTPS certificate
  code=$(curl -s -o /dev/null -w '%{http_code}' "$public/api/supplier/me" || true)
  [ "$code" = 401 ] && break
  sleep 1
done
if [ "$code" != 401 ]; then
  echo "warning: $public does not answer yet. Check that $site points to this server and that"
  echo "ports 80 and 443 are open, then: journalctl -u caddy -n 50"
  exit 1
fi
echo "Done. Open $public"
if [ "$KEY_CREATED" = 1 ]; then
  echo
  echo "A new shop key was created. Save a copy in a password manager, apart from other backups:"
  echo "  sudo cat $CONF/shops.key"
fi
