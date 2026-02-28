#!/bin/bash

set -e

APP_NAME="es_monitor"
INSTALL_DIR="/opt/es_monitor"
SERVICE_DIR="/etc/systemd/system"
APP_USER="es_admin"

echo "=== Installing $APP_NAME ==="

# --------------------------------------------------
# 1. Root check
# --------------------------------------------------
if [ "$EUID" -ne 0 ]; then
  echo "Run as root: sudo ./install.sh"
  exit 1
fi

# --------------------------------------------------
# 2. System dependencies
# --------------------------------------------------
echo "Installing system dependencies..."

apt update
apt install -y \
    python3 \
    python3-venv \
    python3-pip \
    xserver-xorg \
    chromium \
    libffi-dev \
    libssl-dev \
    libreoffice \
    libreoffice-impress \
    poppler-utils

# --------------------------------------------------
# 3. Disable NetworkManager (we use networkd)
# --------------------------------------------------
echo "Configuring network stack..."

systemctl disable NetworkManager 2>/dev/null || true
systemctl stop NetworkManager 2>/dev/null || true

systemctl enable systemd-networkd
systemctl start systemd-networkd

chmod 600 /etc/netplan/*.yaml 2>/dev/null || true
chmod 600 /lib/netplan/*.yaml 2>/dev/null || true

# --------------------------------------------------
# 4. Create application user
# --------------------------------------------------
if id "$APP_USER" &>/dev/null; then
    echo "User $APP_USER already exists"
else
    echo "Creating user $APP_USER"
    useradd -m -s /bin/bash "$APP_USER"
fi

usermod -aG systemd-journal "$APP_USER"

# --------------------------------------------------
# 5. Create application directory
# --------------------------------------------------
echo "Creating application directory..."

rm -rf $INSTALL_DIR
mkdir -p $INSTALL_DIR
mkdir -p $INSTALL_DIR/data/config
mkdir -p $INSTALL_DIR/data/content
mkdir -p $INSTALL_DIR/logs

# --------------------------------------------------
# 6. Copy files
# --------------------------------------------------
echo "Copying application files..."

cp -r backend $INSTALL_DIR/
cp -r player $INSTALL_DIR/
cp -r config.example $INSTALL_DIR/

# --------------------------------------------------
# 7. Virtualenv
# --------------------------------------------------
echo "Creating virtual environment..."

python3 -m venv $INSTALL_DIR/backend/venv
source $INSTALL_DIR/backend/venv/bin/activate
pip install --upgrade pip
pip install -r $INSTALL_DIR/backend/requirements.txt
deactivate

# --------------------------------------------------
# 8. Default config
# --------------------------------------------------
echo "Copying default configuration..."

cp $INSTALL_DIR/config.example/security.json $INSTALL_DIR/data/config/security.json
cp $INSTALL_DIR/config.example/settings.json $INSTALL_DIR/data/config/settings.json

# --------------------------------------------------
# 9. Permissions
# --------------------------------------------------
echo "Setting permissions..."

chown -R $APP_USER:$APP_USER $INSTALL_DIR
chmod -R 755 $INSTALL_DIR
chmod +x $INSTALL_DIR/player/start_kiosk.sh

# --------------------------------------------------
# 10. Sudoers (minimal required)
# --------------------------------------------------
echo "Configuring sudo permissions..."

SUDOERS_FILE="/etc/sudoers.d/es_monitor"

cat <<EOF > $SUDOERS_FILE
$APP_USER ALL=(root) NOPASSWD: /usr/sbin/netplan
$APP_USER ALL=(root) NOPASSWD: /usr/bin/systemctl
$APP_USER ALL=(root) NOPASSWD: /usr/bin/cp
$APP_USER ALL=(root) NOPASSWD: /usr/bin/cat
$APP_USER ALL=(root) NOPASSWD: /usr/bin/tee
$APP_USER ALL=(root) NOPASSWD: /bin/rm
$APP_USER ALL=(root) NOPASSWD: /usr/sbin/reboot
$APP_USER ALL=(root) NOPASSWD: /usr/sbin/shutdown
EOF

chmod 440 $SUDOERS_FILE

# --------------------------------------------------
# 11. Install systemd services
# --------------------------------------------------
echo "Installing systemd services..."

cp systemd/es-monitor-backend.service $SERVICE_DIR/
cp systemd/es-monitor-kiosk.service $SERVICE_DIR/

systemctl daemon-reload
systemctl enable es-monitor-backend.service
systemctl enable es-monitor-kiosk.service

# --------------------------------------------------
# 12. Start services
# --------------------------------------------------
echo "Starting services..."

systemctl restart es-monitor-backend.service
systemctl restart es-monitor-kiosk.service

echo ""
echo "=== Installation complete ==="
echo "Access panel at: http://<DEVICE_IP>:8080"
echo "Recommended: reboot after installation."
