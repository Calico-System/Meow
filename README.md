![Meow](.github/assets/readme-banner.png)

# Meow

A Discord bot and provisioned IP phone dashboard.

**Birch** (the discord bot) and **Oak** (the home assistant system) make themselves known across the Calico system. Here, Birch works through a Discord bot and Oak through a 2001 Cisco 7940G desk phone that should probably be in a landfill by now. Runs on TrueNAS with Docker.

Part of [Calico](https://github.com/Calico-System), though not all of it is custom built like this.

Old phone. New tricks.

---

## Phone dashboard pages

| Page | Content |
|------|---------|
| 1 | Weather - current conditions, wind, UV, sunrise/sunset |
| 2 | BBC News - top 3 headlines |
| 3 | Economy - exchange rates, National Grid carbon intensity |
| 4 | Space - next rocket launch |
| 5 | History - random This Day in History event |
| 6 | Fun - random cat fact (up to 4 lines) + Magic 8 Ball |
| 7 | Status & Pings - all services and ping latency on one screen |
| 8 | Speedtest - last hourly speed result |
| 9 | Servers - Minecraft player count + TrueNAS pool usage |
| 10 | Discord - most recent messages per channel |
| 11 | Latest DM - messages sent to the bot by anyone |
| 12 | Priority DM - messages from designated priority users |

DMs sent to the bot appear on the phone screen for 5 minutes and light the red MWI LED.

---

## Requirements

- Cisco 7940G (tested) or 7960G
- A [SIPcord](https://sipcord.net) account (line 2)
- Docker + Docker Compose on a machine accessible from the phone's LAN
- A Discord bot token ([create one here](https://discord.com/developers))
- The Cisco SIP firmware files for `P0S3-8-12-00` (not included - source these yourself)

Line 1 on the phone registers to a local Asterisk container included in the compose file — no external account needed. Line 2 is SIPcord.

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/Calico-System/Meow.git
cd Meow
```

### 2. Configure environment

```bash
cp .env.example .env
nano .env
```

Fill in all values. See `.env.example` for descriptions of each variable.

### 3. Configure TFTP files

```bash
cp tftp/SIPDefault.cnf.example tftp/SIPDefault.cnf
cp tftp/SIP_YOURMAC_.cnf.example tftp/SIP001122334455.cnf
nano tftp/SIPDefault.cnf
nano tftp/SIP001122334455.cnf
```

Place firmware files (`.sb2`, `.bin`, `.sbn`, `.loads`, `.tar`) in the `tftp/` directory - these are not included in the repo.

The phone directory is configured via `DIRECTORY_ENTRY_*` variables in `.env` and served automatically — no static file needed.

### 4. Start the containers

```bash
docker compose up -d
```

### 5. Point the phone at the TFTP server

On the phone: **Settings → Network Configuration → TFTP Server** → enter your server's IP. The phone will reboot, pull its firmware and config, and register on both lines — line 1 to the local Asterisk container, line 2 to SIPcord.

---

## Customisation

All options are set via `.env` - no need to edit the code. See `.env.example` for the full list with descriptions.

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
| `DUCKDNS_ADDRESS` | - | DuckDNS address — used as external ping on page 7 |
| `PRIORITY_LABEL` | priority users | Label for priority users in bot messages |
| `DIRECTORY_ENTRY_1_NAME` | - | Phone directory entry name (up to 10 entries) |
| `DIRECTORY_ENTRY_1_NUMBER` | - | Phone directory entry extension number |
| `ASTERISK_AMI_SECRET` | - | Asterisk Manager Interface password |
| `ASTERISK_AMI_CALL_CHANNEL_ID` | - | Discord channel ID for call event notifications |

---

## Discord commands

### Birch
| Command | Who | Description |
|---------|-----|-------------|
| `/birchping` | Everyone | Live status - latency, rates, grid, Minecraft |
| `/birchabout` | Everyone | Who Birch is |
| `/birchhelp` | Everyone | Birch commands and DM usage |

### Meow
| Command | Who | Description |
|---------|-----|-------------|
| `/meowpage <1-12> [full]` | Everyone | Show a phone page |
| `/meowall [full]` | Owner | Show all phone pages |
| `/meowmessage <text> [duration]` | Owner | Push a custom message to the phone |
| `/meowtest` | Owner | Push calibration ruler to phone |
| `/meowstatus` | Owner | Current page and rotation state |
| `/meowrefresh` | Owner | Force regenerate all pages |
| `/meowdump` | Owner | Write pages to disk for debugging |
| `/meowpurge` | Owner | Delete all output files |
| `/meowrestart` | Owner | Restart the container to apply updated code |
| `/meowcall <extension>` | Owner | Originate a call from Oak to an extension |
| `/meowcalls` | Owner | Show active calls on the Calico PBX |
| `/meowhelp` | Everyone | Meow commands and page guide |

### Calico
| Command | Who | Description |
|---------|-----|-------------|
| `/calicoabout` | Everyone | About the Calico system |

---

## Repo structure

```
Meow/
├── bot/
│   └── fetch.py                  # Main script
├── tftp/
│   ├── OS79XX.TXT
│   ├── dialplan.xml
│   ├── SIPDefault.cnf.example
│   └── SIP_YOURMAC_.cnf.example
├── http/
│   └── logo.bmp
├── asterisk/
│   ├── asterisk.conf             # Static Asterisk directory config (committed)
│   └── modules.conf              # Static module load list (committed)
├── .github/
│   └── assets/
│       ├── meowlogo.png
│       ├── readme-banner.png
│       └── social-preview.png
├── .env.example
├── docker-compose.yml
├── .gitignore
└── README.md
```

---

## Security

Meow includes injection detection across all user input surfaces — DMs, server messages piped to the phone, and `/meowmessage`. It checks for XML injection, Cisco XML element injection, path traversal, and SQL injection patterns.

If an attempt is detected the owner receives a DM alert with the user's name, ID, source, and content. For DMs the attacker also receives a response letting them know it won't work. Server message attempts are silently filtered and flagged to the owner only.

---

## Ports

| Port | Protocol | Service | Purpose |
|------|----------|---------|---------|
| 69 | UDP | TFTP | Serves firmware and config files to the phone on boot |
| 70 | TCP | HTTP | Serves XML pages, directory, logo and health check to the phone |
| 5060 | UDP | SIPcord | External SIP — handled by the phone directly, not the server |
| 5062 | UDP/TCP | Asterisk | Internal SIP — Oak line 1, Calico component registration |
| 5038 | TCP | Asterisk AMI | Manager Interface — used internally by fetch.py only, not exposed externally |
| 10000–10020 | UDP | Asterisk RTP | Audio media streams for internal calls (supports up to 10 simultaneous) |

---

## Health check

```
http://YOUR_SERVER_IP:70/health
```

Returns JSON: `{"last_fetch_seconds_ago": 45, "ok": true}`

---

## Troubleshooting

### TFTP "Permission denied"

If the phone can't pull its firmware or config, use tcpdump on the Docker host to
check what the TFTP server is returning:

```bash
tcpdump -ni any -vv -s0 -A udp and host <PHONE_IP> and port 69
```

Look for lines containing `Permission denied` in the TFTP error response. If you
see them, the in.tftpd process cannot read the files in `/data`.

**Quick test from any Linux machine on the LAN:**

```bash
tftp <SERVER_IP> -c get SIPDefault.cnf
```

A successful transfer prints the file content. A failure prints
`Transfer timed out` or `Error code 2: Access violation`.

**Why this happens:** The host directory bound to `/data`
(`/mnt/pool/Apps/Calico/Meow/tftp`) is typically created with mode `0770`
(owner + group only). The `in.tftpd` process runs as root to bind UDP/69 but
drops privileges to an unprivileged user for file access (`--secure` mode), and
that user has no read or execute permission under `0770`.

The compose file's `entrypoint` wrapper already handles this at container startup
by running:

```
chmod 755 /data     # directory: traversable by all
chmod 644 /data/*   # all files: readable by all (covers firmware + config)
```

**TrueNAS / ZFS note:** If you use a POSIX or NFSv4 ACL on the dataset, a bare
`chmod` does not persist — the ACL overrides it on every access. To make the
fix permanent at the host level, add `other::r-x` (read + execute) to the
directory's ACL and `other::r--` to the files, or set the ZFS dataset's
`aclinherit` and `aclmode` properties to `passthrough` and rely on the
container's entrypoint to fix permissions at runtime (which it already does on
every restart).
