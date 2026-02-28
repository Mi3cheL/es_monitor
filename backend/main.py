from fastapi import FastAPI, UploadFile, File, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import time
import subprocess
import glob
import json
import base64
import hashlib
import secrets
import logging
import re
import shutil
import socket
import psutil
import platform

APP_NAME = "es_monitor"

BASE_DIR = Path("/opt/es_monitor")
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "content" / "items"
SECURITY_FILE = DATA_DIR / "config" / "security.json"

app = FastAPI(title=APP_NAME)

# =========================
# LOGGING
# =========================

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    filename=str(LOG_DIR / "backend.log"),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

logger = logging.getLogger(__name__)

# =========================
# STATIC FILES
# =========================

app.mount(
    "/static",
    StaticFiles(directory="/opt/es_monitor/backend/public"),
    name="static"
)

# =========================
# SESSION CONFIG
# =========================

SESSIONS = {}
SESSION_TIMEOUT = 900

def get_session_user(request: Request):
    session_id = request.cookies.get("session_id")
    if not session_id:
        raise HTTPException(status_code=401)

    session = SESSIONS.get(session_id)
    if not session:
        raise HTTPException(status_code=401)

    now = int(time.time())
    if now - session["last_activity"] > SESSION_TIMEOUT:
        del SESSIONS[session_id]
        raise HTTPException(status_code=401)

    session["last_activity"] = now
    return session

def require_admin(request: Request):
    session = get_session_user(request)
    username = session["username"]

    users = load_users()
    user = users.get(username)

    if not user or user.get("role") != "admin":
        raise HTTPException(status_code=403)

    return session

# =========================
# HARDENING CONFIG
# =========================

MAX_UPLOAD_SIZE = 300 * 1024 * 1024
CONVERSION_TIMEOUT = 180

# =========================
# SETTINGS CONFIG
# =========================

SETTINGS_FILE = DATA_DIR / "config" / "settings.json"

DEFAULT_SETTINGS = {
    "slide_duration_ms": 30000,
    "max_presentations": 10
}


def load_settings():
    if not SETTINGS_FILE.exists():
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(DEFAULT_SETTINGS, indent=2))
        return DEFAULT_SETTINGS.copy()

    try:
        data = json.loads(SETTINGS_FILE.read_text())
        return {
            "slide_duration_ms": int(data.get("slide_duration_ms", 30000)),
            "max_presentations": int(data.get("max_presentations", 10))
        }
    except Exception:
        return DEFAULT_SETTINGS.copy()


def save_settings(new_settings):
    SETTINGS_FILE.write_text(json.dumps(new_settings, indent=2))

# =========================
# USER AUTH
# =========================

def load_users():
    if not SECURITY_FILE.exists():
        return {}
    data = json.loads(SECURITY_FILE.read_text())
    return data.get("users", {})

def has_admin():
    users = load_users()
    return any(u.get("role") == "admin" for u in users.values())

def is_setup_mode():
    return not has_admin()

def verify_pbkdf2(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt_b64, hash_b64 = stored.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)

        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode(),
            salt,
            rounds,
            dklen=len(expected)
        )
        return dk == expected
    except Exception:
        return False

# =========================
# ROOT
# =========================

@app.get("/")
def root(request: Request):
    try:
        get_session_user(request)
        return RedirectResponse("/files", status_code=302)
    except:
        return RedirectResponse("/login", status_code=302)

@app.get("/health")
def health():
    return {"status": "ok", "app": APP_NAME}

# =========================
# LOGIN
# =========================

@app.get("/login")
def login_page(request: Request):

    if is_setup_mode():
        return RedirectResponse("/setup", status_code=302)

    return FileResponse("/opt/es_monitor/backend/public/login.html")

@app.post("/login")
async def login(request: Request):

    if is_setup_mode():
        return RedirectResponse("/setup", status_code=302)

    form = await request.form()
    username = form.get("username")
    password = form.get("password")
    next_url = request.query_params.get("next")

    users = load_users()
    u = users.get(username)

    if not u or not verify_pbkdf2(password, u.get("password_hash", "")):
        if next_url:
            return RedirectResponse(f"/login?error=1&next={next_url}", status_code=302)
        return RedirectResponse("/login?error=1", status_code=302)

    session_id = secrets.token_hex(32)

    SESSIONS[session_id] = {
        "username": username,
        "last_activity": int(time.time())
    }

    role = u.get("role", "user")

    if next_url == "/admin":

        if role == "admin":
            redirect_target = "/admin"
        else:
            redirect_target = "/forbidden"

    else:
        redirect_target = "/files"

    response = RedirectResponse(redirect_target, status_code=302)

    response.set_cookie(
        key="session_id",
        value=session_id,
        httponly=True,
        samesite="Lax"
    )

    return response

@app.get("/logout")
def logout(request: Request):
    session_id = request.cookies.get("session_id")
    if session_id in SESSIONS:
        username = SESSIONS[session_id]["username"]
        del SESSIONS[session_id]
        logger.info(f"User logged out: {username}")

    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session_id")
    return response

# =========================
# CLEANUP
# =========================

def get_items_dir():
    return DATA_DIR / "content" / "items"


def get_active_item_name():
    active_link = DATA_DIR / "active"
    if active_link.exists() and active_link.is_symlink():
        return active_link.resolve().name
    return None


def cleanup_old_items():
    items_dir = get_items_dir()
    active_name = get_active_item_name()

    settings = load_settings()
    max_items = settings["max_presentations"]

    all_items = sorted(
        [p for p in items_dir.glob("item_*") if p.is_dir()],
        key=lambda x: x.name
    )

    if len(all_items) <= max_items:
        return

    to_delete = all_items[:-max_items]

    for item in to_delete:
        if item.name == active_name:
            continue

        shutil.rmtree(item, ignore_errors=True)
        logger.info(f"Auto-cleanup removed {item.name}")

@app.get("/forbidden")
def forbidden():
    return HTMLResponse("""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Access Denied</title>
<style>

body {
    margin: 0;
    font-family: system-ui, -apple-system, sans-serif;
    background: #0f1115;
    color: #e6e6e6;
    display: flex;
    justify-content: center;
    align-items: center;
    height: 100vh;
}

.container {
    text-align: center;
}

h1 {
    font-size: 64px;
    margin-bottom: 20px;
    color: #ef4444;
    letter-spacing: 2px;
}

p {
    font-size: 18px;
    color: #9aa4b2;
    margin-bottom: 40px;
}

.back-btn {
    display: inline-block;
    padding: 12px 28px;
    border-radius: 8px;
    background: #3b82f6;
    color: white;
    text-decoration: none;
    font-weight: 600;
    transition: 0.2s;
}

.back-btn:hover {
    opacity: 0.85;
}

</style>
</head>

<body>

<div class="container">
    <h1>ACCESS DENIED</h1>
    <p>You do not have permission to access this page.</p>
    <a href="/files" class="back-btn">BACK</a>
</div>

</body>
</html>
""")

# =========================
# UPLOAD
# =========================

@app.post("/upload/pptx")
async def upload_pptx(request: Request, file: UploadFile = File(...)):
    get_session_user(request)

    if not file.filename or not file.filename.lower().endswith(".pptx"):
        return JSONResponse({"error": "only_pptx_allowed"}, status_code=400)

    content = await file.read()
    original_name = Path(file.filename).name

    if len(content) > MAX_UPLOAD_SIZE:
        return JSONResponse({"error": "file_too_large"}, status_code=400)

    ts = int(time.time())
    item_dir = UPLOAD_DIR / f"item_{ts}"
    source_dir = item_dir / "source"
    pdf_dir = item_dir / "rendered" / "pdf"
    slides_dir = item_dir / "rendered" / "slides"

    source_dir.mkdir(parents=True)
    pdf_dir.mkdir(parents=True)
    slides_dir.mkdir(parents=True)

    dst = source_dir / "upload.pptx"
    dst.write_bytes(content)

    subprocess.run(
        [
            "libreoffice",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(pdf_dir),
            str(dst),
        ],
        check=True,
        timeout=CONVERSION_TIMEOUT
    )

    pdf_files = glob.glob(str(pdf_dir / "*.pdf"))
    if not pdf_files:
        subprocess.run(["rm", "-rf", str(item_dir)], check=False)
        return JSONResponse({"error": "pdf_not_generated"}, status_code=500)

    subprocess.run(
        ["pdftoppm", "-png", pdf_files[0], str(slides_dir / "slide")],
        check=True,
        timeout=CONVERSION_TIMEOUT
    )

    active_link = DATA_DIR / "active"
    if active_link.exists() or active_link.is_symlink():
        active_link.unlink()
    active_link.symlink_to(item_dir)

    cleanup_old_items()

    slide_count = len(list(slides_dir.glob("*.png")))
    logger.info(f"Upload successful: {item_dir.name}")

    metadata = {
    "original_name": original_name,
    "slides": slide_count,
    "created_at": ts
    }

    (item_dir / "metadata.json").write_text(json.dumps(metadata))

    return {
        "uploaded": True,
        "item_id": item_dir.name,
        "slides_generated": slide_count
    }

# =========================
# NUMERIC SLIDE SORT
# =========================

def numeric_sort_key(name):
    match = re.search(r"(\d+)", name)
    return int(match.group(1)) if match else 0

# =========================
# ACTIVE SLIDES
# =========================

@app.get("/active/slides")
def active_slides():
    slides_dir = DATA_DIR / "active" / "rendered" / "slides"

    if not slides_dir.exists():
        return []

    files = sorted(
        [p.name for p in slides_dir.glob("*.png")],
        key=numeric_sort_key
    )

    return [f"/active/slide/{name}" for name in files]

@app.get("/active/slide/{filename}")
def active_slide_file(filename: str):
    slide_path = DATA_DIR / "active" / "rendered" / "slides" / filename
    if not slide_path.exists():
        raise HTTPException(status_code=404)
    return FileResponse(slide_path)

# =========================
# FILES PAGE
# =========================

@app.get("/files")
def files_page(request: Request):

    if is_setup_mode():
        return RedirectResponse("/setup", status_code=302)

    try:
        get_session_user(request)
        return FileResponse("/opt/es_monitor/backend/public/files.html")
    except:
        return RedirectResponse("/login", status_code=302)

# =========================
# ITEMS API
# =========================

@app.get("/api/items")
def api_list_items(request: Request):
    get_session_user(request)

    items_dir = get_items_dir()
    active_name = get_active_item_name()
    result = []

    for item_path in sorted(items_dir.glob("item_*")):
        if not item_path.is_dir():
            continue

        slides_dir = item_path / "rendered" / "slides"
        metadata_file = item_path / "metadata.json"

        slide_count = len(list(slides_dir.glob("*.png"))) if slides_dir.exists() else 0
        display_name = item_path.name

        if metadata_file.exists():
            try:
                metadata = json.loads(metadata_file.read_text())
                display_name = metadata.get("original_name", item_path.name)
            except:
                pass

        result.append({
            "id": item_path.name,
            "name": display_name,
            "slides": slide_count,
            "active": item_path.name == active_name
        })

    return result

@app.post("/api/items/{item_id}/activate")
def api_activate_item(item_id: str, request: Request):
    get_session_user(request)

    target = get_items_dir() / item_id
    if not target.exists():
        raise HTTPException(status_code=404)

    active_link = DATA_DIR / "active"
    if active_link.exists() or active_link.is_symlink():
        active_link.unlink()
    active_link.symlink_to(target)

    logger.info(f"Activated item: {item_id}")
    return {"activated": item_id}

@app.delete("/api/items/{item_id}")
def api_delete_item(item_id: str, request: Request):
    get_session_user(request)

    target = get_items_dir() / item_id
    if not target.exists():
        raise HTTPException(status_code=404)

    subprocess.run(["rm", "-rf", str(target)], check=False)
    logger.info(f"Deleted item: {item_id}")
    return {"deleted": item_id}

@app.get("/api/items/{item_id}/download")
def api_download_item(item_id: str, request: Request):
    get_session_user(request)

    target = get_items_dir() / item_id / "source" / "upload.pptx"
    if not target.exists():
        raise HTTPException(status_code=404)

    return FileResponse(target, filename=f"{item_id}.pptx")

# =========================
# DISK USAGE API
# =========================

@app.get("/api/disk")
def api_disk_usage(request: Request):
    get_session_user(request)

    total, used, free = shutil.disk_usage(str(BASE_DIR))
    percent = round((used / total) * 100, 1)

    return {
        "total": total,
        "used": used,
        "free": free,
        "percent": percent
    }

# =========================
# ADMIN PANEL
# =========================

@app.get("/admin")
def admin_page(request: Request):

    if is_setup_mode():
        return RedirectResponse("/setup", status_code=302)

    try:
        require_admin(request)
        return FileResponse("/opt/es_monitor/backend/public/admin.html")
    except:
        return RedirectResponse("/login?next=/admin", status_code=302)

# =========================
# SETUP 
# =========================

@app.get("/setup")
def setup_page():

    if not is_setup_mode():
        return RedirectResponse("/login", status_code=302)

    return FileResponse("/opt/es_monitor/backend/public/setup.html")

@app.post("/api/setup")
async def api_setup_create_admin(request: Request):

    if not is_setup_mode():
        return JSONResponse({"error": "already_initialized"}, status_code=400)

    try:
        data = await request.json()
        username = (data.get("username") or "").strip()
        password = (data.get("password") or "").strip()

        if not username or not password:
            return JSONResponse({"error": "missing_fields"}, status_code=400)

        users = load_users()

        if username in users:
            return JSONResponse({"error": "user_exists"}, status_code=400)

        users[username] = {
            "password_hash": generate_pbkdf2(password),
            "role": "admin"
        }

        save_users(users)

        return {"status": "created"}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# =========================
# PLAYER
# =========================
@app.get("/api/settings")
def public_settings():
    return load_settings()

@app.get("/player", response_class=HTMLResponse)
def player_page():
    return """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Player</title>
<style>
html, body {
    margin: 0;
    padding: 0;
    width: 100%;
    height: 100%;
    overflow: hidden;
    background: #000;
}
#slide {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
}
</style>
</head>
<body>
<img id="slide">

<script>
const SLIDES_URL = "/active/slides";

let slides = [];
let idx = 0;
let slideDuration = 30000;
let slideInterval = null;

async function loadSettings() {
    try {
        const r = await fetch("/api/settings");
        if (!r.ok) return;

        const data = await r.json();
        slideDuration = data.slide_duration_ms || 30000;

    } catch (e) {
        console.log("Settings load error", e);
    }
}

async function loadSlides() {
    const r = await fetch(SLIDES_URL + "?t=" + Date.now());
    slides = await r.json();
    idx = 0;
}

function showSlide() {
    if (!slides.length) return;

    document.getElementById("slide").src =
        slides[idx] + "?t=" + Date.now();

    idx++;
    if (idx >= slides.length) idx = 0;
}

async function start() {
    await loadSettings();
    await loadSlides();

    showSlide();

    if (slideInterval) {
        clearInterval(slideInterval);
    }

    slideInterval = setInterval(showSlide, slideDuration);
}

start();

let lastSignature = "";

setInterval(async () => {
    const r = await fetch(SLIDES_URL + "?t=" + Date.now());
    const newSlides = await r.json();
    const sig = JSON.stringify(newSlides);

    if (lastSignature && sig !== lastSignature) {
        location.reload();
    }

    lastSignature = sig;
}, 10000);
</script>

</body>
</html>
"""
# =========================
# ADMIN API 
# =========================

import platform
import socket
import psutil

@app.get("/api/admin/system")
def api_admin_system(request: Request):
    require_admin(request)

    hostname = socket.gethostname()
    uptime_seconds = int(time.time() - psutil.boot_time())
    os_version = platform.platform()

    ip_address = "unknown"
    mac_address = "unknown"

    try:
        interfaces = psutil.net_if_addrs()

        for iface, addrs in interfaces.items():

            if iface == "lo":
                continue

            current_ip = None
            current_mac = None

            for addr in addrs:

                if addr.family == socket.AF_INET:
                    current_ip = addr.address

                if hasattr(psutil, "AF_LINK") and addr.family == psutil.AF_LINK:
                    current_mac = addr.address

            if current_ip:
                ip_address = current_ip
                if current_mac:
                    mac_address = current_mac
                break

    except Exception as e:
        print("Network detection error:", e)

    cpu_load = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()

    temperature = "N/A"
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            temp_raw = int(f.read().strip())
            temperature = f"{temp_raw / 1000:.1f}°C"
    except Exception:
        pass

    return {
        "hostname": hostname,
        "uptime_seconds": uptime_seconds,
        "os_version": os_version,
        "ip": ip_address,
        "mac": mac_address,
        "cpu_load": cpu_load,
        "ram_used_mb": round(ram.used / 1024 / 1024, 1),
        "ram_total_mb": round(ram.total / 1024 / 1024, 1),
        "temperature": temperature
    }

@app.get("/api/admin/system/services")
def api_admin_system_services(request: Request):
    require_admin(request)

    import subprocess

    services_to_check = [
        ("BACKEND", "es-monitor-backend.service"),
        ("PLAYER", "es-monitor-kiosk.service")
    ]

    result = []

    for display_name, service in services_to_check:

        try:
            process = subprocess.run(
                ["sudo", "systemctl", "is-active", service],
                capture_output=True,
                text=True
            )

            status = process.stdout.strip()

            if status not in ["active", "inactive", "failed", "activating"]:
                status = "unknown"

        except Exception:
            status = "unknown"

        result.append({
            "name": display_name,
            "status": status
        })

    return result
# =========================
# ADMIN NETWORK API
# =========================

NETWORK_CONNECTION = "netplan-eth0"

@app.get("/api/admin/network")
def api_admin_network(request: Request):
    require_admin(request)

    NETPLAN_FILE = "/etc/netplan/90-NM-75a1216a-9d1a-30cd-8aca-ace5526ec021.yaml"

    try:
        import yaml
        import subprocess
        import re

        result = subprocess.run(
            ["sudo", "cat", NETPLAN_FILE],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            raise Exception(result.stderr)

        config = yaml.safe_load(result.stdout)

        eth0 = config.get("network", {}).get("ethernets", {}).get("eth0", {})

        method = "auto" if eth0.get("dhcp4", False) else "manual"

        ip = ""
        mask = ""
        gateway = ""
        dns = ""

        if method == "manual":

            addresses = eth0.get("addresses", [])
            if addresses:
                ip_cidr = addresses[0]
                if "/" in ip_cidr:
                    ip, cidr = ip_cidr.split("/")
                    cidr = int(cidr)

                    mask_bits = (0xffffffff >> (32 - cidr)) << (32 - cidr)
                    mask = ".".join(
                        str((mask_bits >> (8 * i)) & 0xff)
                        for i in reversed(range(4))
                    )

            routes = eth0.get("routes", [])
            for r in routes:
                if r.get("to") == "0.0.0.0/0":
                    gateway = r.get("via", "")
                    break

            nameservers = eth0.get("nameservers", {})
            dns_list = nameservers.get("addresses", [])
            if dns_list:
                dns = dns_list[0]

        else:
            ip_output = subprocess.check_output(
                ["ip", "-4", "addr", "show", "eth0"]
            ).decode()

            match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", ip_output)
            if match:
                ip = match.group(1)
                cidr = int(match.group(2))

                mask_bits = (0xffffffff >> (32 - cidr)) << (32 - cidr)
                mask = ".".join(
                    str((mask_bits >> (8 * i)) & 0xff)
                    for i in reversed(range(4))
                )

            route_output = subprocess.check_output(
                ["ip", "route"]
            ).decode()

            match = re.search(r"default via (\d+\.\d+\.\d+\.\d+)", route_output)
            if match:
                gateway = match.group(1)

        return {
            "method": method,
            "ip": ip,
            "mask": mask,
            "gateway": gateway,
            "dns": dns,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)

@app.post("/api/admin/network")
async def api_admin_network_update(request: Request):
    require_admin(request)

    data = await request.json()

    method = (data.get("method") or "").strip()
    ip = (data.get("ip") or "").strip()
    mask = (data.get("mask") or "").strip()
    gateway = (data.get("gateway") or "").strip()
    dns = (data.get("dns") or "").strip()

    NETPLAN_FILE = "/etc/netplan/90-NM-75a1216a-9d1a-30cd-8aca-ace5526ec021.yaml"

    try:
        import yaml
        import ipaddress
        import subprocess

        if method not in ["auto", "manual"]:
            return JSONResponse({"error": "invalid_method"}, status_code=400)

        if method == "manual":

            if not ip or not mask or not gateway:
                return JSONResponse({"error": "missing_fields"}, status_code=400)

            try:
                ip_obj = ipaddress.IPv4Address(ip)
                gw_obj = ipaddress.IPv4Address(gateway)
            except ValueError:
                return JSONResponse({"error": "invalid_ip"}, status_code=400)

            if dns:
                try:
                    ipaddress.IPv4Address(dns)
                except ValueError:
                    return JSONResponse({"error": "invalid_dns"}, status_code=400)

            try:
                network_test = ipaddress.IPv4Network(f"0.0.0.0/{mask}")
                cidr = network_test.prefixlen
            except Exception:
                return JSONResponse({"error": "invalid_mask"}, status_code=400)

            network = ipaddress.IPv4Network(f"{ip}/{cidr}", strict=False)

            if ip_obj == network.network_address:
                return JSONResponse({"error": "ip_is_network_address"}, status_code=400)

            if ip_obj == network.broadcast_address:
                return JSONResponse({"error": "ip_is_broadcast"}, status_code=400)

            if gw_obj not in network:
                return JSONResponse({"error": "gateway_outside_subnet"}, status_code=400)

        subprocess.run(
            ["sudo", "cp", NETPLAN_FILE, NETPLAN_FILE + ".bak"],
            check=False,
        )

        result = subprocess.check_output(
            ["sudo", "cat", NETPLAN_FILE]
        ).decode()

        config = yaml.safe_load(result)

        config.setdefault("network", {})
        config["network"]["renderer"] = "networkd"
        config["network"]["version"] = 2
        config["network"].setdefault("ethernets", {})
        config["network"]["ethernets"]["eth0"] = {}

        eth0 = config["network"]["ethernets"]["eth0"]

        if method == "auto":
            eth0["dhcp4"] = True

        else:
            eth0["dhcp4"] = False
            eth0["addresses"] = [f"{ip}/{cidr}"]
            eth0["routes"] = [
                {
                    "to": "0.0.0.0/0",
                    "via": gateway
                }
            ]

            if dns:
                eth0["nameservers"] = {
                    "addresses": [dns]
                }

        yaml_content = yaml.dump(config, sort_keys=False)

        subprocess.run(
            ["sudo", "tee", NETPLAN_FILE],
            input=yaml_content.encode(),
            check=True,
        )

        subprocess.run(["sudo", "netplan", "generate"], check=True)
        subprocess.run(["sudo", "netplan", "apply"], check=True)

        return {"status": "updated"}

    except subprocess.CalledProcessError as e:
        return JSONResponse(
            {"error": f"netplan_failed: {str(e)}"},
            status_code=500,
        )
    except Exception as e:
        return JSONResponse(
            {"error": str(e)},
            status_code=500,
        )

# =========================
# ADMIN SECURITY API
# =========================

def save_users(users_dict):
    data = {"users": users_dict}
    SECURITY_FILE.write_text(json.dumps(data, indent=2))

def generate_pbkdf2(password: str) -> str:
    salt = secrets.token_bytes(16)
    rounds = 260000

    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        salt,
        rounds
    )

    return "pbkdf2_sha256$%d$%s$%s" % (
        rounds,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode()
    )

@app.get("/api/admin/users")
def api_admin_users(request: Request):
    require_admin(request)

    users = load_users()

    result = []
    for username, data in users.items():
        result.append({
            "username": username,
            "role": data.get("role", "user")
        })

    return result

@app.post("/api/admin/users")
async def api_admin_add_user(request: Request):
    require_admin(request)

    data = await request.json()

    username = (data.get("username") or "").strip()
    password = (data.get("password") or "").strip()
    role = (data.get("role") or "user").strip()

    if not username or not password:
        return JSONResponse({"error": "missing_fields"}, status_code=400)

    users = load_users()

    if username in users:
        return JSONResponse({"error": "user_exists"}, status_code=400)

    users[username] = {
        "password_hash": generate_pbkdf2(password),
        "role": role if role in ["admin", "user"] else "user"
    }

    save_users(users)

    return {"status": "created"}

@app.put("/api/admin/users/{username}")
async def api_admin_update_user(username: str, request: Request):
    session = require_admin(request)
    current_user = session["username"]

    data = await request.json()

    new_password = (data.get("password") or "").strip()
    new_role = data.get("role")

    users = load_users()

    if username not in users:
        raise HTTPException(status_code=404)

    if new_password:
        users[username]["password_hash"] = generate_pbkdf2(new_password)

    if new_role in ["admin", "user"]:
        if users[username]["role"] == "admin" and new_role == "user":
            admin_count = sum(1 for u in users.values() if u["role"] == "admin")
            if admin_count <= 1:
                return JSONResponse({"error": "last_admin"}, status_code=400)

        users[username]["role"] = new_role

    save_users(users)

    return {"status": "updated"}

@app.delete("/api/admin/users/{username}")
def api_admin_delete_user(username: str, request: Request):
    session = require_admin(request)
    current_user = session["username"]

    users = load_users()

    if username not in users:
        raise HTTPException(status_code=404)

    if username == current_user:
        return JSONResponse({"error": "cannot_delete_self"}, status_code=400)

    if users[username]["role"] == "admin":
        admin_count = sum(1 for u in users.values() if u["role"] == "admin")
        if admin_count <= 1:
            return JSONResponse({"error": "last_admin"}, status_code=400)

    del users[username]
    save_users(users)

    return {"status": "deleted"}


# =========================
# ADMIN MAINTENANCE API
# =========================

@app.post("/api/admin/maintenance/restart-player")
def restart_player(request: Request):
    require_admin(request)

    try:
        subprocess.run(
            ["sudo", "systemctl", "restart", "es-monitor-kiosk.service"],
            check=True
        )
        logger.info("Maintenance: Player restarted")
        return {"status": "player_restarted"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/admin/maintenance/reboot")
def reboot_system(request: Request):
    require_admin(request)

    try:
        logger.info("Maintenance: System reboot requested")
        subprocess.Popen(["sudo", "reboot"])
        return {"status": "rebooting"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/admin/maintenance/shutdown")
def shutdown_system(request: Request):
    require_admin(request)

    try:
        logger.info("Maintenance: System shutdown requested")
        subprocess.Popen(["sudo", "shutdown", "-h", "now"])
        return {"status": "shutting_down"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/admin/maintenance/clear-logs")
def clear_logs(request: Request):
    require_admin(request)

    try:
        logs_dir = BASE_DIR / "logs"
        for file in logs_dir.glob("*.log"):
            file.unlink(missing_ok=True)

        logger.info("Maintenance: Logs cleared")
        return {"status": "logs_cleared"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/admin/maintenance/reset-active")
def reset_active(request: Request):
    require_admin(request)

    try:
        active_link = DATA_DIR / "active"

        if active_link.exists() or active_link.is_symlink():
            active_link.unlink()

        logger.info("Maintenance: Active presentation reset")
        return {"status": "active_reset"}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

# =========================
# ADMIN LOGS API
# =========================

def get_journal_logs(service_name: str, lines: int = 100):
    try:
        output = subprocess.check_output(
            [
                "journalctl",
                "-u",
                service_name,
                "-n",
                str(lines),
                "--no-pager"
            ]
        ).decode()

        return output
    except Exception as e:
        return f"Error reading journal: {str(e)}"


@app.get("/api/admin/logs/backend")
def logs_backend_file(request: Request):
    require_admin(request)

    try:
        log_file = BASE_DIR / "logs" / "backend.log"

        if not log_file.exists():
            return {"log": "No backend.log file found"}

        lines = log_file.read_text().splitlines()
        last_lines = "\n".join(lines[-100:])

        return {"log": last_lines}

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/admin/logs/backend-service")
def logs_backend_service(request: Request):
    require_admin(request)

    log = get_journal_logs("es-monitor-backend.service")
    return {"log": log}


@app.get("/api/admin/logs/kiosk-service")
def logs_kiosk_service(request: Request):
    require_admin(request)

    log = get_journal_logs("es-monitor-kiosk.service")
    return {"log": log}

# =========================
# ADMIN SETTINGS API
# =========================

@app.get("/api/admin/settings")
def get_settings(request: Request):
    require_admin(request)
    return load_settings()


@app.post("/api/admin/settings")
async def update_settings(request: Request):
    require_admin(request)

    data = await request.json()

    slide_duration = int(data.get("slide_duration_ms", 30000))
    max_presentations = int(data.get("max_presentations", 10))

    if slide_duration < 5000 or slide_duration > 300000:
        return JSONResponse({"error": "invalid_slide_duration"}, status_code=400)

    if max_presentations < 1 or max_presentations > 100:
        return JSONResponse({"error": "invalid_max_presentations"}, status_code=400)

    new_settings = {
        "slide_duration_ms": slide_duration,
        "max_presentations": max_presentations
    }

    save_settings(new_settings)

    return {"status": "updated"}
