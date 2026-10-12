# Run your own server

This guide sets up what runs behind [app.embco.xyz](https://app.embco.xyz) on a server of your
own: the dashboard, the hub (the API the dashboard uses to connect shops) and one agent per shop.
Shop owners then use your dashboard exactly as they use app.embco.xyz.

To run a single agent from the command line instead, see the README and
[Command line](cli.md).

!!! info "What you need"
    - A Linux server with systemd. These steps are tested on Ubuntu 24.04.
    - A domain pointing to the server, for HTTPS (or `http://localhost` to try it on your own
      computer).
    - A [Circle](https://console.circle.com) account with developer-controlled wallets: an API
      key and a registered entity secret.
    - An [Anthropic](https://console.anthropic.com) API key, for the AI model that decides.
    - An Arc Testnet node URL.
    - Optional: a Telegram bot token from [@BotFather](https://t.me/BotFather).

All commands run as a user with `sudo`.

## Step 1: Install the tools

```bash
sudo apt update
sudo apt install -y git curl caddy polkitd python3.12
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
```

## Step 2: Create the service user and get the code

The agents run as their own user, `embco`, which owns the code and the shops' data.

```bash
sudo useradd --system --home-dir /var/lib/embco --create-home --shell /usr/sbin/nologin embco
sudo install -d -o embco -g embco /opt/embco
sudo -u embco git clone https://github.com/JaDi03/embco.git /opt/embco
cd /opt/embco
sudo -u embco -H uv sync --frozen --no-dev
sudo install -d -m 700 -o embco -g embco /var/lib/embco/shops
```

## Step 3: Write the server's settings

```bash
sudo install -d -m 750 -o root -g embco /etc/embco
sudo install -m 640 -o root -g embco deploy/embco.env.example /etc/embco/embco.env
sudo nano /etc/embco/embco.env
```

Fill in the Arc node, the Circle key and entity secret, the Anthropic key and the dashboard's
address (`EMBCO_PUBLIC_URL`). The Telegram lines are optional.

Then create the key that encrypts each shop's ERPNext keys on disk. Keep a copy somewhere safe:
without it, every shop has to connect its ERPNext again.

```bash
/opt/embco/.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" | sudo tee /etc/embco/shops.key > /dev/null
sudo chgrp embco /etc/embco/shops.key
sudo chmod 640 /etc/embco/shops.key
```

## Step 4: Start the hub and the bot

```bash
sudo cp deploy/systemd/embco-hub.service deploy/systemd/embco-shop@.service deploy/systemd/embco-telegram.service /etc/systemd/system/
sudo cp deploy/polkit/50-embco-shops.rules /etc/polkit-1/rules.d/
sudo systemctl daemon-reload
sudo systemctl enable --now embco-hub
sudo systemctl enable --now embco-telegram   # only with a Telegram bot token
```

There is nothing to start for the shops yet: the hub starts each shop's agent
(`embco-shop@<contract address>`) when its owner connects ERPNext in the dashboard. The polkit
rule lets the hub start, stop and restart those agents, and nothing else.

## Step 5: Publish the dashboard

Caddy serves the dashboard, forwards `/api` to the hub and `/rpc` to your Arc node, and gets the
HTTPS certificate for your domain.

```bash
sudo install -m 640 -o root -g caddy deploy/caddy.env.example /etc/caddy/embco.env
sudo nano /etc/caddy/embco.env
sudo install -d /etc/systemd/system/caddy.service.d
sudo cp deploy/systemd/caddy-embco.conf /etc/systemd/system/caddy.service.d/embco.conf
echo "import /opt/embco/deploy/Caddyfile" | sudo tee /etc/caddy/Caddyfile > /dev/null
sudo systemctl daemon-reload
sudo systemctl restart caddy
```

In `/etc/caddy/embco.env`, write your domain in `EMBCO_SITE` and split your Arc node URL in two:
`EMBCO_RPC_UPSTREAM` is the scheme and host, `EMBCO_RPC_PATH` the rest (`/` if there is none).

If Caddy already serves other sites on this server, add the `import` line to your Caddyfile
instead of replacing it.

## Step 6: Check it

```bash
systemctl is-active embco-hub caddy
curl -s -o /dev/null -w "%{http_code}\n" https://app.example.com/api/supplier/me
curl -s -X POST -H "content-type: application/json" \
  --data '{"jsonrpc":"2.0","id":1,"method":"eth_chainId","params":[]}' https://app.example.com/rpc
```

The hub answers `401` (nobody is signed in), and the node answers `"result":"0x4cef52"`, Arc
Testnet.

Open your domain in a browser: the dashboard asks to connect a wallet. From here, shop owners
follow [Connect your ERPNext](connect.md).

## Updates

```bash
cd /opt/embco
sudo -u embco git pull --ff-only
sudo -u embco -H uv sync --frozen --no-dev
sudo systemctl restart embco-hub embco-telegram 'embco-shop@*'
```

The dashboard is served straight from `/opt/embco/dashboard`, so it is updated by the pull.

## Logs

```bash
journalctl -u embco-hub -f
journalctl -u 'embco-shop@*' -f
```
