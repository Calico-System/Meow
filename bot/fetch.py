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
        print("WARNING: pip install failed — some features may not work")
    subprocess.run(["apk", "add", "--no-cache", "curl"], check=False)
    result = subprocess.run(["which", "speedtest"], capture_output=True)
    if result.returncode != 0:
        print("Bootstrap: installing Ookla speedtest CLI...")
        subprocess.run(
            "curl -s https://install.speedtest.net/app/cli/install.sh | sh",
            shell=True, check=False
        )
    print("Bootstrap: done.")

bootstrap()

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
from datetime import datetime, timezone

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
OWNER_USER_ID = int(os.environ.get("OWNER_USER_ID", "0"))
PRIORITY_USER_ID = int(os.environ.get("PRIORITY_USER_ID", "0"))
PRIORITY_USER_IDS = {PRIORITY_USER_ID, OWNER_USER_ID}

_REQUIRED_ENV = {
    "DISCORD_TOKEN": DISCORD_TOKEN,
    "DISCORD_GUILD_ID": DISCORD_GUILD_ID,
    "TRUENAS_KEY": TRUENAS_KEY,
    "OWNER_USER_ID": os.environ.get("OWNER_USER_ID", ""),
    "PRIORITY_USER_ID": os.environ.get("PRIORITY_USER_ID", ""),
}
for _var, _val in _REQUIRED_ENV.items():
    if not _val:
        print(f"WARNING: {_var} is not set — related features will not work")

IDLE_CYCLE_SECONDS = int(os.environ.get("IDLE_CYCLE_SECONDS", "30"))
SPEEDTEST_INTERVAL = int(os.environ.get("SPEEDTEST_INTERVAL", "3600"))
MWI_ENABLED = os.environ.get("MWI_ENABLED", "true").lower() == "true"
MWI_DM_DURATION = int(os.environ.get("MWI_DM_DURATION", "300"))
DM_COOLDOWN_SECONDS = int(os.environ.get("DM_COOLDOWN_SECONDS", "60"))
PHONE_SIP_EXTENSION = os.environ.get("PHONE_SIP_EXTENSION", "1001")
MINECRAFT_SERVER_NAME = os.environ.get("MINECRAFT_SERVER_NAME", "Minecraft")
NEWS_BASE_CURRENCY = os.environ.get("NEWS_BASE_CURRENCY", "GBP")

# Ping hosts — configure up to 5 via PING_HOST_1_NAME / PING_HOST_1_IP etc.
# Falls back to just Google if none are set.
PING_HOSTS = {}
for _i in range(1, 6):
    _name = os.environ.get(f"PING_HOST_{_i}_NAME", "")
    _ip   = os.environ.get(f"PING_HOST_{_i}_IP", "")
    if _name and _ip:
        PING_HOSTS[_name] = _ip
if not PING_HOSTS:
    PING_HOSTS = {"Google": "8.8.8.8"}

# ═══════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════

os.makedirs(OUTPUT_DIR, exist_ok=True)
SPEEDTEST_CACHE = os.path.join(OUTPUT_DIR, ".speedtest_cache.json")

STATUS_CACHE = {}
PAGE_CACHE = {}

def save_status_data(key, value):
    STATUS_CACHE[key] = value

def get_status_data():
    return dict(STATUS_CACHE)

def write_xml_refresh(filename, title, text, refresh_secs, refresh_url):
    def sanitize(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    def to_phone_text(s):
        return sanitize(s).replace("\n", "&#13;")
    xml = f'''<CiscoIPPhoneText Refresh="{refresh_secs}" URL="{refresh_url}">
  <Title>{sanitize(title)}</Title>
  <Prompt>Updated: {datetime.now().strftime('%H:%M')}</Prompt>
  <Text>{to_phone_text(text)}</Text>
</CiscoIPPhoneText>'''
    PAGE_CACHE[filename] = xml
    print(f"Cached {filename} (memory, refresh={refresh_secs}s)")

def write_xml(filename, title, text):
    def sanitize(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    def to_phone_text(s):
        return sanitize(s).replace("\n", "&#13;")
    idle_url = f"http://{SERVER_IP}:{HTTP_PORT}/idle.xml"
    xml = f"""<CiscoIPPhoneText Refresh="{IDLE_CYCLE_SECONDS}" URL="{idle_url}">
  <Title>{sanitize(title)}</Title>
  <Prompt>Updated: {datetime.now().strftime('%H:%M')}</Prompt>
  <Text>{to_phone_text(text)}</Text>
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

def ping(host):
    for port in [80, 443, 53]:
        try:
            start = time.time()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            ip = socket.gethostbyname(host)
            sock.connect((ip, port))
            sock.close()
            ms = (time.time() - start) * 1000
            return f"{ms:.0f}ms"
        except socket.timeout:
            continue
        except ConnectionRefusedError:
            ms = (time.time() - start) * 1000
            return f"{ms:.0f}ms"
        except Exception:
            continue
    try:
        socket.gethostbyname(host)
        return "OK"
    except Exception:
        return "DOWN"

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
            print(f"WARNING: {name} timed out after {timeout}s — abandoning")

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

def schedule_speedtest():
    run_speedtest()
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

        weather_text = f"--- WEATHER ---\n{desc}\n{temp}°C | Wind {wind}mph"
        lines = [
            "--- WEATHER ---",
            f"{desc}",
            f"Temp:    {temp}°C (feels {feels}°C)",
            f"High/Low:{t_max}°C / {t_min}°C",
            f"Wind:    {wind}mph {compass} (g{gusts}mph)",
            f"Humidity:{humidity}%  Cloud:{cloud}%",
            f"Pressure:{pressure:.0f}hPa",
            f"UV:      {uv} (max {uv_max})",
            f"Vis:     {vis_km:.1f}km  Rain:{precip}mm",
            f"Precip:  {precip_day}mm today",
            f"Sunrise: {sunrise_str}  Set:{sunset_str}",
        ]
        weather_text_full = "\n".join(lines)

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
        root = ET.fromstring(r.text)
        items = root.findall(".//item")[:3]
        headlines = ["--- BBC NEWS ---"]
        headlines_full = ["--- BBC NEWS ---"]
        for i, item in enumerate(items, 1):
            title = item.find("title")
            if title is not None:
                t = title.text or ""
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
    r = safe_get(f"https://api.exchangerate-api.com/v4/latest/{NEWS_BASE_CURRENCY}")
    if r:
        rates = r.json().get("rates", {})
        eur = rates.get("EUR")
        usd = rates.get("USD")
        if isinstance(eur, (int, float)) and isinstance(usd, (int, float)):
            exchange_text = f"--- {NEWS_BASE_CURRENCY} RATES ---\n{NEWS_BASE_CURRENCY}1 = €{eur:.4f}\n{NEWS_BASE_CURRENCY}1 = ${usd:.4f}"
            STATUS_CACHE["exchange"] = f"{NEWS_BASE_CURRENCY}1 = €{eur:.2f} | ${usd:.2f}"
            save_status_data("eur", eur)
            save_status_data("usd", usd)
        else:
            exchange_text = "Rates: Bad data"

    grid_text = "Grid: Unavailable"
    grid_text_full = "Grid: Unavailable"
    r = safe_get("https://api.carbonintensity.org.uk/intensity")
    if r:
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
            name = d.get("name", "Unknown")
            win = d.get("win_open") or d.get("t0") or ""
            date_str = win[:16] if win else "TBD"
            vehicle = d.get("vehicle", {}).get("name", "")
            pad = d.get("pad", {}).get("name", "")
            details = d.get("launch_description") or f"{vehicle} from {pad}"
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
    r = safe_get(f"http://history.muffinlabs.com/date/{today.month}/{today.day}")
    if r:
        events = r.json().get("data", {}).get("Events", [])
        if events:
            pick = random.choice(events[:20])
            year = pick.get("year", "")
            text_raw = pick.get("text", "")
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
        fact = r.json().get("fact", "")
        fact_wrapped = wrap(fact, 32)
        cat_text = f"--- CAT FACT ---\n{fact_wrapped}"
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
        ping_lines = ["--- PING ---"]
        for name, host in sorted(PING_HOSTS.items()):
            result = ping(host)
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

        indicator_map = {"none": "OK", "minor": "DEGRADED",
                         "major": "MAJOR ISSUE", "critical": "DOWN"}

        claude_status = "Unavailable"
        claude_ping = ping("api.anthropic.com")
        r = safe_get("https://status.anthropic.com/api/v2/status.json")
        if r:
            s = r.json().get("status", {})
            indicator = s.get("indicator", "none")
            claude_status = indicator_map.get(indicator, indicator)
            if indicator != "none":
                _network = True
                _service = True

        discord_svc_status = "Unavailable"
        r = safe_get("https://discordstatus.com/api/v2/status.json")
        if r:
            s = r.json().get("status", {})
            indicator = s.get("indicator", "none")
            discord_svc_status = indicator_map.get(indicator, indicator)
            if indicator != "none":
                _network = True
                _service = True

        status_lines = [
            "--- STATUS ---",
            f"Claude: {claude_status} ({claude_ping})",
            f"Discord: {discord_svc_status} ({discord_latency})",
        ]

        page7_lines = status_lines + ping_lines[:-1]
        page7_full_lines = status_lines + [""] + ping_lines[:-1]
        write_xml("page7.xml", "Status & Pings", "\n".join(page7_lines))
        write_xml("page7_full.xml", "Status & Pings", "\n".join(page7_full_lines))

        write_xml("page8.xml", "Speedtest", "\n".join(speed_lines))
        write_xml("page8_full.xml", "Speedtest", "\n".join(speed_lines))

    except Exception as e:
        print(f"ERROR: fetch_page7 crashed: {e}")
        write_xml("page7.xml", "Status & Pings", f"--- STATUS ---\nError fetching data:\n{str(e)[:60]}")
        write_xml("page7_full.xml", "Status & Pings", f"--- STATUS ---\nError fetching data:\n{str(e)[:60]}")

    finally:
        NETWORK_ISSUE = _network
        PING_ISSUE = _ping
        SERVICE_ISSUE = _service

# ═══════════════════════════════════════════════════════
# PAGE 9: Servers (Minecraft + TrueNAS)
# ═══════════════════════════════════════════════════════

def fetch_page9():
    global MC_HAS_PLAYERS
    mc_lines = [f"--- {MINECRAFT_SERVER_NAME.upper()} ---"]
    try:
        sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock2.settimeout(3)
        sock2.connect((MINECRAFT_IP, MINECRAFT_PORT))
        sock2.send(b'\xfe\x01')
        data = sock2.recv(1024)
        sock2.close()
        if data and len(data) > 3 and data[0] == 0xff:
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

    nas_lines = ["--- TRUENAS ---"]
    try:
        headers = {"Authorization": f"Bearer {TRUENAS_KEY}"}
        r = requests.get(
            f"http://{TRUENAS_IP}/api/v2.0/pool",
            headers=headers, timeout=5
        )
        if r.status_code == 200:
            pools = r.json()
            for pool in pools:
                name = pool.get("name", "unknown")
                status = pool.get("status", "unknown")
                r2 = requests.get(
                    f"http://{TRUENAS_IP}/api/v2.0/pool/dataset",
                    headers=headers, timeout=5,
                    params={"name": name}
                )
                if r2.status_code == 200:
                    datasets = r2.json()
                    if datasets:
                        used = datasets[0].get("used", {}).get("parsed", 0)
                        avail = datasets[0].get("available", {}).get("parsed", 0)
                        total = used + avail
                        pct = (used / total * 100) if total > 0 else 0

                        def fmt_size(b):
                            for unit, div in [("TB", 1_099_511_627_776), ("GB", 1_073_741_824), ("MB", 1_048_576)]:
                                if b >= div:
                                    return f"{b/div:.1f}{unit}"
                            return f"{b}B"

                        nas_lines.append(f"{name}: {status}")
                        nas_lines.append(f"{fmt_size(used)} / {fmt_size(total)}")
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

def fetch_page10():
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
        cname = channel.get("name", "unknown")
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
        content_raw = msg.get("content", "")
        author = msg.get("author", {}).get("username", "Unknown")
        timestamp = msg.get("timestamp", "")
        if not content_raw and msg.get("attachments"):
            content_raw = "[attachment]"
        if not content_raw and msg.get("embeds"):
            content_raw = "[embed]"
        if not content_raw:
            return None
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
            "content": content_raw,
            "ts_display": ts_display,
            "dt": dt
        }

    # Fetch all channels in parallel
    results = [None] * len(text_channels)
    def fetch_and_store(i, channel):
        results[i] = fetch_channel(channel)

    threads = []
    for i, ch in enumerate(text_channels):
        t = threading.Thread(target=fetch_and_store, args=(i, ch), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(timeout=10)

    channel_data = [r for r in results if r is not None]

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

def start_discord_bot():
    import discord
    from discord import app_commands
    import asyncio

    async def owner_only(interaction: discord.Interaction) -> bool:
        if interaction.user.id != OWNER_USER_ID:
            await interaction.response.send_message(embed=discord.Embed(title="Only the owner can use this command.", color=discord.Color.red()), ephemeral=True)
            return False
        return True

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
        for ip in PING_HOSTS.values():
            redactions[ip] = "##.##.##.##"
        for val, rep in redactions.items():
            text = text.replace(val, rep)
        return text

    def build_status_embed():
        latency = round(client.latency * 1000)
        embed = discord.Embed(title="Birch", color=discord.Color.blurple())
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
        embed.set_footer(text="DM me to show a message on the phone screen for 5 mins | Priority user gets top priority")
        return embed

    @tree.command(name="sipping", description="Live status: bot latency, exchange rates, grid, Minecraft")
    async def slash_ping(interaction: discord.Interaction):
        await interaction.response.send_message(embed=build_status_embed())

    @tree.command(name="siprefresh", description="Force regenerate all 12 phone pages immediately")
    async def slash_refresh(interaction: discord.Interaction):
        if not await owner_only(interaction): return
        embed = discord.Embed(title="Refreshing all pages...", color=discord.Color.yellow())
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

    @tree.command(name="sipdump", description="Write all in-memory pages to disk for debugging (auto-deleted after 6 hours)")
    async def slash_dump(interaction: discord.Interaction):
        if not await owner_only(interaction): return
        embed = discord.Embed(title="Dumping pages to disk...", color=discord.Color.yellow())
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

    @tree.command(name="sippurge", description="Delete all XML and JSON output files from disk")
    async def slash_purge(interaction: discord.Interaction):
        if not await owner_only(interaction): return
        embed = discord.Embed(title="Purging output files...", color=discord.Color.yellow())
        await interaction.response.send_message(embed=embed)
        try:
            removed = []
            for fname in os.listdir(OUTPUT_DIR):
                if fname.endswith(".xml") or fname.endswith(".json"):
                    os.remove(os.path.join(OUTPUT_DIR, fname))
                    removed.append(fname)
            if removed:
                embed = discord.Embed(title=f"Removed {len(removed)} files", description="\n".join(removed), color=discord.Color.green())
            else:
                embed = discord.Embed(title="Nothing to remove", color=discord.Color.green())
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=discord.Embed(title="Error", description=str(e), color=discord.Color.red()))

    @tree.command(name="sipabout", description="What this bot does and how the phone setup works")
    async def slash_about(interaction: discord.Interaction):
        embed = discord.Embed(
            title="Birch",
            description=(
                "A general-purpose Discord bot with a Cisco 7940G phone dashboard. "
                "Keeps the phone screen updated with live info: weather, news, exchange rates, "
                "National Grid carbon intensity, rocket launches, cat facts, Minecraft server "
                "status, Discord messages, and network health."
            ),
            color=discord.Color.blurple()
        )
        embed.add_field(name="DM Feature", value="DM me and your message appears on the phone screen for 5 minutes. The priority user gets top priority.", inline=False)
        embed.set_footer(text="github.com/YOURUSERNAME/birch")
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
            return fallback_title, "Page not yet generated. Try /siprefresh"
        try:
            root = ET.fromstring(PAGE_CACHE[filename])
            title = root.findtext("Title") or fallback_title
            text = root.findtext("Text") or ""
            text = text.replace("&#13;", "\n")
            return title, redact(text)
        except Exception as e:
            return fallback_title, f"Error reading page: {e}"

    @tree.command(name="sippage", description="Show a page as it appears on the phone screen (1-12)")
    @app_commands.describe(page="Page number 1-12")
    async def slash_page(interaction: discord.Interaction, page: app_commands.Range[int, 1, 12]):
        if page in LOCKED_PAGES and interaction.user.id != OWNER_USER_ID:
            await interaction.response.send_message(embed=discord.Embed(title="That page is private.", color=discord.Color.red()), ephemeral=True)
            return
        result = get_page_text(page, full=False)
        if not result:
            await interaction.response.send_message(embed=discord.Embed(title=f"Page {page} not found", color=discord.Color.red()), ephemeral=True)
            return
        title, text = result
        embed = discord.Embed(title=f"{page}. {title} (screen)", description=f"```\n{text[:4000]}\n```", color=discord.Color.dark_grey())
        await interaction.response.send_message(embed=embed)

    @tree.command(name="sippagefull", description="Show a page in full scrollable form with no truncation (1-12)")
    @app_commands.describe(page="Page number 1-12")
    async def slash_page_full(interaction: discord.Interaction, page: app_commands.Range[int, 1, 12]):
        if page in LOCKED_PAGES and interaction.user.id != OWNER_USER_ID:
            await interaction.response.send_message(embed=discord.Embed(title="That page is private.", color=discord.Color.red()), ephemeral=True)
            return
        result = get_page_text(page, full=True)
        if not result:
            await interaction.response.send_message(embed=discord.Embed(title=f"Page {page} not found", color=discord.Color.red()), ephemeral=True)
            return
        title, text = result
        embed = discord.Embed(title=f"{page}. {title} (full)", description=f"```\n{text[:4000]}\n```", color=discord.Color.blurple())
        await interaction.response.send_message(embed=embed)

    @tree.command(name="sipall", description="Show all 12 phone pages as they appear on screen")
    async def slash_all(interaction: discord.Interaction):
        if not await owner_only(interaction): return
        await interaction.response.send_message(embed=discord.Embed(title="Fetching all pages...", color=discord.Color.blurple()))
        for num in sorted(PAGE_MAP.keys()):
            if num in LOCKED_PAGES and interaction.user.id != OWNER_USER_ID:
                continue
            result = get_page_text(num, full=False)
            if not result:
                continue
            title, text = result
            embed = discord.Embed(title=f"{num}. {title}", description=f"```\n{text[:4000]}\n```", color=discord.Color.dark_grey())
            await interaction.followup.send(embed=embed)

    @tree.command(name="sipallfull", description="Show all 12 phone pages in full untruncated scrollable form")
    async def slash_all_full(interaction: discord.Interaction):
        if not await owner_only(interaction): return
        await interaction.response.send_message(embed=discord.Embed(title="Fetching all pages (full)...", color=discord.Color.blurple()))
        for num in sorted(PAGE_MAP.keys()):
            if num in LOCKED_PAGES and interaction.user.id != OWNER_USER_ID:
                continue
            result = get_page_text(num, full=True)
            if not result:
                continue
            title, text = result
            embed = discord.Embed(title=f"{num}. {title} (full)", description=f"```\n{text[:4000]}\n```", color=discord.Color.blurple())
            await interaction.followup.send(embed=embed)

    @tree.command(name="siptest", description="Push a calibration ruler to the phone screen")
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
            color=discord.Color.yellow()
        ))

    @tree.command(name="sipmessage", description="Push a custom message to the phone screen for a set duration")
    @app_commands.describe(
        text="The message to display on the phone screen",
        duration="How long to show it in seconds (default 300, max 3600)"
    )
    async def slash_message(interaction: discord.Interaction, text: str, duration: app_commands.Range[int, 10, 3600] = 300):
        if not await owner_only(interaction): return
        idle_url = f"http://{SERVER_IP}:{HTTP_PORT}/idle.xml"
        body = wrap_full(text)
        write_xml_refresh("custommsg.xml", "Message", f"--- MESSAGE ---\n{body}", duration, idle_url)
        write_idle_cycle_immediate("custommsg.xml")
        await interaction.response.send_message(embed=discord.Embed(
            title="Message pushed to phone",
            description=f"```\n{text[:500]}\n```\nShowing for {duration}s, then returning to idle.",
            color=discord.Color.green()
        ))

    @tree.command(name="sipstatus", description="Show what page the phone is currently displaying")
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
            dm_status = f"VIP DM ({age}s ago)" if age < 300 else f"VIP DM (expired, {age}s ago)"
        elif DM_MESSAGE:
            age = int((now - DM_RECEIVED_AT).total_seconds())
            dm_status = f"DM ({age}s ago)" if age < 300 else f"DM (expired, {age}s ago)"

        embed = discord.Embed(title="Phone Status", color=discord.Color.blurple())
        embed.add_field(name="Current page", value=f"`{current_page}`", inline=True)
        embed.add_field(name="Next page", value=f"`{next_page}` in {secs_until_next}s", inline=True)
        embed.add_field(name="Rotation", value=f"{len(active_pages)} pages active", inline=True)
        embed.add_field(name="DM state", value=dm_status, inline=True)
        embed.add_field(name="Network issue", value="Yes" if NETWORK_ISSUE else "No", inline=True)
        embed.add_field(name="MC players", value="Yes" if MC_HAS_PLAYERS else "No", inline=True)
        last_fetch = f"{int(time.time() - LAST_FETCH_TIME)}s ago" if LAST_FETCH_TIME else "Never"
        embed.add_field(name="Last fetch", value=last_fetch, inline=True)
        await interaction.response.send_message(embed=embed)

    @tree.command(name="siphelp", description="Full command list, page key, and usage guide")
    async def slash_help(interaction: discord.Interaction):
        is_owner = interaction.user.id == OWNER_USER_ID
        page_key = "\n".join(
            f"{n:2}. {title:<18} {desc}"
            for n, (_, _f, title, desc) in sorted(PAGE_MAP.items())
            if n not in LOCKED_PAGES or is_owner
        )
        commands_public = (
            "`/sipping` — live status summary\n"
            "`/sippage <1-12>` — page as it appears on screen\n"
            "`/sippagefull <1-12>` — full untruncated page\n"
            "`/sipabout` — what this bot does\n"
            "`/siphelp` — this message"
        )
        commands_owner = (
            "`/sipall` — all pages as they appear on screen\n"
            "`/sipallfull` — all pages in full untruncated form\n"
            "`/siprefresh` — regenerate all pages immediately\n"
            "`/siptest` — push calibration ruler to phone screen\n"
            "`/sipmessage <text> [duration]` — push custom message to phone\n"
            "`/sipstatus` — show current page and rotation state\n"
            "`/sipdump` — write pages to disk for debugging\n"
            "`/sippurge` — delete all output files from disk"
        )
        advanced = (
            "DM the bot to show a message on the phone screen for 5 minutes.\n"
            "The priority user (PRIORITY_USER_ID) goes to page 12 and overrides everything.\n"
            "All other DMs go to page 11 and override normal rotation.\n"
            "Network issues override normal rotation and show page 7 (Status & Pings).\n"
            "Page 10 (Discord) updates within 30s of any new server message."
        )
        embed = discord.Embed(title="Birch", color=discord.Color.blurple())
        embed.add_field(name="Page Key", value=f"```\n{page_key}\n```", inline=False)
        embed.add_field(name="Commands", value=commands_public, inline=False)
        if is_owner:
            embed.add_field(name="Owner Commands", value=commands_owner, inline=False)
        embed.add_field(name="Advanced Usage", value=advanced, inline=False)
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

    @client.event
    async def on_ready():
        print(f"Discord bot connected as {client.user}")
        await tree.sync()
        print("Slash commands synced")
        asyncio.ensure_future(cycle_status())
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
            last_dm = DM_COOLDOWNS.get(message.author.id)
            if last_dm and (datetime.now() - last_dm).total_seconds() < DM_COOLDOWN_SECONDS:
                remaining = DM_COOLDOWN_SECONDS - int((datetime.now() - last_dm).total_seconds())
                await message.reply(embed=discord.Embed(
                    title="Slow down!",
                    description=f"You can send another message in {remaining}s.",
                    color=discord.Color.orange()
                ))
                return
            DM_COOLDOWNS[message.author.id] = datetime.now()

        dm_entry = {
            "author": message.author.name,
            "author_id": message.author.id,
            "text": message.content or "[attachment]",
            "time": datetime.now(),
        }
        if message.author.id in PRIORITY_USER_IDS:
            DM_MESSAGE_PRIORITY = dm_entry
            fetch_page12()
            write_idle_cycle_immediate("page12.xml", hold_secs=300)
        else:
            DM_MESSAGE = dm_entry
            DM_RECEIVED_AT = datetime.now()
            fetch_page11()
            write_idle_cycle_immediate("page11.xml", hold_secs=300)
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
        # Reconnect loop — retries on any connection error with backoff
        backoff = 5
        while True:
            try:
                print("Discord bot: connecting...")
                loop.run_until_complete(client.start(DISCORD_TOKEN))
            except Exception as e:
                print(f"Discord bot disconnected: {e} — retrying in {backoff}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 300)  # exponential backoff, cap at 5 min
            else:
                # clean close — don't reconnect
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
        author = DM_MESSAGE["author"]
        text = DM_MESSAGE["text"]
        received = DM_MESSAGE["time"].strftime("%H:%M %d/%m")
        body = f"--- DM RECEIVED ---\nFrom: {author}\nAt: {received}\n\n{wrap_full(text)}"
    else:
        body = "--- DM ---\nNo messages yet"
    idle_url = f"http://{SERVER_IP}:{HTTP_PORT}/idle.xml"
    write_xml_refresh("page11.xml", "DM", body, 300, idle_url)
    write_xml_refresh("page11_full.xml", "DM", body, 300, idle_url)

def fetch_page12():
    global DM_MESSAGE_PRIORITY
    if DM_MESSAGE_PRIORITY:
        author = DM_MESSAGE_PRIORITY["author"]
        text = DM_MESSAGE_PRIORITY["text"]
        received = DM_MESSAGE_PRIORITY["time"].strftime("%H:%M %d/%m")
        body = f"--- DM: {author.upper()} ---\nAt: {received}\n\n{wrap_full(text)}"
    else:
        body = "--- VIP DM ---\nNo messages yet"
    idle_url = f"http://{SERVER_IP}:{HTTP_PORT}/idle.xml"
    write_xml_refresh("page12.xml", "VIP DM", body, 300, idle_url)
    write_xml_refresh("page12_full.xml", "VIP DM", body, 300, idle_url)

# ═══════════════════════════════════════════════════════
# MENUS
# ═══════════════════════════════════════════════════════

def write_menus():
    base = f"http://{SERVER_IP}:{HTTP_PORT}"

    info_menu = f"""<CiscoIPPhoneMenu>
  <Title>Info Services</Title>
  <Prompt>Select a page</Prompt>
  <MenuItem>
    <n>Weather</n>
    <URL>{base}/page1_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <n>BBC News</n>
    <URL>{base}/page2_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <n>Economy</n>
    <URL>{base}/page3_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <n>Space</n>
    <URL>{base}/page4_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <n>History</n>
    <URL>{base}/page5_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <n>Fun</n>
    <URL>{base}/page6_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <n>Status &amp; Pings</n>
    <URL>{base}/page7_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <n>Speedtest</n>
    <URL>{base}/page8_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <n>Servers</n>
    <URL>{base}/page9_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <n>Discord Activity</n>
    <URL>{base}/page10_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <n>Latest DM</n>
    <URL>{base}/page11_full.xml</URL>
  </MenuItem>
  <MenuItem>
    <n>Priority DM</n>
    <URL>{base}/page12_full.xml</URL>
  </MenuItem>
</CiscoIPPhoneMenu>"""
    PAGE_CACHE["infoservices.xml"] = info_menu
    print("Cached infoservices.xml (memory)")

    main_menu = f"""<CiscoIPPhoneMenu>
  <Title>Birch Services</Title>
  <Prompt>Select an option</Prompt>
  <MenuItem>
    <n>Channel Directory</n>
    <URL>{base}/channels.xml</URL>
  </MenuItem>
  <MenuItem>
    <n>Extension Cheat Sheet</n>
    <URL>{base}/cheatsheet.xml</URL>
  </MenuItem>
  <MenuItem>
    <n>Info Services</n>
    <URL>{base}/infoservices.xml</URL>
  </MenuItem>
</CiscoIPPhoneMenu>"""
    PAGE_CACHE["services.xml"] = main_menu
    print("Cached services.xml (memory)")

# ═══════════════════════════════════════════════════════
# GLOBAL FLAGS
# ═══════════════════════════════════════════════════════

MC_HAS_PLAYERS = False
NETWORK_ISSUE = False
PING_ISSUE = False
SERVICE_ISSUE = False
MWI_STATE = False
LAST_FETCH_TIME = 0
DM_MESSAGE = None
DM_RECEIVED_AT = None
DM_MESSAGE_PRIORITY = None
LAST_PAGE7_UPDATE = 0
DM_COOLDOWNS = {}

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
    content = f"""<CiscoIPPhoneText Refresh="{secs}" URL="{base}/{page}">
  <Title>Birch</Title>
  <Prompt>New message!</Prompt>
  <Text>Loading...</Text>
</CiscoIPPhoneText>"""
    PAGE_CACHE["idle.xml"] = content
    print(f"Cached idle.xml in memory (immediate switch to {page}, hold {secs}s)")

def _get_active_pages():
    now = datetime.now()
    if DM_MESSAGE_PRIORITY:
        dm_time = DM_MESSAGE_PRIORITY["time"]
        if hasattr(dm_time, 'tzinfo') and dm_time.tzinfo is not None:
            dm_time = dm_time.replace(tzinfo=None)
        if (now - dm_time).total_seconds() < 300:
            return ["page12.xml"]
    if DM_RECEIVED_AT:
        received_at = DM_RECEIVED_AT
        if hasattr(received_at, 'tzinfo') and received_at.tzinfo is not None:
            received_at = received_at.replace(tzinfo=None)
        if (now - received_at).total_seconds() < 300:
            return ["page11.xml"]
    if NETWORK_ISSUE:
        return ["page7.xml"]
    pages = ["page1.xml", "page2.xml", "page3.xml", "page4.xml",
             "page5.xml", "page6.xml", "page7.xml", "page8.xml", "page10.xml"]
    if MC_HAS_PLAYERS:
        pages.append("page9.xml")
    if DM_MESSAGE_PRIORITY:
        dm_time = DM_MESSAGE_PRIORITY["time"]
        if hasattr(dm_time, 'tzinfo') and dm_time.tzinfo is not None:
            dm_time = dm_time.replace(tzinfo=None)
        if (now - dm_time).total_seconds() >= 300:
            pages.append("page12.xml")
    if DM_RECEIVED_AT:
        received_at = DM_RECEIVED_AT
        if hasattr(received_at, 'tzinfo') and received_at.tzinfo is not None:
            received_at = received_at.replace(tzinfo=None)
        if (now - received_at).total_seconds() >= 300:
            pages.append("page11.xml")
    return pages

def write_cycle_ring():
    base = f"http://{SERVER_IP}:{HTTP_PORT}"
    active_pages = _get_active_pages()

    if len(active_pages) == 1:
        content = f"""<CiscoIPPhoneText Refresh="{IDLE_CYCLE_SECONDS}" URL="{base}/{active_pages[0]}">
  <Title>Birch</Title>
  <Prompt>New message!</Prompt>
  <Text>Loading...</Text>
</CiscoIPPhoneText>"""
        PAGE_CACHE["idle.xml"] = content
        print(f"Cached idle.xml (exclusive: {active_pages[0]})")
        threading.Thread(target=update_mwi, daemon=True).start()
        return

    for i, filename in enumerate(active_pages):
        next_filename = active_pages[(i + 1) % len(active_pages)]
        next_url = f"{base}/{next_filename}"
        if filename not in PAGE_CACHE:
            print(f"WARNING: {filename} not in PAGE_CACHE, skipping ring patch")
            continue
        xml = PAGE_CACHE[filename]
        xml = re.sub(r'(<CiscoIPPhoneText[^>]*Refresh="[^"]*"\s+URL=")[^"]*(")',
                     rf'\g<1>{next_url}\g<2>', xml)
        PAGE_CACHE[filename] = xml

    entry = active_pages[random.randrange(len(active_pages))]
    content = f"""<CiscoIPPhoneText Refresh="{IDLE_CYCLE_SECONDS}" URL="{base}/{entry}">
  <Title>Birch</Title>
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

def dump_to_disk():
    global DUMP_ACTIVE
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
    threading.Timer(6 * 3600, delete_dump).start()
    return written

def delete_dump():
    global DUMP_ACTIVE
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

    # Write idle.xml placeholder immediately so the phone never gets a 404
    # even if it polls before the first fetch cycle completes
    base = f"http://{SERVER_IP}:{HTTP_PORT}"
    PAGE_CACHE["idle.xml"] = f"""<CiscoIPPhoneText Refresh="10" URL="{base}/idle.xml">
  <Title>Birch</Title>
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
                print("WARNING: Watchdog triggered — fetch loop stalled, restarting fetch.")
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
