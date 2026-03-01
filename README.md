# ES Monitor

ES Monitor is a lightweight Raspberry Pi based presentation display system designed for kiosks, information screens, and internal corporate displays.

The system allows administrators to upload PowerPoint presentations, automatically convert them into slide images, and display them in full-screen kiosk mode using Chromium.

This project is intended to be free and open for commercial and non-commercial use.

---

# Features

- PowerPoint (.pptx) upload and automatic conversion
- Slide rendering to PNG
- Full-screen kiosk mode (Chromium)
- Web-based administration panel
- Network configuration (DHCP / Static)
- User management (admin / user roles)
- Service monitoring
- System information dashboard
- Configurable slide duration
- Configurable max stored presentations
- Log viewer
- Safe maintenance controls (restart services, reset active presentation)

---

# Architecture

ES Monitor consists of two main components:

### 1. Backend (FastAPI)
- REST API
- Admin panel
- File management
- Configuration handling
- Network configuration via Netplan
- Service control via systemd

### 2. Player (Chromium Kiosk)
- Displays active slides
- Automatically reloads on content change
- Reads slide duration from settings

---

# Repository Structure

```
es_monitor/
├── backend/              # FastAPI application
│   ├── main.py
│   ├── public/           # HTML/CSS/JS frontend
│   └── requirements.txt
├── player/               # Kiosk startup scripts
├── config.example/       # Example configuration files
├── systemd/              # Service unit files
├── install.sh            # Automated installer
└── README.md
```

---

# System Requirements

- Raspberry Pi 3/4/5
- Raspberry Pi OS (Bookworm or Bullseye)
- Python 3.11+
- Xorg
- Chromium
- systemd

---

# Installation

Clone the repository:

```bash
git clone https://github.com/Mi3cheL/es_monitor.git
cd es_monitor
```

Run installer:

```bash
sudo ./install.sh
```

After installation:

```
http://<DEVICE_IP>:8080
```

---

# First Login

The administrator account configuration is stored in:

```
/opt/es_monitor/data/config/security.json
```

File is automatically created during the initial setup process when accessing the web interface for the first time and creating the administrator account.

Password hashes use PBKDF2 SHA256.

Example:
json
{
  "users": {
    "admin": {
      "password_hash": "<generated_hash>",
      "role": "admin"
    }
  }
}

---

# Configuration

Settings file:

```
/opt/es_monitor/data/config/settings.json
```

Example:

```json
{
  "slide_duration_ms": 30000,
  "max_presentations": 10
}
```

---

# API Endpoints

### System
- `GET /api/admin/system`
- `GET /api/admin/system/services`

### Network
- `GET /api/admin/network`
- `POST /api/admin/network`

### Users
- `GET /api/admin/users`
- `POST /api/admin/users`
- `PUT /api/admin/users/{username}`
- `DELETE /api/admin/users/{username}`

### Settings
- `GET /api/admin/settings`
- `POST /api/admin/settings`

### Maintenance
- `POST /api/admin/maintenance/reset-active`
- `POST /api/admin/maintenance/restart-backend`
- `POST /api/admin/maintenance/restart-player`

---

# How Slide Rendering Works

1. Upload .pptx file
2. LibreOffice converts to PDF
3. pdftoppm converts PDF to PNG slides
4. Slides stored in `/data/content/items`
5. Active presentation is a symbolic link
6. Player reads `/active/slides` endpoint

---

# Services

Systemd units:

- `es-monitor-backend.service`
- `es-monitor-kiosk.service`

Manage manually:

```bash
sudo systemctl status es-monitor-backend
sudo systemctl restart es-monitor-backend
```

---

# Security Model

- Session-based authentication
- Role-based access control
- Admin-only system actions
- PBKDF2 password hashing
- No default credentials
- Network validation before applying static IP

---

# Development Notes

The repository does not contain:

- Runtime logs
- Uploaded presentations
- Virtual environments
- System-specific files

Virtual environment is created during installation.

---

# Roadmap

- Backup & Restore
- Multi-playlist support
- Remote management
- Docker support
- Web-based initial setup wizard

---

# License

ES Monitor is released under the GNU General Public License v3.0 (GPLv3).

You are free to:

- Use the software
- Modify it
- Distribute it

Under the condition that:

- Any distributed or commercial version must also be licensed under GPLv3
- Source code must remain available

See the LICENSE file for full terms.

---

# Donations

This project is free to use.

If it helps your organization, voluntary donations are welcome to support further development.

---

# Author

Developed as an independent open-source kiosk display system.

---

# Disclaimer

Use at your own risk.  
Test thoroughly before deploying in production environments.
