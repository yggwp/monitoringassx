#!/bin/bash

# AssistX POC Monitoring - Automated VPS Setup Script (Ubuntu)
# Usage: sudo bash setup_vps.sh

echo "------------------------------------------------"
echo " AssistX Monitoring - Deploying to Production "
echo "------------------------------------------------"

# 1. Update and install dependencies
apt update && apt upgrade -y
apt install -y python3-pip python3-venv nginx gunicorn git sqlite3

# 2. Create app directory if not exists
PROJECT_DIR="/opt/assistx-monitoring"
mkdir -p $PROJECT_DIR
# Delete old database if exists to ensure fresh start on deployment
[ -f "$PROJECT_DIR/history.db" ] && rm "$PROJECT_DIR/history.db"
cp -r . $PROJECT_DIR
cd $PROJECT_DIR

# 3. Setup Virtual Environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gunicorn gevent

# 4. Create Systemd Service
cat <<EOF > /etc/systemd/system/assistx.service
[Unit]
Description=AssistX POC Monitoring Dashboard
After=network.target

[Service]
User=root
Group=root
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$PROJECT_DIR/venv/bin"
ExecStart=$PROJECT_DIR/venv/bin/gunicorn --workers 1 --worker-class gevent --bind 0.0.0.0:5050 app:app

[Install]
WantedBy=multi-user.target
EOF

# 5. Start and Enable Service
systemctl daemon-reload
systemctl enable assistx
systemctl restart assistx

# 6. Setup Firewall (Optional)
ufw allow 5050
ufw allow 80
ufw allow 443

echo "------------------------------------------------"
echo " DEPLOYMENT COMPLETE! "
echo " Dashboard running at: http://YOUR_VPS_IP:5050 "
echo "------------------------------------------------"
