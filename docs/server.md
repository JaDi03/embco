# Run your own server

This guide sets up what runs behind [app.embco.xyz](https://app.embco.xyz) on a server of your
own: the dashboard, the hub (the API the dashboard uses to connect shops) and one agent per shop.
Shop owners then use your dashboard exactly as they use app.embco.xyz.

To run a single agent from the command line instead, see the README and
[Command line](cli.md).

!!! info "What you need"
    - A server with Ubuntu or Debian (tested on Ubuntu 24.04), and a user with `sudo`.
    - A domain pointing to the server, for HTTPS. To try it on your own computer, use
      `http://localhost` instead.
    - A [Circle](https://console.circle.com) account with developer-controlled wallets: an API
      key and a registered entity secret.
    - An [Anthropic](https://console.anthropic.com) API key, for the AI model that decides.
    - An Arc Testnet node URL.
    - Optional: a Telegram bot token from [@BotFather](https://t.me/BotFather).

## Install

```bash
git clone https://github.com/JaDi03/embco.git
sudo bash embco/deploy/install.sh
```

The script asks for the dashboard address, the Arc node URL, the Circle key and entity secret,
the Anthropic key and, optionally, the Telegram bot. Keys are not shown while you type them.

When it ends with `Done. Open https://...`, open that address: the dashboard asks to connect a
wallet. From here, shop owners follow [Connect your ERPNext](connect.md).

!!! warning "Keep a copy of the shop key"
    The script creates `/etc/embco/shops.key`, which encrypts each shop's ERPNext keys on disk.
    Copy it somewhere safe: without it, every shop has to connect its ERPNext again.

## Update

Run the same script again. It takes the new code, keeps your settings and restarts everything:

```bash
sudo bash /opt/embco/deploy/install.sh
```

## What the script sets up

| What | Where |
|---|---|
| The code, run by its own system user `embco`, which can only write its own data | `/opt/embco` |
| The server's settings and the shop key, readable only by root and `embco` | `/etc/embco` |
| Each shop's settings, ERPNext keys (encrypted) and journal | `/var/lib/embco/shops` |
| The hub, the Telegram bot and one agent per shop, restarted if they stop | systemd: `embco-hub`, `embco-telegram`, `embco-shop@<contract address>` |
| A rule that lets the hub start, stop and restart the shops' agents, and nothing else | polkit |
| The dashboard with HTTPS, `/api` to the hub and `/rpc` to your Arc node, whose URL stays on the server | Caddy, settings in `/etc/caddy/embco.env` |

The hub starts a shop's agent when its owner connects ERPNext in the dashboard. If Caddy already
serves other sites on the server, the script adds the dashboard next to them.

To change a setting later, edit `/etc/embco/embco.env` (the dashboard address is in
`/etc/caddy/embco.env`) and run the script again.

## Logs

```bash
journalctl -u embco-hub -f
journalctl -u 'embco-shop@*' -f
```
