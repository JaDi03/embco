# Run your own server

This guide sets up what runs behind [app.embco.xyz](https://app.embco.xyz) on a server of your
own: the dashboard, the hub (the API the dashboard uses to connect shops) and one agent per shop.
Shop owners then use your dashboard exactly as they use app.embco.xyz: they create their shop
contract, connect their ERPNext and set their limits there.

!!! info "What you need"
    - A server with Ubuntu or Debian (tested on Ubuntu 24.04), and a user with `sudo`.
    - A domain pointing to the server, with ports 80 and 443 open. The dashboard gets its HTTPS
      certificate on its own.
    - A [Circle](https://console.circle.com) account with developer-controlled wallets: an API
      key and a registered entity secret.
    - An [Anthropic](https://console.anthropic.com) API key, for the AI model that decides.
    - An Arc Testnet node URL.
    - Optional: a Telegram bot token from [@BotFather](https://t.me/BotFather).

## Install

```bash
git clone https://github.com/JaDi03/embco.git
cd embco
cp .env.example .env
nano .env
sudo bash deploy/install.sh
```

In `.env`, write your domain in `EMBCO_SITE` and fill in the node URL and the keys. Each line has
a short note.

The script checks `.env` before it installs anything, and says what is missing. It ends with
`Done. Open https://your-domain`: open it, and the dashboard asks to connect a wallet. From
there, shop owners follow [Connect your ERPNext](connect.md).

!!! warning "Back up the shops"
    The script creates `/etc/embco/shops.key`, which encrypts each shop's ERPNext keys on disk.
    Back it up together with the shops' data, as [below](#back-up-and-move-to-a-new-server).

## Update or change a setting

The same command, from the same folder, updates everything and keeps your settings:

```bash
cd embco
sudo bash deploy/install.sh
```

The script moves `.env` to `/etc/embco/embco.env`, so no copy with your keys stays in the
folder you cloned. To change a setting, edit that file, or write a new `.env` in the folder, and
run the command again.

## Back up and move to a new server

Everything a shop connected lives in two places: the shop key and the shops' folders (settings,
encrypted ERPNext keys, journal). The key alone is not enough, and the folders cannot be read
without it. To back up both, with the agents stopped for a moment so the journals are complete:

```bash
sudo systemctl stop 'embco-shop@*'
sudo tar czf embco-backup.tar.gz /etc/embco/shops.key /var/lib/embco/shops
sudo systemctl restart embco-hub
```

Restarting the hub starts the agents again. Keep the file off the server: it holds the shops'
ERPNext keys, encrypted with the key that travels next to them, so store it like a password.

On the new server, put the backup back before you install, then install as usual:

```bash
sudo tar xzf embco-backup.tar.gz -C /
git clone https://github.com/JaDi03/embco.git
cd embco
cp .env.example .env
nano .env
sudo bash deploy/install.sh
```

The script keeps the restored key, gives the files to the `embco` user, and the hub starts the
agents of the shops whose owners left them on. Point your domain to the new server first.

## What the script sets up

| What | Where |
|---|---|
| The code, run by its own system user `embco`, which can only write its own data | `/opt/embco` |
| The server's settings and the shop key, readable only by root and `embco` | `/etc/embco` |
| Each shop's settings, ERPNext keys (encrypted) and journal | `/var/lib/embco/shops` |
| The hub, the Telegram bot and one agent per shop, restarted if they stop | systemd: `embco-hub`, `embco-telegram`, `embco-shop@<contract address>` |
| A rule that lets the hub start, stop and restart the shops' agents, and nothing else | polkit |
| The dashboard with HTTPS, `/api` to the hub and `/rpc` to your Arc node, whose URL stays on the server | Caddy |

The hub starts a shop's agent when its owner connects ERPNext in the dashboard. If Caddy already
serves other sites on the server, the script adds the dashboard next to them.

## Logs

```bash
journalctl -u embco-hub -f
journalctl -u 'embco-shop@*' -f
```
