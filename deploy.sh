#!/usr/bin/env bash
# deploy.sh — Automated VPS setup for Decision Maker Finder
#
# Run this as root on a fresh Ubuntu 22.04 VPS:
#   chmod +x deploy.sh && ./deploy.sh
#
# After running: open http://YOUR_VPS_IP:5001 in your browser.

set -e  # Exit immediately on any error

APP_DIR="/opt/decision-maker-finder"
SERVICE_NAME="decision-maker-finder"
APP_USER="dmf"
PORT=5001

echo ""
echo "============================================"
echo "  Decision Maker Finder — VPS Setup"
echo "============================================"
echo ""

# ── 1. System update ────────────────────────────────────────────────────────
echo "[1/8] Updating system packages..."
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq python3 python3-pip python3-venv git curl ufw netcat-openbsd

# ── 2. Create app user ──────────────────────────────────────────────────────
echo "[2/8] Creating app user '$APP_USER'..."
if ! id "$APP_USER" &>/dev/null; then
    adduser --disabled-password --gecos "" "$APP_USER"
fi

# ── 3. Create app directory ─────────────────────────────────────────────────
echo "[3/8] Setting up app directory at $APP_DIR..."
mkdir -p "$APP_DIR"

# If deploy.sh is in the same directory as the app files, copy them
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/app.py" ]; then
    echo "       Copying app files from $SCRIPT_DIR..."
    rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude 'venv' \
        "$SCRIPT_DIR/" "$APP_DIR/"
else
    echo "       No app files found next to deploy.sh."
    echo "       Upload your project files to $APP_DIR and re-run."
    echo "       From your Mac:  scp -r <project_folder>/ root@\$(hostname -I | awk '{print \$1}'):$APP_DIR/"
fi

chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

# ── 4. Python virtual environment + dependencies ────────────────────────────
echo "[4/8] Installing Python dependencies..."
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/venv"
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/venv/bin/pip" install -q \
    flask \
    requests \
    "beautifulsoup4>=4.12" \
    lxml \
    dnspython \
    python-whois \
    tqdm \
    anthropic

echo "       Dependencies installed."

# ── 5. Firewall ─────────────────────────────────────────────────────────────
echo "[5/8] Configuring firewall..."
ufw --force reset > /dev/null 2>&1
ufw default deny incoming   > /dev/null 2>&1
ufw default allow outgoing  > /dev/null 2>&1
ufw allow 22    comment "SSH"
ufw allow "$PORT" comment "Decision Maker Finder UI"
# Port 25 outbound is controlled by the OUTPUT chain, not UFW INPUT — no rule needed
ufw --force enable > /dev/null 2>&1
echo "       Firewall: SSH (22) and app ($PORT) open."

# ── 6. systemd service ──────────────────────────────────────────────────────
echo "[6/8] Installing systemd service..."
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << EOF
[Unit]
Description=Decision Maker Finder (Flask)
After=network.target

[Service]
Type=simple
User=${APP_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/venv/bin/python3 ${APP_DIR}/app.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
echo "       Service '$SERVICE_NAME' enabled and started."

# ── 7. Verify SMTP port 25 ──────────────────────────────────────────────────
echo "[7/8] Checking SMTP port 25 outbound..."
if nc -zw5 gmail-smtp-in.l.google.com 25 2>/dev/null; then
    echo "       ✅ Port 25 is OPEN — SMTP email verification will work!"
else
    echo "       ⚠️  Port 25 appears BLOCKED on this VPS."
    echo "       Contact your VPS provider to open outbound port 25."
    echo "       (Hetzner/DigitalOcean/Vultr typically open it on request)"
fi

# ── 8. Done ─────────────────────────────────────────────────────────────────
VPS_IP=$(hostname -I | awk '{print $1}')
echo ""
echo "[8/8] ✅ Setup complete!"
echo ""
echo "  App URL:  http://${VPS_IP}:${PORT}"
echo ""
echo "  Useful commands:"
echo "    View logs:      journalctl -u $SERVICE_NAME -f"
echo "    Restart:        systemctl restart $SERVICE_NAME"
echo "    Stop:           systemctl stop $SERVICE_NAME"
echo "    Update & sync:  rsync -avz --exclude '__pycache__' --exclude 'venv' <local_dir>/ root@${VPS_IP}:${APP_DIR}/"
echo "                    then: systemctl restart $SERVICE_NAME"
echo ""
