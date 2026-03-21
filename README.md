# Birch

A general-purpose Discord bot with a built-in Cisco 7940G IP phone dashboard. The phone's idle screen auto-cycles through live pages fetched from public APIs — weather, BBC news, exchange rates, National Grid carbon intensity, rocket launches, and more. A Discord bot lets you push messages directly to the phone screen and control everything remotely.

Built and running on TrueNAS with Docker, but works on any Linux host with Docker Compose.

---

## Phone dashboard pages

| Page | Content |
|------|---------|
| 1 | Weather — current conditions, wind, UV, sunrise/sunset |
| 2 | BBC News — top 3 headlines |
| 3 | Economy — exchange rates, National Grid carbon intensity |
| 4 | Space — next rocket launch |
| 5 | History — random This Day in History event |
| 6 | Fun — random cat fact + Magic 8 Ball |
| 7 | Status & Pings — service health, ping latency |
| 8 | Speedtest — last hourly speed result |
| 9 | Servers — Minecraft player count + TrueNAS pool usage |
| 10 | Discord — most recent messages per channel |
| 11 | Latest DM — messages sent to the bot by anyone |
| 12 | Priority DM — messages from the designated priority user |

DMs sent to the bot appear on the phone screen for 5 minutes and light the red MWI LED.

---

## Requirements

- Cisco 7940G or 7960G running SIP firmware `P0S3-8-12-00`
- A [SIPcord](https://sipcord.net) account
- Docker + Docker Compose on a machine accessible from the phone's LAN
- A Discord bot token ([create one here](https://discord.com/developers))
- The Cisco SIP firmware files (not included — source these yourself)

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOURUSERNAME/birch.git
cd birch
```

### 2. Configure environment

```bash
cp dashboard/.env.example dashboard/.env
nano dashboard/.env
```

Fill in all values. See `.env.example` for descriptions of each variable.

### 3. Configure TFTP files

```bash
cp tftp/SIPDefault.cnf.example tftp/SIPDefault.cnf
cp tftp/SIP_YOURMAC_.cnf.example tftp/SIP001122334455.cnf
nano tftp/SIPDefault.cnf
nano tftp/SIP001122334455.cnf
```

Place firmware files (`.sb2`, `.bin`, `.sbn`, `.loads`) in the `tftp/` directory — these are not included in the repo.

### 4. Start the containers

```bash
cd dashboard
docker compose up -d
```

### 5. Point the phone at the TFTP server

On the phone: **Settings → Network Configuration → TFTP Server** → enter your server's IP. The phone will reboot, pull its firmware and config, and register with SIPcord.

---

## Customisation

All options are set via `.env` — no need to edit the code. See `.env.example` for the full list with descriptions. Key options:

| Variable | Default | Description |
|----------|---------|-------------|
| `IDLE_CYCLE_SECONDS` | 30 | How long each page stays on screen |
| `SPEEDTEST_INTERVAL` | 3600 | Seconds between speedtests |
| `MWI_ENABLED` | true | Toggle the phone's red LED on DMs |
| `MWI_DM_DURATION` | 300 | Seconds to keep LED lit after a DM |
| `DM_COOLDOWN_SECONDS` | 60 | Rate limit for non-priority DMs |
| `MINECRAFT_SERVER_NAME` | My Server | Server name shown on page 9 |
| `NEWS_BASE_CURRENCY` | GBP | Base currency for exchange rates |
| `PING_HOST_1_NAME/IP` | Google/8.8.8.8 | Up to 5 configurable ping targets |

---

## Discord bot commands

| Command | Who | Description |
|---------|-----|-------------|
| `/sipping` | Everyone | Live status — latency, rates, grid, Minecraft |
| `/sippage <1-12>` | Everyone | Show a page as it appears on screen |
| `/sippagefull <1-12>` | Everyone | Show a page untruncated |
| `/sipabout` | Everyone | What Birch does |
| `/siphelp` | Everyone | Full command list |
| `/siprefresh` | Owner | Force regenerate all pages |
| `/sipmessage <text>` | Owner | Push a custom message to the phone |
| `/sipstatus` | Owner | Current page and rotation state |
| `/siptest` | Owner | Push calibration ruler to screen |
| `/sipdump` | Owner | Write pages to disk for debugging |
| `/sippurge` | Owner | Delete all output files |

---

## Repo structure

```
birch/
├── dashboard/
│   ├── fetch.py                  # Main script
│   ├── docker-compose.yml
│   ├── .env                      # Your secrets — never committed
│   └── .env.example              # Template
├── tftp/
│   ├── OS79XX.TXT
│   ├── dialplan.xml
│   ├── SIPDefault.cnf.example
│   └── SIP_YOURMAC_.cnf.example
├── http/
│   └── logo.bmp
├── .gitignore
└── README.md
```

---

## Health check

```
http://YOUR_SERVER_IP:70/health
```

Returns JSON: `{"last_fetch_seconds_ago": 45, "ok": true}`
