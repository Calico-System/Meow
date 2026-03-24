import subprocess
import sys
import os

# ═══════════════════════════════════════════════════════
# BOOTSTRAP - install dependencies before anything else
# ═══════════════════════════════════════════════════════

def bootstrap():
    print("Bootstrap: installing dependencies...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "requests", "speedtest-cli", "discord.py", "-q"],
        check=False
    )
    if result.returncode != 0:
        print("WARNING: pip install failed - some features may not work")
    subprocess.run(["apk", "add", "--no-cache", "curl"], check=False)
    result = subprocess.run(["which", "speedtest"], capture_output=True)
    if result.returncode != 0:
        print("Bootstrap: installing Ookla speedtest CLI...")
        # Download to a temp file first so we can sanity-check the content
        # before executing — avoids blindly piping an untrusted URL to sh.
        import tempfile
        try:
            dl = subprocess.run(
                ["curl", "-fsSL", "--max-filesize", "524288",
                 "https://install.speedtest.net/app/cli/install.sh"],
                capture_output=True, timeout=30
            )
            if dl.returncode != 0:
                print("Bootstrap: failed to download Ookla install script")
            else:
                script = dl.stdout
                # Basic sanity check: must start with a shebang and contain
                # expected keywords — rejects HTML error pages, redirects, etc.
                if script[:2] == b"#!" and b"speedtest" in script.lower():
                    with tempfile.NamedTemporaryFile(suffix=".sh", delete=False) as tf:
                        tf.write(script)
                        tf_path = tf.name
                    os.chmod(tf_path, 0o700)
                    subprocess.run(["sh", tf_path], check=False)
                    os.unlink(tf_path)
                else:
                    print("Bootstrap: Ookla install script failed sanity check — skipping")
        except Exception as e:
            print(f"Bootstrap: Ookla install error: {e}")
    print("Bootstrap: done.")

bootstrap()

import concurrent.futures
import requests
import random
import re
import threading
import json
import socket
import time
import uuid
import zoneinfo
import xml.etree.ElementTree as ET
import asyncio
from collections import deque
from datetime import datetime, timezone

# Suppress the urllib3 InsecureRequestWarning that would fire on every TrueNAS
# HTTPS request — we're intentionally skipping cert verification for a LAN host
# that typically uses a self-signed certificate.
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ═══════════════════════════════════════════════════════
# CONFIGURATION - all values come from environment / .env
# ═══════════════════════════════════════════════════════

OUTPUT_DIR = "/output"
TRUENAS_IP = os.environ.get("TRUENAS_IP", "192.168.4.10")
TRUENAS_KEY = os.environ.get("TRUENAS_KEY", "")
MINECRAFT_IP = os.environ.get("MINECRAFT_IP", "192.168.4.10")
MINECRAFT_PORT = int(os.environ.get("MINECRAFT_PORT", "9136"))
SERVER_IP = os.environ.get("SERVER_IP", "192.168.4.10")
HTTP_PORT = int(os.environ.get("HTTP_PORT", "70"))

PHONE_IP = os.environ.get("PHONE_IP", "192.168.4.101")
PHONE_SIP_PORT = int(os.environ.get("PHONE_SIP_PORT", "5060"))

LATITUDE = float(os.environ.get("LATITUDE", "51.2717"))
LONGITUDE = float(os.environ.get("LONGITUDE", "0.6147"))
TIMEZONE = os.environ.get("TIMEZONE", "Europe/London")
LOCATION_NAME = os.environ.get("LOCATION_NAME", "Langley Heath")

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
DISCORD_GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "")
def _parse_ids(env_key: str) -> set:
    raw = os.environ.get(env_key, "")
    return {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}

OWNER_USER_IDS   = _parse_ids("OWNER_USER_IDS")
PRIORITY_USER_IDS = _parse_ids("PRIORITY_USER_IDS") | OWNER_USER_IDS
PRIORITY_LABEL   = os.environ.get("PRIORITY_LABEL", "priority users")

_REQUIRED_ENV = {
    "DISCORD_TOKEN": DISCORD_TOKEN,
    "DISCORD_GUILD_ID": DISCORD_GUILD_ID,
    "TRUENAS_KEY": TRUENAS_KEY,
    "OWNER_USER_IDS": os.environ.get("OWNER_USER_IDS", ""),
    "PRIORITY_USER_IDS": os.environ.get("PRIORITY_USER_IDS", ""),
}
for _var, _val in _REQUIRED_ENV.items():
    if not _val:
        print(f"WARNING: {_var} is not set - related features will not work")

IDLE_CYCLE_SECONDS = int(os.environ.get("IDLE_CYCLE_SECONDS", "30"))
SPEEDTEST_INTERVAL = int(os.environ.get("SPEEDTEST_INTERVAL", "3600"))
MWI_ENABLED = os.environ.get("MWI_ENABLED", "true").lower() == "true"
MWI_DM_DURATION = int(os.environ.get("MWI_DM_DURATION", "300"))
DM_COOLDOWN_SECONDS = int(os.environ.get("DM_COOLDOWN_SECONDS", "60"))
PHONE_SIP_EXTENSION = os.environ.get("PHONE_SIP_EXTENSION", "1001")
DUCKDNS_ADDRESS = os.environ.get("DUCKDNS_ADDRESS", "")
MINECRAFT_SERVER_NAME = os.environ.get("MINECRAFT_SERVER_NAME", "Minecraft")
NEWS_BASE_CURRENCY = os.environ.get("NEWS_BASE_CURRENCY", "GBP")

# Ping hosts - configure up to 5 via PING_HOST_1_NAME / PING_HOST_1_IP etc.
# Falls back to just Google if none are set.
PING_HOSTS = {}
for _i in range(1, 6):
    _name = os.environ.get(f"PING_HOST_{_i}_NAME", "")
    _ip   = os.environ.get(f"PING_HOST_{_i}_IP", "")
    if _name and _ip:
        PING_HOSTS[_name] = _ip
if not PING_HOSTS:
    PING_HOSTS = {"Google": "8.8.8.8"}
if DUCKDNS_ADDRESS and "External" not in PING_HOSTS:
    PING_HOSTS["External"] = DUCKDNS_ADDRESS

# Directory entries - configure up to 10 via DIRECTORY_ENTRY_1_NAME / DIRECTORY_ENTRY_1_NUMBER etc.
DIRECTORY_ENTRIES = []
for _i in range(1, 11):
    _name   = os.environ.get(f"DIRECTORY_ENTRY_{_i}_NAME", "")
    _number = os.environ.get(f"DIRECTORY_ENTRY_{_i}_NUMBER", "")
    if _name and _number:
        DIRECTORY_ENTRIES.append((_name, _number))

# Asterisk AMI
ASTERISK_AMI_HOST     = os.environ.get("ASTERISK_AMI_HOST", "127.0.0.1")
ASTERISK_AMI_PORT     = int(os.environ.get("ASTERISK_AMI_PORT", "5038"))
ASTERISK_AMI_USER     = os.environ.get("ASTERISK_AMI_USER", "")
ASTERISK_AMI_SECRET   = os.environ.get("ASTERISK_AMI_SECRET", "")
ASTERISK_AMI_CHANNEL  = os.environ.get("ASTERISK_AMI_CALL_CHANNEL_ID", "")
ASTERISK_LINE1_NUMBER = os.environ.get("ASTERISK_LINE1_NUMBER", "2001")
ASTERISK_LINE1_SECRET = os.environ.get("ASTERISK_LINE1_SECRET", "")
ASTERISK_LINE1_DISPLAYNAME = os.environ.get("ASTERISK_LINE1_DISPLAYNAME", "Oak")
ASTERISK_SIP_PORT     = int(os.environ.get("ASTERISK_SIP_PORT", "5062"))
ASTERISK_RTP_START    = int(os.environ.get("ASTERISK_RTP_START", "10000"))
ASTERISK_RTP_END      = int(os.environ.get("ASTERISK_RTP_END", "10020"))
ASTERISK_CONFIG_DIR   = os.environ.get("ASTERISK_CONFIG_DIR", "/etc/asterisk")

# Additional Asterisk extensions
ASTERISK_EXTENSIONS = []
for _i in range(1, 11):
    _name    = os.environ.get(f"ASTERISK_EXTENSION_{_i}_NAME", "")
    _number  = os.environ.get(f"ASTERISK_EXTENSION_{_i}_NUMBER", "")
    _secret  = os.environ.get(f"ASTERISK_EXTENSION_{_i}_SECRET", "")
    _display = os.environ.get(f"ASTERISK_EXTENSION_{_i}_DISPLAYNAME", _name)
    if _name and _number and _secret:
        ASTERISK_EXTENSIONS.append((_name, _number, _secret, _display))

# Additional Asterisk lines (line 2–10, e.g. softphones or other SIP devices)
ASTERISK_LINES = []
for _i in range(2, 11):
    _name    = os.environ.get(f"ASTERISK_LINE{_i}_NAME", "")
    _number  = os.environ.get(f"ASTERISK_LINE{_i}_NUMBER", "")
    _secret  = os.environ.get(f"ASTERISK_LINE{_i}_SECRET", "")
    _display = os.environ.get(f"ASTERISK_LINE{_i}_DISPLAYNAME", _name)
    if _name and _number and _secret:
        ASTERISK_LINES.append((_name, _number, _secret, _display))

# ═══════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════

os.makedirs(OUTPUT_DIR, exist_ok=True)
SPEEDTEST_CACHE = os.path.join(OUTPUT_DIR, ".speedtest_cache.json")

# ── Module-level XML helpers ────────────────────────────────────────────────
_XML_DECL = '<?xml version="1.0" encoding="UTF-8"?>\n'

def _sanitize_xml(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

# Characters that are illegal in XML 1.0 (excluding the three whitespace chars
# that ARE allowed: tab \x09, LF \x0A, CR \x0D).
_XML_INVALID_CHARS_RE = re.compile(
    r'[\x00-\x08\x0b\x0c\x0e-\x1f]'
)

def _strip_invalid_xml_chars(s):
    """Remove characters that are not legal in XML 1.0 documents."""
    return _XML_INVALID_CHARS_RE.sub('', str(s))

def _to_phone_text(s):
    return _sanitize_xml(_strip_invalid_xml_chars(s)).replace("\n", "&#13;")

STATUS_CACHE = {}
PAGE_CACHE = {}
INJECTION_QUEUE = deque()  # thread-safe queue for injection alerts from sync contexts

def save_status_data(key, value):
    STATUS_CACHE[key] = value

def get_status_data():
    return dict(STATUS_CACHE)

def write_xml_refresh(filename, title, text, refresh_secs, refresh_url):
    xml = f'''{_XML_DECL}<CiscoIPPhoneText Refresh="{refresh_secs}" URL="{_sanitize_xml(refresh_url)}">
  <Title>{_sanitize_xml(title)}</Title>
  <Prompt>Updated: {datetime.now().strftime('%H:%M')}</Prompt>
  <Text>{_to_phone_text(text)}</Text>
</CiscoIPPhoneText>'''
    PAGE_CACHE[filename] = xml
    print(f"Cached {filename} (memory, refresh={refresh_secs}s)")

def write_xml(filename, title, text):
    idle_url = f"http://{SERVER_IP}:{HTTP_PORT}/idle.xml"
    xml = f"""{_XML_DECL}<CiscoIPPhoneText Refresh="{IDLE_CYCLE_SECONDS}" URL="{_sanitize_xml(idle_url)}">
  <Title>{_sanitize_xml(title)}</Title>
  <Prompt>Updated: {datetime.now().strftime('%H:%M')}</Prompt>
  <Text>{_to_phone_text(text)}</Text>
</CiscoIPPhoneText>"""
    PAGE_CACHE[filename] = xml
    print(f"Cached {filename} (memory)")

def safe_get(url, timeout=10, **kwargs):
    try:
        r = requests.get(url, timeout=timeout, **kwargs)
        r.raise_for_status()
        return r
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

def wrap(text, width=32):
    words = text.split()
    lines, line = [], ""
    for word in words:
        while len(word) > width:
            chunk = word[:width]
            if line:
                lines.append(line)
                line = ""
            lines.append(chunk)
            word = word[width:]
        if len(line) + len(word) + 1 <= width:
            line = (line + " " + word).strip()
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return "\n".join(lines)

def wrap_full(text):
    return wrap(text, 32)

_INJECTION_PATTERNS = [
    # Script/HTML injection
    "<script", "</script", "javascript:", "onerror=", "onload=",
    # XML injection
    "<?xml", "<!DOCTYPE", "<!ENTITY",
    # Cisco XML element injection
    "<Text>", "</Text>", "<Title>", "</Title>",
    "<CiscoIPPhone", "</CiscoIPPhone",
    # Path traversal
    "../", "..\\",
    # SQL - only match unambiguous patterns
    "SELECT *", "SELECT 1", "; DROP ", "; DELETE ", "; INSERT ", "; UPDATE ",
    "' OR '", '" OR "', "' OR 1", '" OR 1',
    "UNION SELECT", "UNION ALL",
]

def looks_like_injection(text: str) -> bool:
    upper = text.upper()
    return any(p.upper() in upper for p in _INJECTION_PATTERNS)

# Translation table built once at import time — faster than repeated str.replace() calls.
_PHONE_SAFE_TABLE = str.maketrans({
    ord('°'):      'deg',
    ord('£'):      'GBP',
    ord('€'):      'EUR',
    ord('\u2018'): "'",    # left single quote
    ord('\u2019'): "'",    # right single quote
    ord('\u201c'): '"',    # left double quote
    ord('\u201d'): '"',    # right double quote
    ord('\u2013'): '-',    # en dash
    ord('\u2014'): '-',    # em dash
    ord('\u2026'): '...',  # ellipsis
    ord('\u00b7'): '.',    # middle dot
    ord('\u00d7'): 'x',    # multiply sign
    ord('\u00f7'): '/',    # division sign
    ord('\u00e9'): 'e',    # é
    ord('\u00e8'): 'e',    # è
    ord('\u00ea'): 'e',    # ê
    ord('\u00e0'): 'a',    # à
    ord('\u00e1'): 'a',    # á
    ord('\u00e2'): 'a',    # â
    ord('\u00fc'): 'u',    # ü
    ord('\u00fa'): 'u',    # ú
    ord('\u00fb'): 'u',    # û
    ord('\u00f3'): 'o',    # ó
    ord('\u00f2'): 'o',    # ò
    ord('\u00f4'): 'o',    # ô
    ord('\u00ed'): 'i',    # í
    ord('\u00ec'): 'i',    # ì
    ord('\u00f1'): 'n',    # ñ
    ord('\u00e7'): 'c',    # ç
})

def phone_safe(text):
    """Replace characters the 7940G can't render with ASCII equivalents."""
    text = text.translate(_PHONE_SAFE_TABLE)
    # Strip anything still outside printable ASCII range
    return ''.join(c if 32 <= ord(c) < 127 or c == '\n' else '?' for c in text)

def _fmt_size(b):
    """Format a byte count as a human-readable size string."""
    for unit, div in [("TB", 1_099_511_627_776), ("GB", 1_073_741_824), ("MB", 1_048_576)]:
        if b >= div:
            return f"{b/div:.1f}{unit}"
    return f"{b}B"

def ping(host):
    for port in [80, 443, 53]:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            start = time.time()
            ip = socket.gethostbyname(host)
            sock.connect((ip, port))
            ms = (time.time() - start) * 1000
            return f"{ms:.0f}ms"
        except socket.timeout:
            continue
        except ConnectionRefusedError:
            ms = (time.time() - start) * 1000
            return f"{ms:.0f}ms"
        except Exception:
            continue
        finally:
            if sock is not None:
                sock.close()
    try:
        socket.gethostbyname(host)
        return "OK"
    except Exception:
        return "DOWN"

# ═══════════════════════════════════════════════════════
# ASTERISK CONFIG WRITER
# ═══════════════════════════════════════════════════════

def write_asterisk_configs():
    """Write Asterisk config files from env vars. Called at startup before Asterisk reads them."""
    if not ASTERISK_LINE1_SECRET:
        print("Asterisk: ASTERISK_LINE1_SECRET not set - skipping config generation")
        return
    try:
        os.makedirs(ASTERISK_CONFIG_DIR, exist_ok=True)

        # asterisk.conf
        asterisk_conf = """; Auto-generated by fetch.py — do not edit manually

[directories]
astetcdir => /etc/asterisk
astmoddir => /usr/lib/asterisk/modules
astvarlibdir => /var/lib/asterisk
astdbdir => /var/lib/asterisk
astkeydir => /var/lib/asterisk
astdatadir => /var/lib/asterisk
astagidir => /var/lib/asterisk/agi-bin
astspooldir => /var/spool/asterisk
astrundir => /var/run/asterisk
astlogdir => /var/log/asterisk
astsbindir => /usr/sbin
"""
        asterisk_conf_path = os.path.join(ASTERISK_CONFIG_DIR, "asterisk.conf")
        with open(asterisk_conf_path, "w") as f:
            f.write(asterisk_conf)
        os.chmod(asterisk_conf_path, 0o644)

        # pjsip.conf
        def pjsip_endpoint(name, number, secret, display):
            return f"""
; {display} ({number})
[{name}](endpoint_template)
auth={name}
aors={name}
callerid="{display}" <{number}>

[{name}](auth_template)
password={secret}
username={name}

[{name}](aor_template)
"""

        line_blocks = ""
        for name, number, secret, display in ASTERISK_LINES:
            line_blocks += pjsip_endpoint(name, number, secret, display)

        ext_blocks = ""
        for name, number, secret, display in ASTERISK_EXTENSIONS:
            ext_blocks += pjsip_endpoint(name, number, secret, display)

        pjsip = f"""; Auto-generated by fetch.py — do not edit manually

; ── Shared templates ──────────────────────────────────────────
[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:{ASTERISK_SIP_PORT}

[endpoint_template](!)
type=endpoint
context=calico
disallow=all
allow=ulaw
allow=alaw
direct_media=no
force_rport=yes
ice_support=no

[auth_template](!)
type=auth
auth_type=userpass

[aor_template](!)
type=aor
max_contacts=1
remove_existing=yes

; ── Oak — Cisco 7940G line 1 ──────────────────────────────────
[oak-line1](endpoint_template)
auth=oak-line1
aors=oak-line1
callerid="{ASTERISK_LINE1_DISPLAYNAME}" <{ASTERISK_LINE1_NUMBER}>

[oak-line1](auth_template)
password={ASTERISK_LINE1_SECRET}
username=oak-line1

[oak-line1](aor_template)
{line_blocks}
; ── Additional extensions ─────────────────────────────────────
{ext_blocks}"""
        pjsip_conf_path = os.path.join(ASTERISK_CONFIG_DIR, "pjsip.conf")
        with open(pjsip_conf_path, "w") as f:
            f.write(pjsip)
        os.chmod(pjsip_conf_path, 0o644)

        # extensions.conf
        ext_conf = """; Auto-generated by fetch.py — do not edit manually

[calico]
exten => _2XXX,1,Dial(PJSIP/${EXTEN},30)
exten => _2XXX,2,Hangup()
"""
        extensions_conf_path = os.path.join(ASTERISK_CONFIG_DIR, "extensions.conf")
        with open(extensions_conf_path, "w") as f:
            f.write(ext_conf)
        os.chmod(extensions_conf_path, 0o644)

        # rtp.conf
        rtp = f"""; Auto-generated by fetch.py — do not edit manually

[general]
rtpstart={ASTERISK_RTP_START}
rtpend={ASTERISK_RTP_END}
"""
        rtp_conf_path = os.path.join(ASTERISK_CONFIG_DIR, "rtp.conf")
        with open(rtp_conf_path, "w") as f:
            f.write(rtp)
        os.chmod(rtp_conf_path, 0o644)

        # modules.conf
        modules = """; Auto-generated by fetch.py — do not edit manually

[modules]
autoload=yes

; ── Hardware / protocol channels not present in this environment ──────────────
noload => chan_dahdi.so
noload => chan_mgcp.so
noload => chan_skinny.so
noload => chan_unistim.so
noload => chan_sip.so
noload => chan_console.so
noload => chan_iax2.so

; ── FAX — res_fax is not installed; spandsp backend depends on it ─────────────
noload => res_fax.so
noload => res_fax_spandsp.so

; ── Codec — libvorbisenc.so.2 not present in this image ──────────────────────
noload => format_ogg_vorbis.so

; ── ODBC — no res_odbc.conf; all dependents must be noloaded too ─────────────
noload => res_odbc.so
noload => res_config_odbc.so
noload => cdr_odbc.so
noload => cdr_adaptive_odbc.so
noload => cel_odbc.so
noload => func_odbc.so

; ── PostgreSQL — no res_pgsql.conf / cdr_pgsql.conf / cel_pgsql.conf ─────────
noload => res_config_pgsql.so
noload => cdr_pgsql.so
noload => cel_pgsql.so

; ── SQLite3 custom CDR/CEL — no config ───────────────────────────────────────
noload => res_config_sqlite3.so
noload => cdr_sqlite3_custom.so
noload => cel_sqlite3_custom.so

; ── LDAP realtime — no res_ldap.conf ─────────────────────────────────────────
noload => res_config_ldap.so

; ── Phone provisioning — no phoneprov.conf ───────────────────────────────────
noload => res_phoneprov.so
noload => res_pjsip_phoneprov_provider.so

; ── STUN monitor — no res_stun_monitor.conf ──────────────────────────────────
noload => res_stun_monitor.so

; ── HEP (Homer/SIPREC capture) — no hep.conf ─────────────────────────────────
noload => res_hep.so
noload => res_hep_rtcp.so
noload => res_hep_pjsip.so

; ── PJSIP notify — no pjsip_notify.conf ─────────────────────────────────────
noload => res_pjsip_notify.so

; ── Agent pool — no agents.conf ──────────────────────────────────────────────
noload => app_agent_pool.so

; ── Calendar integration — no calendar.conf ──────────────────────────────────
noload => res_calendar.so

; ── Conference bridge — no confbridge.conf ───────────────────────────────────
noload => app_confbridge.so
noload => app_page.so

; ── Call parking — no res_parking.conf ───────────────────────────────────────
noload => res_parking.so

; ── CDR/CEL backends with no config ──────────────────────────────────────────
noload => cdr_manager.so
noload => cel_manager.so
noload => cdr_custom.so
noload => cel_custom.so
noload => cdr_csv.so

; ── CLI aliases — no cli_aliases.conf ────────────────────────────────────────
noload => res_clialiases.so

; ── Voicemail — no voicemail.conf in this setup ──────────────────────────────
noload => app_voicemail.so

; ── AMD — no amd.conf ────────────────────────────────────────────────────────
noload => app_amd.so

; ── DUNDi — no dundi.conf ────────────────────────────────────────────────────
noload => pbx_dundi.so

; ── Mini voicemail — no minivm.conf ──────────────────────────────────────────
noload => app_minivm.so

; ── Follow-me — no followme.conf ─────────────────────────────────────────────
noload => app_followme.so

; ── AEL dialplan — not used ──────────────────────────────────────────────────
noload => pbx_ael.so

; ── Prometheus metrics — no prometheus.conf ──────────────────────────────────
noload => res_prometheus.so

; ── Call queues — no queues.conf ─────────────────────────────────────────────
noload => app_queue.so

; ── Alarm receiver — not used ────────────────────────────────────────────────
noload => app_alarmreceiver.so

; ── SMDI — no smdi.conf ──────────────────────────────────────────────────────
noload => res_smdi.so

; ── Deprecated modules ───────────────────────────────────────────────────────
noload => res_adsi.so
noload => app_adsiprog.so
noload => app_getcpeid.so
"""
        modules_conf_path = os.path.join(ASTERISK_CONFIG_DIR, "modules.conf")
        with open(modules_conf_path, "w") as f:
            f.write(modules)
        os.chmod(modules_conf_path, 0o644)

        # logger.conf
        logger = """; Auto-generated by fetch.py — do not edit manually

[general]

[logfiles]
console => notice,warning,error,verbose
"""
        logger_conf_path = os.path.join(ASTERISK_CONFIG_DIR, "logger.conf")
        with open(logger_conf_path, "w") as f:
            f.write(logger)
        os.chmod(logger_conf_path, 0o644)

        # stasis.conf
        # Note: 'minimum_size' was removed in newer Asterisk versions — do not add it.
        stasis = """; Auto-generated by fetch.py — do not edit manually

[threadpool]
initial_size=5
idle_timeout_sec=20
max_size=200
"""
        stasis_conf_path = os.path.join(ASTERISK_CONFIG_DIR, "stasis.conf")
        with open(stasis_conf_path, "w") as f:
            f.write(stasis)
        os.chmod(stasis_conf_path, 0o644)

        # manager.conf
        manager = f"""; Auto-generated by fetch.py — do not edit manually

[general]
enabled=yes
port={ASTERISK_AMI_PORT}
bindaddr=127.0.0.1

[{ASTERISK_AMI_USER or 'calico'}]
secret={ASTERISK_AMI_SECRET}
permit=127.0.0.1/255.255.255.255
read=all
write=all
"""
        manager_conf_path = os.path.join(ASTERISK_CONFIG_DIR, "manager.conf")
        with open(manager_conf_path, "w") as f:
            f.write(manager)
        os.chmod(manager_conf_path, 0o644)

        print(f"Asterisk: wrote configs to {ASTERISK_CONFIG_DIR}")
    except Exception as e:
        print(f"Asterisk: config write error: {e}")
        raise

# ═══════════════════════════════════════════════════════
# SPEEDTEST
# ═══════════════════════════════════════════════════════

def run_fetch_parallel(*funcs, timeout=25):
    threads = []
    for fn in funcs:
        t = threading.Thread(target=fn, daemon=True, name=fn.__name__)
        t.start()
        threads.append((fn.__name__, t))
    for name, t in threads:
        t.join(timeout=timeout)
        if t.is_alive():
            print(f"WARNING: {name} timed out after {timeout}s - abandoning")

def run_speedtest():
    print("Running speedtest...")
    tmp = SPEEDTEST_CACHE + ".tmp"
    try:
        result = subprocess.run(
            ["speedtest", "--format=json", "--accept-license", "--accept-gdpr"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            cache = {
                "download": data["download"]["bandwidth"] * 8 / 1_000_000,
                "upload": data["upload"]["bandwidth"] * 8 / 1_000_000,
                "ping": data["ping"]["latency"],
                "timestamp": datetime.now().strftime("%H:%M %d/%m")
            }
            with open(tmp, "w") as f:
                json.dump(cache, f)
            os.replace(tmp, SPEEDTEST_CACHE)
            print("Speedtest complete (Ookla)")
            return
    except Exception as e:
        print(f"Ookla speedtest error: {e}")

    try:
        result = subprocess.run(
            ["speedtest-cli", "--json"],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            cache = {
                "download": data["download"] / 1_000_000,
                "upload": data["upload"] / 1_000_000,
                "ping": data["ping"],
                "timestamp": datetime.now().strftime("%H:%M %d/%m")
            }
            with open(tmp, "w") as f:
                json.dump(cache, f)
            os.replace(tmp, SPEEDTEST_CACHE)
            print("Speedtest complete (speedtest-cli)")
            return
    except Exception as e:
        print(f"speedtest-cli error: {e}")

    print("All speedtest methods failed")

_speedtest_running = threading.Event()

def schedule_speedtest():
    if _speedtest_running.is_set():
        print("Speedtest: previous run still in progress — skipping this cycle")
    else:
        _speedtest_running.set()
        try:
            run_speedtest()
        finally:
            _speedtest_running.clear()
    threading.Timer(SPEEDTEST_INTERVAL, schedule_speedtest).start()

def get_speedtest_result():
    try:
        with open(SPEEDTEST_CACHE, "r") as f:
            return json.load(f)
    except Exception:
        return None

# ═══════════════════════════════════════════════════════
# PAGE 1: Weather
# ═══════════════════════════════════════════════════════

def fetch_page1():
    weather_text = "Weather: Unavailable"
    weather_text_full = "Weather: Unavailable"
    r = safe_get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "current": "temperature_2m,apparent_temperature,weathercode,windspeed_10m,winddirection_10m,windgusts_10m,relative_humidity_2m,pressure_msl,cloud_cover,uv_index,visibility,precipitation",
            "daily": "temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_sum,windspeed_10m_max,uv_index_max",
            "wind_speed_unit": "mph",
            "timezone": TIMEZONE,
            "forecast_days": 1
        }
    )
    if r:
        local_tz = zoneinfo.ZoneInfo(TIMEZONE)
        j = r.json()
        d = j["current"]
        dy = j.get("daily", {})
        codes = {0:"Clear",1:"Mainly Clear",2:"Partly Cloudy",
                 3:"Overcast",45:"Foggy",48:"Icy Fog",
                 51:"Lt Drizzle",53:"Drizzle",55:"Hvy Drizzle",
                 61:"Lt Rain",63:"Rain",65:"Hvy Rain",
                 71:"Lt Snow",73:"Snow",75:"Hvy Snow",
                 80:"Showers",81:"Hvy Showers",95:"Thunderstorm"}
        desc        = codes.get(d["weathercode"], "Unknown")
        temp        = d["temperature_2m"]
        feels       = d["apparent_temperature"]
        wind        = d["windspeed_10m"]
        gusts       = d["windgusts_10m"]
        humidity    = d["relative_humidity_2m"]
        pressure    = d["pressure_msl"]
        cloud       = d["cloud_cover"]
        uv          = d["uv_index"]
        vis_m       = d.get("visibility", 0)
        vis_km      = vis_m / 1000 if vis_m else 0
        precip      = d.get("precipitation", 0)
        deg = d.get("winddirection_10m", 0)
        dirs = ["N","NE","E","SE","S","SW","W","NW"]
        compass = dirs[round(deg / 45) % 8]
        t_max      = dy.get("temperature_2m_max", [None])[0]
        t_min      = dy.get("temperature_2m_min", [None])[0]
        precip_day = dy.get("precipitation_sum", [None])[0]
        uv_max     = dy.get("uv_index_max", [None])[0]

        def fmt_sun(iso):
            try:
                dt = datetime.fromisoformat(iso).astimezone(local_tz)
                return dt.strftime("%H:%M")
            except Exception:
                return "?"
        sunrise_str = fmt_sun(dy.get("sunrise", [""])[0])
        sunset_str  = fmt_sun(dy.get("sunset",  [""])[0])

        weather_text = phone_safe(f"--- WEATHER ---\n{desc}\n{temp}degC | Wind {wind}mph")
        lines = [
            "--- WEATHER ---",
            f"{desc}",
            f"Temp:    {temp}degC (feels {feels}degC)",
            f"High/Low:{t_max}degC / {t_min}degC",
            f"Wind:    {wind}mph {compass} (g{gusts}mph)",
            f"Humidity:{humidity}%  Cloud:{cloud}%",
            f"Pressure:{pressure:.0f}hPa",
            f"UV:      {uv} (max {uv_max})",
            f"Vis:     {vis_km:.1f}km  Rain:{precip}mm",
            f"Precip:  {precip_day}mm today",
            f"Sunrise: {sunrise_str}  Set:{sunset_str}",
        ]
        weather_text_full = phone_safe("\n".join(lines))

    write_xml("page1.xml", "Weather", weather_text)
    write_xml("page1_full.xml", "Weather", weather_text_full)

# ═══════════════════════════════════════════════════════
# PAGE 2: BBC News
# ═══════════════════════════════════════════════════════

def fetch_page2():
    news_text = "News: Unavailable"
    news_text_full = "News: Unavailable"
    r = safe_get("https://feeds.bbci.co.uk/news/rss.xml")
    if r:
        try:
            root = ET.fromstring(r.text)
        except ET.ParseError as e:
            print(f"ERROR: fetch_page2 - failed to parse BBC RSS feed: {e}")
            root = None
        if root is not None:
            items = root.findall(".//item")[:3]
            headlines = ["--- BBC NEWS ---"]
            headlines_full = ["--- BBC NEWS ---"]
            for i, item in enumerate(items, 1):
                title = item.find("title")
                if title is not None:
                    t = phone_safe(title.text or "")
                    t_short = t if len(t) <= 29 else t[:26] + "..."
                    headlines.append(f"{i}. {t_short}")
                    headlines_full.append(f"{i}. {wrap_full(t)}")
            if len(headlines) > 1:
                news_text = "\n".join(headlines)
                news_text_full = "\n".join(headlines_full)
                first = headlines[1][3:]
                STATUS_CACHE["news"] = f"BBC: {first.split(chr(10))[0]}"[:128]
                save_status_data("headline", first.replace("\n", " "))
            else:
                news_text_full = news_text

    write_xml("page2.xml", "BBC News", news_text)
    write_xml("page2_full.xml", "BBC News", news_text_full)

# ═══════════════════════════════════════════════════════
# PAGE 3: Economy
# ═══════════════════════════════════════════════════════

def fetch_page3():
    exchange_text = "Rates: Unavailable"
    grid_text = "Grid: Unavailable"
    grid_text_full = "Grid: Unavailable"

    def fetch_exchange():
        nonlocal exchange_text
        # frankfurter.app uses ECB reference rates, is open-source, and has no
        # documented rate limits — replacing exchangerate-api.com v4 which is
        # capped at 1500 free requests/month (our 5-min cycle uses ~8 600/month).
        # Exclude the base currency itself from the symbols list; frankfurter
        # returns an error if the base appears in the target set.
        symbols = ",".join(s for s in ["EUR", "USD"] if s != NEWS_BASE_CURRENCY)
        r = safe_get(
            "https://api.frankfurter.app/latest",
            params={"base": NEWS_BASE_CURRENCY, "symbols": symbols},
        )
        if r:
            rates = r.json().get("rates", {})
            eur = rates.get("EUR")
            usd = rates.get("USD")
            if isinstance(eur, (int, float)) and isinstance(usd, (int, float)):
                currency_symbol = "GBP" if NEWS_BASE_CURRENCY == "GBP" else NEWS_BASE_CURRENCY
                exchange_text = f"--- {NEWS_BASE_CURRENCY} RATES ---\n{currency_symbol}1 = EUR{eur:.4f}\n{currency_symbol}1 = USD{usd:.4f}"
                STATUS_CACHE["exchange"] = f"{currency_symbol}1 = EUR{eur:.2f} | USD{usd:.2f}"
                save_status_data("eur", eur)
                save_status_data("usd", usd)
            else:
                exchange_text = "Rates: Bad data"

    def fetch_grid():
        nonlocal grid_text, grid_text_full
        r = safe_get("https://api.carbonintensity.org.uk/intensity")
        if not r:
            return
        d = r.json()["data"][0]["intensity"]
        actual = d.get("actual", "N/A")
        index = d.get("index", "N/A").title()
        r2 = safe_get("https://api.carbonintensity.org.uk/generation")
        gen_lines = ""
        gen_lines_full = ""
        if r2:
            gens = r2.json().get("data", {}).get("generationmix", [])
            top2 = sorted(gens, key=lambda x: x["perc"], reverse=True)[:2]
            gen_lines = "\n" + "\n".join(
                f"  {g['fuel'].title()[:10]}: {g['perc']}%" for g in top2)
            all_gens = sorted(gens, key=lambda x: x["perc"], reverse=True)
            gen_lines_full = "\n" + "\n".join(
                f"  {g['fuel'].title()}: {g['perc']}%" for g in all_gens if g["perc"] > 0)
        grid_text = f"--- NATL GRID ---\n{actual}g/kWh ({index}){gen_lines}"
        grid_text_full = f"--- NATL GRID ---\n{actual}g/kWh ({index}){gen_lines_full}"
        STATUS_CACHE["grid"] = f"Grid: {actual}g/kWh ({index})"
        save_status_data("grid_actual", actual)
        save_status_data("grid_index", index)

    run_fetch_parallel(fetch_exchange, fetch_grid)

    write_xml("page3.xml", "Economy", f"{exchange_text}\n{grid_text}")
    write_xml("page3_full.xml", "Economy", f"{exchange_text}\n\n{grid_text_full}")

# ═══════════════════════════════════════════════════════
# PAGE 4: Space
# ═══════════════════════════════════════════════════════

def fetch_page4():
    space_text = "Space: Unavailable"
    space_text_full = "Space: Unavailable"
    r = safe_get("https://fdo.rocketlaunch.live/json/launches/next/1")
    if r:
        launches = r.json().get("result", [])
        if launches:
            d = launches[0]
            name = phone_safe(d.get("name", "Unknown"))
            win = d.get("win_open") or d.get("t0") or ""
            date_str = win[:16] if win else "TBD"
            vehicle = d.get("vehicle", {}).get("name", "")
            pad = d.get("pad", {}).get("name", "")
            details = phone_safe(d.get("launch_description") or f"{vehicle} from {pad}")
            details_short = wrap(details, 32).split("\n")[0]
            details_full = wrap_full(details)
            space_text = f"--- SPACE ---\n{name}\n{date_str}\n{details_short}"
            space_text_full = f"--- SPACE ---\n{name}\n{date_str}\n{details_full}"
            STATUS_CACHE["rocket"] = f"Next launch: {name} on {date_str}"[:128]
            save_status_data("launch_name", name)
            save_status_data("launch_date", date_str[:16])
        else:
            space_text = "--- SPACE ---\nNo upcoming\nlaunches found"
            space_text_full = space_text

    write_xml("page4.xml", "Space", space_text)
    write_xml("page4_full.xml", "Space", space_text_full)

# ═══════════════════════════════════════════════════════
# PAGE 5: History
# ═══════════════════════════════════════════════════════

def fetch_page5():
    history_text = "History: Unavailable"
    history_text_full = "History: Unavailable"
    today = datetime.now()
    # Wikipedia "On This Day" REST API — HTTPS, maintained by Wikimedia Foundation,
    # more reliable than the previous muffinlabs.com HTTP endpoint.
    r = safe_get(
        f"https://en.wikipedia.org/api/rest_v1/feed/onthisday/events"
        f"/{today.month:02d}/{today.day:02d}",
        headers={"Accept": "application/json"}
    )
    if r:
        events = r.json().get("events", [])
        if events:
            pick = random.choice(events[:20])
            year = pick.get("year", "")
            text_raw = phone_safe(pick.get("text", ""))
            text_wrapped = wrap(text_raw, 32)
            text_wrapped_full = wrap_full(text_raw)
            first_line = text_wrapped.split("\n")[0]
            history_text = f"--- THIS DAY ---\n{today.strftime('%d %B')} {year}\n{first_line}"
            history_text_full = f"--- THIS DAY ---\n{today.strftime('%d %B')} {year}\n{text_wrapped_full}"
            STATUS_CACHE["history"] = f"{today.strftime('%d %b')} {year}: {first_line}"[:128]
            save_status_data("history", f"{today.strftime('%d %b')} {year}: {text_raw.replace(chr(10), ' ')}")

    write_xml("page5.xml", "History", history_text)
    write_xml("page5_full.xml", "History", history_text_full)

# ═══════════════════════════════════════════════════════
# PAGE 6: Fun
# ═══════════════════════════════════════════════════════

def fetch_page6():
    cat_text = "Cat Fact: Unavailable"
    cat_text_full = "Cat Fact: Unavailable"
    r = safe_get("https://catfact.ninja/fact")
    if r:
        fact = phone_safe(r.json().get("fact", ""))
        fact_wrapped = wrap(fact, 32)
        fact_short = "\n".join(fact_wrapped.split("\n")[:4])
        cat_text = f"--- CAT FACT ---\n{fact_short}"
        cat_text_full = f"--- CAT FACT ---\n{wrap_full(fact)}"
        STATUS_CACHE["catfact"] = f"Cat fact: {fact_wrapped.split(chr(10))[0]}"[:128]
        save_status_data("cat_fact", fact.replace("\n", " "))

    responses = [
        "It is certain", "It is decidedly so",
        "Without a doubt", "Yes, definitely",
        "You may rely on it", "As I see it, yes",
        "Most likely", "Outlook good", "Yes",
        "Signs point to yes", "Reply hazy, try again",
        "Ask again later", "Better not tell you now",
        "Cannot predict now", "Concentrate & ask again",
        "Don't count on it", "My reply is no",
        "My sources say no", "Outlook not so good",
        "Very doubtful",
    ]
    answer = random.choice(responses)
    eight_ball_text = f"--- MAGIC 8 BALL ---\n{answer}"
    STATUS_CACHE["8ball"] = f"8ball: {answer}"
    save_status_data("eight_ball", answer)

    write_xml("page6.xml", "Fun", f"{cat_text}\n{eight_ball_text}")
    write_xml("page6_full.xml", "Fun", f"{cat_text_full}\n\n{eight_ball_text}")

# ═══════════════════════════════════════════════════════
# PAGE 7: Status & Pings
# ═══════════════════════════════════════════════════════

def fetch_page7():
    global NETWORK_ISSUE, PING_ISSUE, SERVICE_ISSUE
    _network = False
    _ping = False
    _service = False

    try:
        indicator_map = {"none": "OK", "minor": "DEGRADED",
                         "major": "MAJOR ISSUE", "critical": "DOWN"}

        # Collect results from parallel tasks via shared containers.
        ping_results = {}          # name -> latency string
        claude_ping_result = [None]
        claude_status_result = ["Unavailable"]
        discord_svc_result = ["Unavailable"]

        def do_ping_hosts():
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(PING_HOSTS)) as ex:
                futures = {ex.submit(ping, host): name for name, host in PING_HOSTS.items()}
                for future in concurrent.futures.as_completed(futures):
                    name = futures[future]
                    ping_results[name] = future.result()

        def do_claude_status():
            claude_ping_result[0] = ping("api.anthropic.com")
            r = safe_get("https://status.anthropic.com/api/v2/status.json")
            if r:
                indicator = r.json().get("status", {}).get("indicator", "none")
                claude_status_result[0] = indicator_map.get(indicator, indicator)

        def do_discord_status():
            r = safe_get("https://discordstatus.com/api/v2/status.json")
            if r:
                indicator = r.json().get("status", {}).get("indicator", "none")
                discord_svc_result[0] = indicator_map.get(indicator, indicator)

        run_fetch_parallel(do_ping_hosts, do_claude_status, do_discord_status)

        # Build ping lines in sorted order (matching the previous sequential order).
        ping_lines = ["--- PING ---"]
        for name in sorted(ping_results):
            result = ping_results[name]
            if result == "DOWN":
                _network = True
                _ping = True
            ping_lines.append(f"{name[:11]}: {result}")

        if discord_bot and not discord_bot.is_closed():
            dc_ms = round(discord_bot.latency * 1000)
            discord_latency = f"{dc_ms}ms"
        else:
            discord_latency = "DOWN"
            _network = True
            _service = True
        ping_lines.append(f"Discord bot: {discord_latency}")

        claude_ping = claude_ping_result[0] or "?"
        claude_status = claude_status_result[0]
        discord_svc_status = discord_svc_result[0]

        if claude_status not in ("OK", "Unavailable"):
            _network = True
            _service = True
        if discord_svc_status not in ("OK", "Unavailable"):
            _network = True
            _service = True

        cache = get_speedtest_result()
        speed_lines = ["--- SPEEDTEST ---"]
        if cache:
            speed_lines.append(f"Download: {cache['download']:.1f} Mbps")
            speed_lines.append(f"Upload:   {cache['upload']:.1f} Mbps")
            speed_lines.append(f"Ping:     {cache['ping']:.0f} ms")
            speed_lines.append(f"At:       {cache['timestamp']}")
        else:
            speed_lines.append("Running first test...")
            speed_lines.append("Check back in 2 mins")

        claude_line = (
            f"Claude: {claude_ping}" if claude_status == "OK"
            else f"Claude: {claude_status} ({claude_ping})" if claude_status not in ("DOWN", "Unavailable")
            else f"Claude: {claude_status}"
        )
        discord_line = (
            f"Discord: {discord_latency}" if discord_svc_status == "OK" and discord_latency != "DOWN"
            else f"Discord: {discord_svc_status} ({discord_latency})" if discord_svc_status not in ("DOWN", "Unavailable") and discord_latency != "DOWN"
            else f"Discord: {discord_svc_status if discord_svc_status != 'OK' else 'DOWN'}"
        )
        all_status_lines = [
            "--- STATUS ---",
            claude_line,
            discord_line,
        ] + ping_lines[1:]  # append all ping hosts (skip "--- PING ---" header)

        page7_lines = all_status_lines[:7]  # cap at 7 for auto version
        page7_full_lines = all_status_lines  # full version shows everything
        write_xml("page7.xml", "Status & Pings", "\n".join(page7_lines))
        write_xml("page7_full.xml", "Status & Pings", "\n".join(page7_full_lines))

        write_xml("page8.xml", "Speedtest", "\n".join(speed_lines))
        write_xml("page8_full.xml", "Speedtest", "\n".join(speed_lines))

    except Exception as e:
        print(f"ERROR: fetch_page7 crashed: {e}")
        write_xml("page7.xml", "Status & Pings", f"--- STATUS ---\nError fetching data:\n{str(e)[:60]}")
        write_xml("page7_full.xml", "Status & Pings", f"--- STATUS ---\nError fetching data:\n{str(e)[:60]}")
        write_xml("page8.xml", "Speedtest", "--- SPEEDTEST ---\nUnavailable")
        write_xml("page8_full.xml", "Speedtest", "--- SPEEDTEST ---\nUnavailable")

    finally:
        NETWORK_ISSUE = _network
        PING_ISSUE = _ping
        SERVICE_ISSUE = _service

# ═══════════════════════════════════════════════════════
# PAGE 9: Servers (Minecraft + TrueNAS)
# ═══════════════════════════════════════════════════════

# Legacy Server List Ping request (Minecraft ≥ 1.4, still honoured by all
# modern servers for backward compatibility).
_MC_LEGACY_PING_REQUEST  = b'\xfe\x01'
# Response always starts with 0xFF followed by a 2-byte UTF-16 char count.
_MC_LEGACY_PING_RESPONSE = 0xff

def fetch_page9():
    global MC_HAS_PLAYERS
    mc_lines = [f"--- {MINECRAFT_SERVER_NAME.upper()} ---"]
    sock2 = None
    try:
        sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock2.settimeout(3)
        sock2.connect((MINECRAFT_IP, MINECRAFT_PORT))
        # Legacy Server List Ping (still supported by all modern Minecraft servers)
        sock2.sendall(_MC_LEGACY_PING_REQUEST)
        # Read until we have the full response: 0xFF + 2-byte char count + UTF-16BE payload
        data = b''
        while len(data) < 3:
            chunk = sock2.recv(1024)
            if not chunk:
                break
            data += chunk
        if data and data[0] == _MC_LEGACY_PING_RESPONSE and len(data) >= 3:
            char_count = (data[1] << 8) | data[2]
            total_expected = 3 + char_count * 2
            while len(data) < total_expected:
                chunk = sock2.recv(1024)
                if not chunk:
                    break
                data += chunk
        if data and len(data) > 3 and data[0] == _MC_LEGACY_PING_RESPONSE:
            try:
                raw = data[3:].decode('utf-16-be', errors='ignore')
                parts = raw.split('\x00')
                nums = [p for p in parts if p.strip().lstrip('-').isdigit()]
                if len(nums) >= 2:
                    online = int(nums[-2])
                    max_p = int(nums[-1])
                    MC_HAS_PLAYERS = online > 0
                    mc_lines.append("Online")
                    mc_lines.append(f"Players: {online}/{max_p}")
                    STATUS_CACHE["minecraft"] = f"{MINECRAFT_SERVER_NAME}: {online}/{max_p} online"
                    save_status_data("mc_online", online)
                    save_status_data("mc_max", max_p)
                else:
                    MC_HAS_PLAYERS = False
                    mc_lines.append("Online")
                    mc_lines.append("Players: 0/?")
            except Exception as e:
                mc_lines.append("Online")
                mc_lines.append(f"Parse error: {str(e)[:15]}")
        else:
            mc_lines.append("Online")
            mc_lines.append("Players: ?/?")
    except ConnectionRefusedError:
        MC_HAS_PLAYERS = False
        mc_lines.append("Offline")
    except Exception as e:
        MC_HAS_PLAYERS = False
        mc_lines.append(f"Error: {str(e)[:20]}")
    finally:
        if sock2 is not None:
            try:
                sock2.close()
            except Exception:
                pass

    nas_lines = ["--- TRUENAS ---"]
    try:
        headers = {"Authorization": f"Bearer {TRUENAS_KEY}"}
        r = requests.get(
            f"https://{TRUENAS_IP}/api/v2.0/pool",
            headers=headers, timeout=5, verify=False
        )
        if r.status_code == 200:
            pools = r.json()
            for pool in pools:
                name = pool.get("name", "unknown")
                status = pool.get("status", "unknown")
                r2 = requests.get(
                    f"https://{TRUENAS_IP}/api/v2.0/pool/dataset",
                    headers=headers, timeout=5, verify=False,
                    params={"name": name}
                )
                if r2.status_code == 200:
                    datasets = r2.json()
                    if datasets:
                        used = datasets[0].get("used", {}).get("parsed", 0)
                        avail = datasets[0].get("available", {}).get("parsed", 0)
                        total = used + avail
                        pct = (used / total * 100) if total > 0 else 0

                        nas_lines.append(f"{name}: {status}")
                        nas_lines.append(f"{_fmt_size(used)} / {_fmt_size(total)}")
                        nas_lines.append(f"Used: {pct:.0f}%")
                else:
                    nas_lines.append(f"{name}: {status}")
        else:
            nas_lines.append(f"API error {r.status_code}")
    except Exception as e:
        nas_lines.append(f"Error: {str(e)[:20]}")

    text = "\n".join(mc_lines) + "\n" + "\n".join(nas_lines)
    text_full = "\n".join(mc_lines) + "\n\n" + "\n".join(nas_lines)
    write_xml("page9.xml", "Servers", text)
    write_xml("page9_full.xml", "Servers", text_full)

# ═══════════════════════════════════════════════════════
# PAGE 10: Discord Recent Messages
# ═══════════════════════════════════════════════════════

_fetch_page10_lock = threading.Semaphore(1)

def fetch_page10():
    if not _fetch_page10_lock.acquire(blocking=False):
        print("fetch_page10: already running — skipping this trigger")
        return
    try:
        _fetch_page10_impl()
    finally:
        _fetch_page10_lock.release()

def _fetch_page10_impl():
    headers = {
        "Authorization": f"Bot {DISCORD_TOKEN}",
        "Content-Type": "application/json"
    }

    r = safe_get(
        f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/channels",
        headers=headers
    )
    if not r:
        write_xml("page10.xml", "Discord", "Discord: Unavailable")
        write_xml("page10_full.xml", "Discord", "Discord: Unavailable")
        return

    channels = r.json()
    text_channels = [c for c in channels if c.get("type") == 0]

    def fetch_channel(channel):
        cid = channel["id"]
        cname = phone_safe(channel.get("name", "unknown"))
        try:
            r2 = requests.get(
                f"https://discord.com/api/v10/channels/{cid}/messages?limit=1",
                headers=headers, timeout=10
            )
            if r2.status_code in (403, 404):
                return None
            if r2.status_code != 200:
                return None
            msgs = r2.json()
        except Exception:
            return None
        if not msgs or not isinstance(msgs, list) or len(msgs) == 0:
            return None
        msg = msgs[0]
        if msg.get("author", {}).get("bot", False):
            return None
        content_raw = phone_safe(msg.get("content", ""))
        author = phone_safe(msg.get("author", {}).get("username", "Unknown"))
        author_id = msg.get("author", {}).get("id", 0)
        timestamp = msg.get("timestamp", "")
        if not content_raw and msg.get("attachments"):
            content_raw = "[attachment]"
        if not content_raw and msg.get("embeds"):
            content_raw = "[embed]"
        if not content_raw:
            return None
        injection_attempt = looks_like_injection(msg.get("content", ""))
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            local_tz = zoneinfo.ZoneInfo(TIMEZONE)
            dt_local = dt.astimezone(local_tz)
            ts_display = dt_local.strftime("%H:%M %d/%m")
        except Exception:
            ts_display = "?"
            dt = datetime.min.replace(tzinfo=timezone.utc)

        return {
            "name": cname,
            "author": author,
            "author_id": author_id,
            "content": content_raw,
            "ts_display": ts_display,
            "dt": dt,
            "injection": injection_attempt,
        }

    # Fetch all channels in parallel using a thread pool
    if not text_channels:
        write_xml("page10.xml", "Discord", "No recent messages found")
        write_xml("page10_full.xml", "Discord", "No recent messages found")
        return

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(text_channels), 20)) as ex:
        futures = [ex.submit(fetch_channel, ch) for ch in text_channels]
        # as_completed(timeout=10) imposes a 10-second total wall-clock budget for
        # collecting all results, not a per-future limit.  This is intentional:
        # we want the whole parallel fetch to finish within 10 seconds so that a
        # single slow channel cannot stall the page update.
        completed = concurrent.futures.as_completed(futures, timeout=10)
        all_results = []
        try:
            for f in completed:
                result = f.result()
                if result is not None:
                    all_results.append(result)
        except concurrent.futures.TimeoutError:
            pass

    # Fire silent alerts for any injection attempts, exclude from phone display
    for ch in all_results:
        if ch.get("injection"):
            INJECTION_QUEUE.append({
                "type": "silent",
                "user_name": ch["author"],
                "user_id": ch["author_id"],
                "source": f"#{ch['name']} (server)",
                "content": ch["content"],
            })
    channel_data = [ch for ch in all_results if not ch.get("injection")]

    if not channel_data:
        write_xml("page10.xml", "Discord", "No recent messages found")
        write_xml("page10_full.xml", "Discord", "No recent messages found")
        return

    try:
        channel_data.sort(key=lambda x: x["dt"], reverse=True)
    except Exception:
        pass

    lines = ["--- DISCORD ---"]

    if channel_data:
        c = channel_data[0]
        line2 = f"#{c['name'][:13]} {c['ts_display']}"
        author_short = c['author'][:12]
        msg = c['content'].replace("\n", " ")
        line3 = f"{author_short}: {msg}"
        if len(line3) > 32:
            line3 = line3[:29] + "..."
        lines.append(line2)
        lines.append(line3)

    if len(channel_data) > 1:
        c2 = channel_data[1]
        lines.append(f"#{c2['name'][:20]}")
        lines.append(c2['ts_display'])

    lines_full = ["--- DISCORD ---"]
    for c in channel_data[:3]:
        msg_full = wrap_full(c['content'].replace("\n", " "))
        lines_full.append(f"#{c['name']} {c['ts_display']}")
        lines_full.append(msg_full)
        lines_full.append("")

    write_xml("page10.xml", "Discord", "\n".join(lines))
    write_xml("page10_full.xml", "Discord", "\n".join(lines_full).rstrip())

# ═══════════════════════════════════════════════════════
# DISCORD BOT
# ═══════════════════════════════════════════════════════

STATUS_KEYS = ["exchange", "grid", "history", "catfact", "8ball", "minecraft", "news", "rocket"]

discord_bot = None

# ═══════════════════════════════════════════════════════
# ASTERISK AMI
# ═══════════════════════════════════════════════════════

ACTIVE_CALLS = {}        # channel -> {extension, start_time, callerid}
AMI_CONNECTED = False
_ami_socket = None
_ami_lock = threading.Lock()
_ami_event_callbacks = []  # list of async coroutines to call on events

def ami_connect():
    """Connect to Asterisk AMI and log in. Returns socket or None."""
    global _ami_socket, AMI_CONNECTED
    if not ASTERISK_AMI_USER or not ASTERISK_AMI_SECRET:
        return None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((ASTERISK_AMI_HOST, ASTERISK_AMI_PORT))
        sock.settimeout(None)
        sock.recv(1024)  # banner
        login = (
            f"Action: Login\r\n"
            f"Username: {ASTERISK_AMI_USER}\r\n"
            f"Secret: {ASTERISK_AMI_SECRET}\r\n"
            f"\r\n"
        )
        sock.sendall(login.encode())
        resp = sock.recv(1024).decode(errors="ignore")
        if "Success" not in resp:
            print(f"AMI login failed: {resp.strip()}")
            sock.close()
            return None
        _ami_socket = sock
        AMI_CONNECTED = True
        print("AMI connected to Asterisk")
        return sock
    except Exception as e:
        print(f"AMI connect error: {e}")
        AMI_CONNECTED = False
        return None

def ami_send(action: str):
    """Send a raw AMI action string."""
    with _ami_lock:
        if _ami_socket:
            try:
                _ami_socket.sendall(action.encode())
                return True
            except Exception as e:
                print(f"AMI send error: {e}")
    return False

def ami_originate(extension: str, callerid: str = "Calico <2001>") -> bool:
    """Originate a call from the phone's line 1 to an extension."""
    # Strip anything that isn't a valid dialstring character to prevent
    # CRLF injection into the raw AMI protocol stream.
    extension = re.sub(r"[^0-9A-Za-z+#*@._-]", "", extension)
    if not extension:
        print("ami_originate: empty extension after sanitisation — aborting")
        return False
    action = (
        f"Action: Originate\r\n"
        f"Channel: PJSIP/oak-line1\r\n"
        f"Exten: {extension}\r\n"
        f"Context: calico\r\n"
        f"Priority: 1\r\n"
        f"CallerID: {callerid}\r\n"
        f"Timeout: 30000\r\n"
        f"Async: yes\r\n"
        f"\r\n"
    )
    return ami_send(action)

def _parse_ami_event(raw: str) -> dict:
    """Parse a single AMI event block into a dict."""
    event = {}
    for line in raw.strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            event[key.strip()] = val.strip()
    return event

def ami_event_loop(loop):
    """Background thread: reads AMI events and fires callbacks."""
    global AMI_CONNECTED
    backoff = 5
    while True:
        sock = ami_connect()
        if not sock:
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            continue
        backoff = 5
        buf = ""
        try:
            while True:
                data = sock.recv(4096).decode(errors="ignore")
                if not data:
                    break
                buf += data
                while "\r\n\r\n" in buf:
                    block, buf = buf.split("\r\n\r\n", 1)
                    event = _parse_ami_event(block)
                    if not event.get("Event"):
                        continue
                    ev = event["Event"]

                    # Track active calls
                    channel = event.get("Channel", "")
                    if ev == "Dial" and event.get("SubEvent") == "Begin":
                        ACTIVE_CALLS[channel] = {
                            "extension": event.get("Dialstring", "?"),
                            "callerid": event.get("CallerIDNum", "?"),
                            "start": datetime.now(),
                        }
                    elif ev in ("Hangup", "HangupRequest") and channel in ACTIVE_CALLS:
                        ACTIVE_CALLS.pop(channel, None)

                    # Fire async callbacks (call events to Discord)
                    if ev in ("Dial", "Hangup", "Bridge"):
                        for cb in _ami_event_callbacks:
                            asyncio.run_coroutine_threadsafe(cb(event), loop)
        except Exception as e:
            print(f"AMI event loop error: {e}")
        finally:
            AMI_CONNECTED = False
            try:
                sock.close()
            except Exception:
                pass
        print("AMI disconnected - reconnecting...")
        time.sleep(backoff)

def start_ami(loop):
    """Start the AMI event loop in a background thread."""
    if not ASTERISK_AMI_USER or not ASTERISK_AMI_SECRET:
        print("AMI: ASTERISK_AMI_USER/SECRET not set - skipping")
        return
    t = threading.Thread(target=ami_event_loop, args=(loop,), daemon=True, name="ami_event_loop")
    t.start()
    print("AMI event loop thread started")

def start_discord_bot():
    import discord
    from discord import app_commands
    import asyncio

    async def owner_only(interaction: discord.Interaction) -> bool:
        if interaction.user.id not in OWNER_USER_IDS:
            await interaction.response.send_message(embed=discord.Embed(title="Only the owner can use this command.", color=discord.Color.red()), ephemeral=True)
            return False
        return True

    async def alert_injection(user: discord.User | discord.Member, source: str, content: str):
        """DM the owner and send a laughing response to the attacker."""
        # Warn the attacker
        try:
            await user.send(embed=discord.Embed(
                title="\U0001f923 Nice try.",
                description="That won\u2019t work here.",
                color=discord.Color.red()
            ))
        except Exception:
            pass
        # Alert the owner
        for owner_id in OWNER_USER_IDS:
            try:
                owner = await client.fetch_user(owner_id)
                embed = discord.Embed(
                    title="\u26a0\ufe0f Injection attempt detected",
                    color=discord.Color.red()
                )
                embed.add_field(name="Source", value=source, inline=True)
                embed.add_field(name="User", value=f"{user.name} (`{user.id}`)", inline=True)
                embed.add_field(name="Content", value=f"```\n{content[:900]}\n```", inline=False)
                await owner.send(embed=embed)
            except Exception:
                pass

    async def alert_injection_silent(user_name: str, user_id: int, source: str, content: str):
        """Silently DM the owner only — used for server messages where we don't want to call out the user publicly."""
        for owner_id in OWNER_USER_IDS:
            try:
                owner = await client.fetch_user(owner_id)
                embed = discord.Embed(
                    title="\u26a0\ufe0f Injection attempt detected (server message)",
                    color=discord.Color.red()
                )
                embed.add_field(name="Source", value=source, inline=True)
                embed.add_field(name="User", value=f"{user_name} (`{user_id}`)", inline=True)
                embed.add_field(name="Content", value=f"```\n{content[:900]}\n```", inline=False)
                await owner.send(embed=embed)
            except Exception:
                pass

    intents = discord.Intents.default()
    intents.message_content = True
    intents.messages = True
    client = discord.Client(intents=intents)
    tree = app_commands.CommandTree(client)

    global discord_bot
    discord_bot = client

    def redact(text):
        redactions = {
            TRUENAS_IP: "##.##.##.##",
            MINECRAFT_IP: "##.##.##.##",
            SERVER_IP: "##.##.##.##",
            PHONE_IP: "##.##.##.##",
            str(LATITUDE): "##.####",
            str(LONGITUDE): "#.####",
            LOCATION_NAME: "######",
        }
        if DUCKDNS_ADDRESS:
            redactions[DUCKDNS_ADDRESS] = "####.duckdns.org"
        for ip in PING_HOSTS.values():
            redactions[ip] = "##.##.##.##"
        for val, rep in redactions.items():
            text = text.replace(val, rep)
        return text

    def build_status_embed():
        latency = round(client.latency * 1000)
        embed = discord.Embed(title="Meow", color=discord.Color.from_rgb(253, 105, 0))
        embed.add_field(name="Latency", value=f"`{latency}ms`", inline=True)
        if "exchange" in STATUS_CACHE:
            embed.add_field(name="Rates", value=STATUS_CACHE["exchange"], inline=True)
        if "grid" in STATUS_CACHE:
            embed.add_field(name="Grid", value=STATUS_CACHE["grid"], inline=True)
        if "rocket" in STATUS_CACHE:
            embed.add_field(name="Next Launch", value=STATUS_CACHE["rocket"], inline=False)
        data = get_status_data()
        cat = data.get("cat_fact") or STATUS_CACHE.get("catfact", "")
        if cat:
            embed.add_field(name="Cat Fact", value=cat, inline=False)
        if "minecraft" in STATUS_CACHE:
            embed.add_field(name="Minecraft", value=STATUS_CACHE["minecraft"], inline=True)
        embed.set_footer(text=f"DM me to show a message on the phone screen for 5 mins | {PRIORITY_LABEL.capitalize()} gets top priority")
        return embed

    @tree.command(name="birchping", description="Live status: bot latency, exchange rates, grid, Minecraft")
    async def slash_ping(interaction: discord.Interaction):
        await interaction.response.send_message(embed=build_status_embed())

    @tree.command(name="meowrefresh", description="Force regenerate all 12 phone pages immediately")
    async def slash_refresh(interaction: discord.Interaction):
        if not await owner_only(interaction): return
        embed = discord.Embed(title="Refreshing all pages...", color=discord.Color.from_rgb(253, 105, 0))
        await interaction.response.send_message(embed=embed)
        try:
            dm_funcs = []
            if DM_MESSAGE is not None:
                dm_funcs.append(fetch_page11)
            if DM_MESSAGE_PRIORITY is not None:
                dm_funcs.append(fetch_page12)
            run_fetch_parallel(fetch_page1, fetch_page2, fetch_page3, fetch_page4,
                               fetch_page5, fetch_page6, fetch_page7, fetch_page9,
                               fetch_page10, *dm_funcs)
            write_menus()
            write_idle_cycle()
            await interaction.followup.send(embed=discord.Embed(title="All pages refreshed!", color=discord.Color.green()))
        except Exception as e:
            await interaction.followup.send(embed=discord.Embed(title="Error", description=str(e), color=discord.Color.red()))

    @tree.command(name="meowdump", description="Write all in-memory pages to disk for debugging (auto-deleted after 6 hours)")
    async def slash_dump(interaction: discord.Interaction):
        if not await owner_only(interaction): return
        embed = discord.Embed(title="Dumping pages to disk...", color=discord.Color.from_rgb(253, 105, 0))
        await interaction.response.send_message(embed=embed)
        try:
            written = dump_to_disk()
            embed = discord.Embed(
                title=f"Dumped {len(written)} files to disk",
                description="\n".join(written),
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=discord.Embed(title="Error", description=str(e), color=discord.Color.red()))

    @tree.command(name="meowpurge", description="Delete all XML and JSON output files from disk")
    async def slash_purge(interaction: discord.Interaction):
        if not await owner_only(interaction): return
        embed = discord.Embed(title="Purging output files...", color=discord.Color.from_rgb(253, 105, 0))
        await interaction.response.send_message(embed=embed)
        try:
            removed = []
            for fname in os.listdir(OUTPUT_DIR):
                fpath = os.path.join(OUTPUT_DIR, fname)
                if fname.endswith(".xml") or (fname.endswith(".json") and fname != os.path.basename(SPEEDTEST_CACHE)):
                    os.remove(fpath)
                    removed.append(fname)
            if removed:
                embed = discord.Embed(title=f"Removed {len(removed)} files", description="\n".join(removed), color=discord.Color.green())
            else:
                embed = discord.Embed(title="Nothing to remove", color=discord.Color.green())
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=discord.Embed(title="Error", description=str(e), color=discord.Color.red()))

    @tree.command(name="meowrestart", description="Restart the Meow container to apply updated code")
    async def slash_restart(interaction: discord.Interaction):
        if not await owner_only(interaction): return
        embed = discord.Embed(
            title="Restarting...",
            description="Container will restart now. Back in a few seconds.",
            color=discord.Color.from_rgb(253, 105, 0)
        )
        await interaction.response.send_message(embed=embed)
        # Give discord.py time to flush the response before hard-exiting.
        # threading.Timer runs outside the event loop so it won't be cancelled
        # when the loop exits — the container restart will fire reliably.
        write_asterisk_configs()
        threading.Timer(2, lambda: os._exit(0)).start()

    @tree.command(name="birchabout", description="Who Birch is")
    async def slash_birchabout(interaction: discord.Interaction):
        embed = discord.Embed(
            title="Birch",
            description=(
                "I'm Birch. I keep an eye on things.\n"
                "Weather, news, exchange rates, rocket launches, the grid, your servers - I keep track.\n"
                "DM me and I'll put it on the screen. Voice messages coming soon.\n"
                "Part of Calico."
            ),
            color=discord.Color.from_rgb(253, 105, 0)
        )
        embed.set_footer(text="github.com/Calico-System/Meow")
        await interaction.response.send_message(embed=embed)

    PAGE_MAP = {
        1:  ("page1.xml",    "page1_full.xml",    "Weather",          "Current local conditions (temp, wind, weather)"),
        2:  ("page2.xml",    "page2_full.xml",    "BBC News",         "Top 3 headlines from BBC News"),
        3:  ("page3.xml",    "page3_full.xml",    "Economy",          "GBP/EUR and GBP/USD exchange rates plus National Grid carbon intensity"),
        4:  ("page4.xml",    "page4_full.xml",    "Space",            "Next upcoming rocket launch"),
        5:  ("page5.xml",    "page5_full.xml",    "History",          "Random This Day in History event"),
        6:  ("page6.xml",    "page6_full.xml",    "Fun",              "Random cat fact plus a Magic 8 Ball answer"),
        7:  ("page7.xml",    "page7_full.xml",    "Status & Pings",   "Claude and Discord service status plus ping latency"),
        8:  ("page8.xml",    "page8_full.xml",    "Speedtest",        "Download/upload speed and ping from last hourly speedtest"),
        9:  ("page9.xml",    "page9_full.xml",    "Servers",          "Minecraft player count and TrueNAS pool usage"),
        10: ("page10.xml",   "page10_full.xml",   "Discord Messages", "Most recent messages from each Discord text channel"),
        11: ("page11.xml",   "page11_full.xml",   "Latest DM",        "Most recent DM from non-priority users, shown for 5 mins"),
        12: ("page12.xml",   "page12_full.xml",   "Priority DM",      "Most recent DM from the priority user. Highest priority"),
    }

    LOCKED_PAGES = {1, 11, 12}

    def get_page_text(page_num, full=False):
        if page_num not in PAGE_MAP:
            return None
        auto_filename, full_filename, fallback_title, _ = PAGE_MAP[page_num]
        filename = full_filename if full else auto_filename
        if filename not in PAGE_CACHE:
            filename = auto_filename if full else full_filename
        if filename not in PAGE_CACHE:
            return fallback_title, "Page not yet generated. Try /meowrefresh"
        try:
            root = ET.fromstring(PAGE_CACHE[filename])
            title = root.findtext("Title") or fallback_title
            text = root.findtext("Text") or ""
            text = text.replace("&#13;", "\n")
            return title, redact(text)
        except Exception as e:
            return fallback_title, f"Error reading page: {e}"

    @tree.command(name="meowpage", description="Show a phone page (1-12)")
    @app_commands.describe(page="Page number 1-12", full="Show full untruncated version (default: screen version)")
    async def slash_page(interaction: discord.Interaction, page: app_commands.Range[int, 1, 12], full: bool = False):
        if page in LOCKED_PAGES and interaction.user.id not in OWNER_USER_IDS:
            await interaction.response.send_message(embed=discord.Embed(title="That page is private.", color=discord.Color.red()), ephemeral=True)
            return
        result = get_page_text(page, full=full)
        if not result:
            await interaction.response.send_message(embed=discord.Embed(title=f"Page {page} not found", color=discord.Color.red()), ephemeral=True)
            return
        title, text = result
        label = "full" if full else "screen"
        colour = discord.Color.from_rgb(253, 105, 0)
        embed = discord.Embed(title=f"{page}. {title} ({label})", description=f"```\n{text[:4000]}\n```", color=colour)
        await interaction.response.send_message(embed=embed)

    @tree.command(name="meowall", description="Show all phone pages")
    @app_commands.describe(full="Show full untruncated versions (default: screen versions)")
    async def slash_all(interaction: discord.Interaction, full: bool = False):
        if not await owner_only(interaction): return
        label = "full" if full else "screen"
        await interaction.response.send_message(embed=discord.Embed(title=f"Fetching all pages ({label})...", color=discord.Color.from_rgb(253, 105, 0)))
        for num in sorted(PAGE_MAP.keys()):
            if num in LOCKED_PAGES and interaction.user.id not in OWNER_USER_IDS:
                continue
            result = get_page_text(num, full=full)
            if not result:
                continue
            title, text = result
            colour = discord.Color.from_rgb(253, 105, 0)
            suffix = " (full)" if full else ""
            embed = discord.Embed(title=f"{num}. {title}{suffix}", description=f"```\n{text[:4000]}\n```", color=colour)
            await interaction.followup.send(embed=embed)

    @tree.command(name="meowtest", description="Push a calibration ruler to the phone screen")
    async def slash_test(interaction: discord.Interaction):
        if not await owner_only(interaction): return
        lines = [
            "--- CALIBRATION ---",
            "1234567890123456789012345678901234567890",
            "10chars---|",
            "15chars--------|",
            "20chars-------------|",
            "25chars------------------|",
            "30chars-------------------------|",
            "32chars---------------------------|",
        ]
        text = "\n".join(lines)
        idle_url = f"http://{SERVER_IP}:{HTTP_PORT}/idle.xml"
        write_xml_refresh("calibration.xml", "Calibration", text, 300, idle_url)
        write_idle_cycle_immediate("calibration.xml")
        await interaction.response.send_message(embed=discord.Embed(
            title="Calibration page pushed",
            description="Check the phone screen. Page auto-returns to idle after 5 minutes.",
            color=discord.Color.from_rgb(253, 105, 0)
        ))

    @tree.command(name="meowmessage", description="Push a custom message to the phone screen for a set duration")
    @app_commands.describe(
        text="The message to display on the phone screen",
        duration="How long to show it in seconds (default 300, max 3600)"
    )
    async def slash_message(interaction: discord.Interaction, text: str, duration: app_commands.Range[int, 10, 3600] = 300):
        if not await owner_only(interaction): return
        if looks_like_injection(text):
            await alert_injection(interaction.user, "/meowmessage", text)
            await interaction.response.send_message(embed=discord.Embed(title="Nice try. \U0001f923", color=discord.Color.red()), ephemeral=True)
            return
        idle_url = f"http://{SERVER_IP}:{HTTP_PORT}/idle.xml"
        body = wrap_full(text)
        write_xml_refresh("custommsg.xml", "Message", f"--- MESSAGE ---\n{body}", duration, idle_url)
        write_idle_cycle_immediate("custommsg.xml")
        await interaction.response.send_message(embed=discord.Embed(
            title="Message pushed to phone",
            description=f"```\n{text[:500]}\n```\nShowing for {duration}s, then returning to idle.",
            color=discord.Color.green()
        ))

    @tree.command(name="meowstatus", description="Show what page the phone is currently displaying")
    async def slash_status(interaction: discord.Interaction):
        if not await owner_only(interaction): return
        now = datetime.now()
        active_pages = _get_active_pages()
        slot = (now.minute * 60 + now.second) // IDLE_CYCLE_SECONDS
        current_page = active_pages[slot % len(active_pages)]
        next_slot = slot + 1
        next_page = active_pages[next_slot % len(active_pages)]
        secs_until_next = IDLE_CYCLE_SECONDS - (now.minute * 60 + now.second) % IDLE_CYCLE_SECONDS

        dm_status = "None"
        if DM_MESSAGE_PRIORITY:
            age = int((now - DM_MESSAGE_PRIORITY["time"]).total_seconds())
            dm_status = f"VIP DM ({age}s ago)" if age < MWI_DM_DURATION else f"VIP DM (expired, {age}s ago)"
        elif DM_MESSAGE:
            age = int((now - DM_RECEIVED_AT).total_seconds())
            dm_status = f"DM ({age}s ago)" if age < MWI_DM_DURATION else f"DM (expired, {age}s ago)"

        embed = discord.Embed(title="Phone Status", color=discord.Color.from_rgb(253, 105, 0))
        embed.add_field(name="Current page", value=f"`{current_page}`", inline=True)
        embed.add_field(name="Next page", value=f"`{next_page}` in {secs_until_next}s", inline=True)
        embed.add_field(name="Rotation", value=f"{len(active_pages)} pages active", inline=True)
        embed.add_field(name="DM state", value=dm_status, inline=True)
        embed.add_field(name="Network issue", value="Yes" if NETWORK_ISSUE else "No", inline=True)
        embed.add_field(name="MC players", value="Yes" if MC_HAS_PLAYERS else "No", inline=True)
        last_fetch = f"{int(time.time() - LAST_FETCH_TIME)}s ago" if LAST_FETCH_TIME else "Never"
        embed.add_field(name="Last fetch", value=last_fetch, inline=True)
        await interaction.response.send_message(embed=embed)

    @tree.command(name="birchhelp", description="Birch commands and DM usage")
    async def slash_birchhelp(interaction: discord.Interaction):
        commands_public = (
            "`/birchping` - live status and latency\n"
            "`/birchabout` - who Birch is\n"
            "`/birchhelp` - this message\n"
            "`/meowhelp` - Meow system commands"
        )
        advanced = (
            "DM Birch to show a message on the phone screen for 5 minutes.\n"
            f"{PRIORITY_LABEL.capitalize()} go to page 12 and override everything.\n"
            "All other DMs go to page 11 and override normal rotation.\n"
            "Network issues force page 7 (Status & Pings).\n"
            "Page 10 (Discord) updates within 30s of any new server message."
        )
        embed = discord.Embed(title="Birch", color=discord.Color.from_rgb(253, 105, 0))
        embed.add_field(name="Commands", value=commands_public, inline=False)
        embed.add_field(name="DM Usage", value=advanced, inline=False)
        await interaction.response.send_message(embed=embed)

    @tree.command(name="meowhelp", description="Meow system commands and phone page guide")
    async def slash_meowhelp(interaction: discord.Interaction):
        is_owner = interaction.user.id in OWNER_USER_IDS
        page_key = "\n".join(
            f"{n:2}. {title:<18} {desc}"
            for n, (_, _f, title, desc) in sorted(PAGE_MAP.items())
            if n not in LOCKED_PAGES or is_owner
        )
        commands_public = (
            "`/meowpage <1-12> [full]` - show a phone page (add full:True for untruncated)\n"
            "`/meowhelp` - this message"
        )
        commands_owner = (
            "`/meowall [full]` - show all phone pages (add full:True for untruncated)\n"
            "`/meowrefresh` - regenerate all pages immediately\n"
            "`/meowtest` - push calibration ruler to phone\n"
            "`/meowmessage <text> [duration]` - push custom message to phone\n"
            "`/meowstatus` - current page and rotation state\n"
            "`/meowdump` - write pages to disk for debugging\n"
            "`/meowpurge` - delete all output files\n"
            "`/meowrestart` - restart the container to apply updated code\n"
            "`/meowcall <extension>` - originate a call from Oak to an extension\n"
            "`/meowcalls` - show active calls on the Calico PBX"
        )
        embed = discord.Embed(title="Meow", color=discord.Color.from_rgb(253, 105, 0))
        embed.add_field(name="Page Key", value=f"```\n{page_key}\n```", inline=False)
        embed.add_field(name="Commands", value=commands_public, inline=False)
        if is_owner:
            embed.add_field(name="Owner Commands", value=commands_owner, inline=False)
        await interaction.response.send_message(embed=embed)

    @tree.command(name="meowcall", description="Originate a call from Oak to an extension")
    @app_commands.describe(extension="Extension number to dial")
    async def slash_call(interaction: discord.Interaction, extension: str):
        if not await owner_only(interaction): return
        if not AMI_CONNECTED:
            await interaction.response.send_message(embed=discord.Embed(
                title="Asterisk not connected",
                description="AMI is unavailable — check the Asterisk container.",
                color=discord.Color.red()
            ), ephemeral=True)
            return
        success = ami_originate(extension)
        if success:
            await interaction.response.send_message(embed=discord.Embed(
                title=f"Calling {extension}",
                description="Oak's phone should ring shortly.",
                color=discord.Color.from_rgb(253, 105, 0)
            ))
        else:
            await interaction.response.send_message(embed=discord.Embed(
                title="Call failed",
                description="AMI originate command failed.",
                color=discord.Color.red()
            ), ephemeral=True)

    @tree.command(name="meowcalls", description="Show active calls on the Calico PBX")
    async def slash_calls(interaction: discord.Interaction):
        if not await owner_only(interaction): return
        if not ACTIVE_CALLS:
            await interaction.response.send_message(embed=discord.Embed(
                title="No active calls",
                color=discord.Color.from_rgb(253, 105, 0)
            ))
            return
        embed = discord.Embed(title="Active calls", color=discord.Color.from_rgb(253, 105, 0))
        for channel, info in ACTIVE_CALLS.items():
            duration = int((datetime.now() - info["start"]).total_seconds())
            mins, secs = divmod(duration, 60)
            embed.add_field(
                name=f"{info['callerid']} → {info['extension']}",
                value=f"`{mins:02d}:{secs:02d}` | `{channel}`",
                inline=False
            )
        await interaction.response.send_message(embed=embed)

    async def on_ami_event(event: dict):
        """Post call events to the designated Discord channel."""
        if not ASTERISK_AMI_CHANNEL:
            return
        try:
            ch = await client.fetch_channel(int(ASTERISK_AMI_CHANNEL))
        except Exception:
            return
        ev = event.get("Event", "")
        channel = event.get("Channel", "")
        callerid = event.get("CallerIDNum", "?")
        exten = event.get("Dialstring") or event.get("Exten", "?")

        if ev == "Dial" and event.get("SubEvent") == "Begin":
            embed = discord.Embed(
                title="\U0001f4de Call started",
                description=f"`{callerid}` → `{exten}`",
                color=discord.Color.from_rgb(253, 105, 0)
            )
        elif ev == "Hangup" and channel:
            cause = event.get("Cause-txt", "Normal")
            duration = ""
            if channel in ACTIVE_CALLS:
                secs = int((datetime.now() - ACTIVE_CALLS[channel]["start"]).total_seconds())
                mins, s = divmod(secs, 60)
                duration = f" ({mins:02d}:{s:02d})"
            embed = discord.Embed(
                title="\U0001f4f4 Call ended",
                description=f"`{channel}`{duration} — {cause}",
                color=discord.Color.from_rgb(253, 105, 0)
            )
        elif ev == "Bridge":
            embed = discord.Embed(
                title="\U0001f501 Call bridged",
                description=f"`{event.get('Channel1', '?')}` ↔ `{event.get('Channel2', '?')}`",
                color=discord.Color.from_rgb(253, 105, 0)
            )
        else:
            return

        try:
            await ch.send(embed=embed)
        except Exception as e:
            print(f"AMI Discord post error: {e}")

    @tree.command(name="calicoabout", description="About the Calico system")
    async def slash_calicoabout(interaction: discord.Interaction):
        embed = discord.Embed(
            title="Calico",
            description=(
                "Calico is a personal home lab system.\n"
                "Birch watches and reports. Oak speaks and listens.\n"
                "Meow is how they show up here - through Discord and a 2001 desk phone.\n"
                "Old phone. New tricks."
            ),
            color=discord.Color.red()
        )
        embed.set_footer(text="github.com/Calico-System/Meow")
        await interaction.response.send_message(embed=embed)

    async def cycle_status():
        await client.wait_until_ready()
        idx = 0
        while not client.is_closed():
            available = [k for k in STATUS_KEYS if k in STATUS_CACHE]
            if available:
                key = available[idx % len(available)]
                status_text = STATUS_CACHE[key]
                await client.change_presence(
                    activity=discord.Activity(
                        type=discord.ActivityType.watching,
                        name=status_text[:128]
                    )
                )
                idx += 1
            await asyncio.sleep(300)

    async def drain_injection_queue():
        """Process injection alerts queued from sync contexts (e.g. fetch_page10 threads)."""
        while not client.is_closed():
            while INJECTION_QUEUE:
                item = INJECTION_QUEUE.popleft()
                if item["type"] == "silent":
                    await alert_injection_silent(
                        item["user_name"], item["user_id"],
                        item["source"], item["content"]
                    )
            await asyncio.sleep(5)

    @client.event
    async def on_ready():
        print(f"Discord bot connected as {client.user}")
        await tree.sync()
        print("Slash commands synced")
        asyncio.ensure_future(cycle_status())
        asyncio.ensure_future(drain_injection_queue())
        _ami_event_callbacks.append(on_ami_event)
        start_ami(asyncio.get_event_loop())
        threading.Thread(target=fetch_page10, daemon=True).start()

    @client.event
    async def on_message(message):
        global DM_MESSAGE, DM_RECEIVED_AT, DM_MESSAGE_PRIORITY, LAST_PAGE7_UPDATE
        if message.author.bot:
            return

        if message.guild is not None:
            now_ts = time.time()
            if now_ts - LAST_PAGE7_UPDATE >= 30:
                LAST_PAGE7_UPDATE = now_ts
                threading.Thread(target=fetch_page10, daemon=True).start()
            return

        if message.author.id not in PRIORITY_USER_IDS:
            if looks_like_injection(message.content or ""):
                await alert_injection(message.author, "DM", message.content or "")
                return
            # Cooldown check: read and write under the same lock to prevent
            # a TOCTOU race where two near-simultaneous DMs both pass the check.
            now_dt = datetime.now()
            with _DM_LOCK:
                last_dm = DM_COOLDOWNS.get(message.author.id)
                if last_dm and (now_dt - last_dm).total_seconds() < DM_COOLDOWN_SECONDS:
                    remaining = DM_COOLDOWN_SECONDS - int((now_dt - last_dm).total_seconds())
                    # Release lock before awaiting I/O
                    do_cooldown = True
                else:
                    DM_COOLDOWNS[message.author.id] = now_dt
                    do_cooldown = False
            if do_cooldown:
                await message.reply(embed=discord.Embed(
                    title="Slow down!",
                    description=f"You can send another message in {remaining}s.",
                    color=discord.Color.red()
                ))
                return
            # Prune stale entries opportunistically (non-blocking)
            threading.Thread(target=_prune_cooldowns, daemon=True).start()

        if looks_like_injection(message.content or ""):
            await alert_injection(message.author, "DM", message.content or "")
            return

        dm_entry = {
            "author": message.author.name,
            "author_id": message.author.id,
            "text": phone_safe(message.content or "[attachment]"),
            "time": datetime.now(),
        }
        if message.author.id in PRIORITY_USER_IDS:
            with _DM_LOCK:
                DM_MESSAGE_PRIORITY = dm_entry
            fetch_page12()
            write_idle_cycle_immediate("page12.xml", hold_secs=MWI_DM_DURATION)
            if AMI_CONNECTED:
                ami_originate(ASTERISK_LINE1_NUMBER, f"Priority DM <{ASTERISK_LINE1_NUMBER}>")
        else:
            with _DM_LOCK:
                DM_MESSAGE = dm_entry
                DM_RECEIVED_AT = datetime.now()
            fetch_page11()
            write_idle_cycle_immediate("page11.xml", hold_secs=MWI_DM_DURATION)
        threading.Thread(target=update_mwi, daemon=True).start()
        print(f"DM from {message.author.name}: {message.content[:50]}")
        received = dm_entry["time"].strftime("%H:%M %d/%m")
        wrapped = wrap_full(dm_entry["text"])
        page = "page12" if message.author.id in PRIORITY_USER_IDS else "page11"
        screen_text = f"--- DM RECEIVED ---\nFrom: {message.author.name}\nAt: {received}\n\n{wrapped}"
        embed = discord.Embed(
            title=f"Showing on {page}",
            description=f"```\n{screen_text}\n```",
            color=discord.Color.green()
        )
        await message.reply(embed=embed)

    def run_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # discord.py's reconnect=True handles transient disconnects automatically inside client.start()
        # For fatal errors (bad token, etc.) we retry with backoff
        backoff = 5
        while True:
            try:
                print("Discord bot: connecting...")
                loop.run_until_complete(client.start(DISCORD_TOKEN, reconnect=True))
            except Exception as e:
                print(f"Discord bot fatal error: {e} - retrying in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)
            else:
                print("Discord bot: clean shutdown")
                break

    thread = threading.Thread(target=run_bot, daemon=True)
    thread.start()
    print("Discord bot thread started")

# ═══════════════════════════════════════════════════════
# PAGES 11 + 12: DM Notifications
# ═══════════════════════════════════════════════════════

def fetch_page11():
    if DM_MESSAGE:
        author = phone_safe(DM_MESSAGE["author"])
        text = phone_safe(DM_MESSAGE["text"])
        received = DM_MESSAGE["time"].strftime("%H:%M %d/%m")
        text_short = "\n".join(wrap_full(text).split("\n")[:4])
        body_auto = f"--- DM RECEIVED ---\nFrom: {author}\nAt: {received}\n{text_short}"
        body_full = f"--- DM RECEIVED ---\nFrom: {author}\nAt: {received}\n\n{wrap_full(text)}"
    else:
        body_auto = "--- DM ---\nNo messages yet"
        body_full = body_auto
    idle_url = f"http://{SERVER_IP}:{HTTP_PORT}/idle.xml"
    write_xml_refresh("page11.xml", "DM", body_auto, MWI_DM_DURATION, idle_url)
    write_xml_refresh("page11_full.xml", "DM", body_full, MWI_DM_DURATION, idle_url)

def fetch_page12():
    if DM_MESSAGE_PRIORITY:
        author = phone_safe(DM_MESSAGE_PRIORITY["author"])
        text = phone_safe(DM_MESSAGE_PRIORITY["text"])
        received = DM_MESSAGE_PRIORITY["time"].strftime("%H:%M %d/%m")
        text_short = "\n".join(wrap_full(text).split("\n")[:4])
        body_auto = f"--- DM: {author.upper()} ---\nAt: {received}\n{text_short}"
        body_full = f"--- DM: {author.upper()} ---\nAt: {received}\n\n{wrap_full(text)}"
    else:
        body_auto = "--- VIP DM ---\nNo messages yet"
        body_full = body_auto
    idle_url = f"http://{SERVER_IP}:{HTTP_PORT}/idle.xml"
    write_xml_refresh("page12.xml", "VIP DM", body_auto, MWI_DM_DURATION, idle_url)
    write_xml_refresh("page12_full.xml", "VIP DM", body_full, MWI_DM_DURATION, idle_url)

# ═══════════════════════════════════════════════════════
# MENUS
# ═══════════════════════════════════════════════════════

def write_menus():
    base = f"http://{SERVER_IP}:{HTTP_PORT}"

    info_menu = f"""{_XML_DECL}<CiscoIPPhoneMenu>
  <Title>Info Services</Title>
  <Prompt>Select a page</Prompt>
  <MenuItem>
    <Name>Weather</Name>
    <URL>{base}/page1_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <Name>BBC News</Name>
    <URL>{base}/page2_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <Name>Economy</Name>
    <URL>{base}/page3_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <Name>Space</Name>
    <URL>{base}/page4_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <Name>History</Name>
    <URL>{base}/page5_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <Name>Fun</Name>
    <URL>{base}/page6_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <Name>Status &amp; Pings</Name>
    <URL>{base}/page7_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <Name>Speedtest</Name>
    <URL>{base}/page8_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <Name>Servers</Name>
    <URL>{base}/page9_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <Name>Discord Activity</Name>
    <URL>{base}/page10_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <Name>Latest DM</Name>
    <URL>{base}/page11_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <Name>Priority DM</Name>
    <URL>{base}/page12_full.xml</URL>
  </MenuItem>
</CiscoIPPhoneMenu>"""
    PAGE_CACHE["infoservices.xml"] = info_menu
    print("Cached infoservices.xml (memory)")

    main_menu = f"""{_XML_DECL}<CiscoIPPhoneMenu>
  <Title>Meow Services</Title>
  <Prompt>Select an option</Prompt>
  <MenuItem>
    <Name>Info Services</Name>
    <URL>{base}/infoservices.xml</URL>
  </MenuItem>
</CiscoIPPhoneMenu>"""
    PAGE_CACHE["services.xml"] = main_menu
    print("Cached services.xml (memory)")

    entries_xml = ""
    for name, number in DIRECTORY_ENTRIES:
        safe_name   = _sanitize_xml(name)
        safe_number = _sanitize_xml(number)
        entries_xml += f"  <DirectoryEntry>\n    <Name>{safe_name}</Name>\n    <Telephone>{safe_number}</Telephone>\n  </DirectoryEntry>\n"
    if not entries_xml:
        entries_xml = "  <DirectoryEntry>\n    <Name>No entries configured</Name>\n    <Telephone>0</Telephone>\n  </DirectoryEntry>\n"
    directory = f"""{_XML_DECL}<CiscoIPPhoneDirectory>
  <Title>Directory</Title>
  <Prompt>Select to dial</Prompt>
{entries_xml}</CiscoIPPhoneDirectory>"""
    PAGE_CACHE["directory.xml"] = directory
    print("Cached directory.xml (memory)")

# ═══════════════════════════════════════════════════════
# GLOBAL FLAGS
# ═══════════════════════════════════════════════════════

MC_HAS_PLAYERS = False
NETWORK_ISSUE = False
PING_ISSUE = False
SERVICE_ISSUE = False
MWI_STATE = False
LAST_FETCH_TIME = 0
LAST_PAGE7_UPDATE = 0
DM_MESSAGE = None
DM_RECEIVED_AT = None
DM_MESSAGE_PRIORITY = None
DM_COOLDOWNS = {}
# Protects all DM_* globals and DM_COOLDOWNS against concurrent mutation from
# the Discord async thread and the fetch/MWI threads.
_DM_LOCK = threading.Lock()


def _prune_cooldowns():
    """Remove cooldown entries older than DM_COOLDOWN_SECONDS to prevent unbounded growth."""
    now = datetime.now()
    with _DM_LOCK:
        expired = [uid for uid, ts in DM_COOLDOWNS.items()
                   if (now - ts).total_seconds() >= DM_COOLDOWN_SECONDS]
        for uid in expired:
            del DM_COOLDOWNS[uid]

# ═══════════════════════════════════════════════════════
# MWI - Message Waiting Indicator
# ═══════════════════════════════════════════════════════

def send_mwi(waiting: bool):
    global MWI_STATE
    if not MWI_ENABLED:
        return
    if waiting == MWI_STATE:
        return
    state = "yes" if waiting else "no"
    msgs = "1/0 (0/0)" if waiting else "0/0 (0/0)"
    call_id = str(uuid.uuid4())
    body = f"Messages-Waiting: {state}\r\nVoice-Message: {msgs}\r\n"
    notify = (
        f"NOTIFY sip:{PHONE_SIP_EXTENSION}@{PHONE_IP}:{PHONE_SIP_PORT} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP {SERVER_IP}:5060;branch=z9hG4bK{call_id[:8]}\r\n"
        f"From: <sip:dashboard@{SERVER_IP}>;tag={call_id[:8]}\r\n"
        f"To: <sip:{PHONE_SIP_EXTENSION}@{PHONE_IP}>\r\n"
        f"Call-ID: {call_id}\r\n"
        f"CSeq: 1 NOTIFY\r\n"
        f"Event: message-summary\r\n"
        f"Subscription-State: active;expires=3600\r\n"
        f"Content-Type: application/simple-message-summary\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"\r\n"
        f"{body}"
    )
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2)
        sock.sendto(notify.encode(), (PHONE_IP, PHONE_SIP_PORT))
        sock.close()
        MWI_STATE = waiting
        print(f"MWI {'ON' if waiting else 'OFF'} sent to {PHONE_IP}")
    except Exception as e:
        print(f"MWI send error: {e}")

def update_mwi():
    now = datetime.now()
    dm_active = DM_RECEIVED_AT and (now - DM_RECEIVED_AT).total_seconds() < MWI_DM_DURATION
    priority_active = DM_MESSAGE_PRIORITY and (now - DM_MESSAGE_PRIORITY["time"]).total_seconds() < MWI_DM_DURATION
    send_mwi(bool(dm_active or priority_active))

def schedule_mwi_clear():
    try:
        update_mwi()
    except Exception as e:
        print(f"WARNING: update_mwi failed: {e}")
    threading.Timer(60, schedule_mwi_clear).start()

# ═══════════════════════════════════════════════════════
# IDLE CYCLE
# ═══════════════════════════════════════════════════════

def write_idle_cycle_immediate(page, hold_secs=None):
    base = f"http://{SERVER_IP}:{HTTP_PORT}"
    secs = hold_secs if hold_secs is not None else IDLE_CYCLE_SECONDS
    content = f"""{_XML_DECL}<CiscoIPPhoneText Refresh="{secs}" URL="{base}/{page}">
  <Title>Meow</Title>
  <Prompt>New message!</Prompt>
  <Text>Loading...</Text>
</CiscoIPPhoneText>"""
    PAGE_CACHE["idle.xml"] = content
    print(f"Cached idle.xml in memory (immediate switch to {page}, hold {secs}s)")

def _naive(dt):
    """Strip timezone info from a datetime so it can be compared with datetime.now()."""
    return dt.replace(tzinfo=None) if (dt is not None and dt.tzinfo is not None) else dt

def _get_active_pages():
    now = datetime.now()
    if DM_MESSAGE_PRIORITY:
        dm_time = _naive(DM_MESSAGE_PRIORITY["time"])
        if (now - dm_time).total_seconds() < MWI_DM_DURATION:
            return ["page12.xml"]
    if DM_RECEIVED_AT:
        received_at = _naive(DM_RECEIVED_AT)
        if (now - received_at).total_seconds() < MWI_DM_DURATION:
            return ["page11.xml"]
    if NETWORK_ISSUE:
        return ["page7.xml"]
    pages = ["page1.xml", "page2.xml", "page3.xml", "page4.xml",
             "page5.xml", "page6.xml", "page7.xml", "page8.xml", "page10.xml"]
    if MC_HAS_PLAYERS:
        pages.append("page9.xml")
    if DM_MESSAGE_PRIORITY:
        dm_time = _naive(DM_MESSAGE_PRIORITY["time"])
        if (now - dm_time).total_seconds() >= MWI_DM_DURATION:
            pages.append("page12.xml")
    if DM_RECEIVED_AT:
        received_at = _naive(DM_RECEIVED_AT)
        if (now - received_at).total_seconds() >= MWI_DM_DURATION:
            pages.append("page11.xml")
    return pages

def write_cycle_ring():
    base = f"http://{SERVER_IP}:{HTTP_PORT}"
    active_pages = _get_active_pages()

    if len(active_pages) == 1:
        content = f"""{_XML_DECL}<CiscoIPPhoneText Refresh="{IDLE_CYCLE_SECONDS}" URL="{base}/{active_pages[0]}">
  <Title>Meow</Title>
  <Prompt>New message!</Prompt>
  <Text>Loading...</Text>
</CiscoIPPhoneText>"""
        PAGE_CACHE["idle.xml"] = content
        print(f"Cached idle.xml (exclusive: {active_pages[0]})")
        threading.Thread(target=update_mwi, daemon=True).start()
        return

    # Only patch pages that are actually in cache to avoid broken ring links
    available_pages = [p for p in active_pages if p in PAGE_CACHE]
    if not available_pages:
        print("WARNING: write_cycle_ring - no pages in cache, skipping ring patch")
        return

    for i, filename in enumerate(available_pages):
        next_filename = available_pages[(i + 1) % len(available_pages)]
        next_url = f"{base}/{next_filename}"
        xml = PAGE_CACHE[filename]
        # Replace the URL attribute on the root CiscoIPPhoneText element.
        # count=1 is safe because our generated XML never has URL="..." in
        # element content — only as the single attribute on the root tag.
        xml = re.sub(r'URL="[^"]*"', f'URL="{next_url}"', xml, count=1)
        PAGE_CACHE[filename] = xml

    entry = available_pages[random.randrange(len(available_pages))]
    content = f"""{_XML_DECL}<CiscoIPPhoneText Refresh="{IDLE_CYCLE_SECONDS}" URL="{base}/{entry}">
  <Title>Meow</Title>
  <Prompt>Auto-cycling every {IDLE_CYCLE_SECONDS}s</Prompt>
  <Text>Loading...</Text>
</CiscoIPPhoneText>"""
    PAGE_CACHE["idle.xml"] = content
    print(f"Cached idle.xml -> {entry}, ring: {' -> '.join(active_pages)}")
    threading.Thread(target=update_mwi, daemon=True).start()

write_idle_cycle = write_cycle_ring

# ═══════════════════════════════════════════════════════
# DISK DUMP
# ═══════════════════════════════════════════════════════

DUMP_ACTIVE = False
_dump_delete_timer = None

def dump_to_disk():
    global DUMP_ACTIVE, _dump_delete_timer
    written = []
    for filename, xml in PAGE_CACHE.items():
        try:
            with open(os.path.join(OUTPUT_DIR, filename), "w") as f:
                f.write(xml)
            written.append(filename)
        except Exception as e:
            print(f"Dump error for {filename}: {e}")
    DUMP_ACTIVE = True
    print(f"Dumped {len(written)} files to disk")
    # Cancel any existing auto-delete timer before scheduling a fresh one,
    # so multiple /meowdump calls don't stack up competing timers.
    if _dump_delete_timer is not None:
        _dump_delete_timer.cancel()
    _dump_delete_timer = threading.Timer(6 * 3600, delete_dump)
    _dump_delete_timer.daemon = True
    _dump_delete_timer.start()
    return written

def delete_dump():
    global DUMP_ACTIVE, _dump_delete_timer
    _dump_delete_timer = None
    removed = []
    for filename in list(PAGE_CACHE.keys()):
        filepath = os.path.join(OUTPUT_DIR, filename)
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                removed.append(filename)
            except Exception as e:
                print(f"Delete error for {filename}: {e}")
    DUMP_ACTIVE = False
    print(f"Auto-deleted {len(removed)} dumped files from disk")

# ═══════════════════════════════════════════════════════
# HTTP SERVER
# ═══════════════════════════════════════════════════════

def start_http_server():
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import mimetypes

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            path = self.path.lstrip("/").split("?")[0]

            # Block path traversal attempts
            if ".." in path or path.startswith("/"):
                print(f"WARNING: Path traversal attempt from {self.client_address[0]}: {self.path}")
                self.send_response(400)
                self.end_headers()
                return

            if path == "health":
                age = int(time.time() - LAST_FETCH_TIME) if LAST_FETCH_TIME else -1
                body = json.dumps({"last_fetch_seconds_ago": age, "ok": age < 600}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            if path in PAGE_CACHE:
                data = PAGE_CACHE[path].encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/xml; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return

            filepath = os.path.join(OUTPUT_DIR, path or "index.html")
            if os.path.isfile(filepath):
                mime, _ = mimetypes.guess_type(filepath)
                with open(filepath, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", mime or "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self.send_response(404)
                self.end_headers()

    server = HTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"HTTP server started on port {HTTP_PORT}")

# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    print(f"Fetching all data at {datetime.now().strftime('%H:%M:%S')}...")

    # Remove any stale readiness marker from a previous run so the healthcheck
    # cannot pass before this startup finishes writing configs.
    _marker_path = os.path.join(ASTERISK_CONFIG_DIR, ".configs_written")
    try:
        os.remove(_marker_path)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"Asterisk: could not remove stale readiness marker: {e}")

    # Write Asterisk configs first so they're ready before Asterisk starts
    _configs_generation_ok = False
    try:
        write_asterisk_configs()
        _configs_generation_ok = True
    except Exception:
        pass  # error already printed by write_asterisk_configs()

    # Only write the readiness marker if configs were generated successfully and
    # the expected config files exist on disk, so we don't advertise readiness
    # when Asterisk would immediately crash.
    try:
        os.makedirs(ASTERISK_CONFIG_DIR, exist_ok=True)

        required_configs = ["pjsip.conf", "extensions.conf"]
        missing = [
            name for name in required_configs
            if not os.path.isfile(os.path.join(ASTERISK_CONFIG_DIR, name))
        ]

        if not _configs_generation_ok or missing:
            if not _configs_generation_ok:
                print("Asterisk: not writing readiness marker; config generation failed.")
            else:
                print("Asterisk: not writing readiness marker; missing config files:")
                for name in missing:
                    print(f"  - {name}")
            print("Asterisk: container healthcheck will fail; Asterisk will not start until configs are successfully generated.")
        else:
            with open(_marker_path, "w") as f:
                f.write("ok")
    except Exception as e:
        print(f"Asterisk: could not write readiness marker: {e}")
        print("Asterisk: healthcheck will fail; Asterisk container will not start. Check ASTERISK_CONFIG_DIR permissions.")
        raise SystemExit(1)

    # Write idle.xml placeholder immediately so the phone never gets a 404
    # even if it polls before the first fetch cycle completes
    base = f"http://{SERVER_IP}:{HTTP_PORT}"
    PAGE_CACHE["idle.xml"] = f"""{_XML_DECL}<CiscoIPPhoneText Refresh="10" URL="{base}/idle.xml">
  <Title>Meow</Title>
  <Prompt>Starting up...</Prompt>
  <Text>Loading pages, please wait...</Text>
</CiscoIPPhoneText>"""
    print("Cached idle.xml placeholder (startup)")

    # Pre-populate DM pages so they exist before any DM arrives
    fetch_page11()
    fetch_page12()

    start_http_server()
    start_discord_bot()

    speedtest_thread = threading.Thread(target=schedule_speedtest, daemon=True)
    speedtest_thread.start()

    threading.Thread(target=schedule_mwi_clear, daemon=True).start()

    def fetch_all():
        global LAST_FETCH_TIME
        print(f"Fetching all pages at {datetime.now().strftime('%H:%M:%S')}...")
        try:
            dm_funcs = []
            if DM_MESSAGE is not None:
                dm_funcs.append(fetch_page11)
            if DM_MESSAGE_PRIORITY is not None:
                dm_funcs.append(fetch_page12)
            run_fetch_parallel(fetch_page1, fetch_page2, fetch_page3, fetch_page4,
                               fetch_page5, fetch_page6, fetch_page7, fetch_page9,
                               fetch_page10, *dm_funcs)
            write_menus()
            write_idle_cycle()
            LAST_FETCH_TIME = time.time()
            print(f"All pages cached at {datetime.now().strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"ERROR: fetch_all crashed: {e}")

    def watchdog():
        while True:
            time.sleep(60)
            if LAST_FETCH_TIME > 0 and time.time() - LAST_FETCH_TIME > 600:
                print("WARNING: Watchdog triggered - fetch loop stalled, restarting fetch.")
                try:
                    threading.Thread(target=fetch_all, daemon=True).start()
                except Exception as e:
                    print(f"ERROR: Watchdog failed to restart fetch_all: {e}")

    fetch_all()

    threading.Thread(target=watchdog, daemon=True).start()

    while True:
        try:
            time.sleep(300)
            fetch_all()
        except Exception as e:
            print(f"ERROR: Main loop crashed: {e}")
            time.sleep(30)
