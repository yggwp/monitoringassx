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
PROJECT_DIR="/opt/assistx-monitoring-v2"
mkdir -p $PROJECT_DIR
# Ensure database is kept safe between deployments
cp -r . $PROJECT_DIR
cd $PROJECT_DIR

# 3. Setup Virtual Environment
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gunicorn gevent

# 4. Create Systemd Service
cat <<EOF > /etc/systemd/system/assistx_v2.service
[Unit]
Description=AssistX POC Monitoring Dashboard
After=network.target

[Service]
User=root
Group=root
WorkingDirectory=$PROJECT_DIR
Environment="PATH=$PROJECT_DIR/venv/bin"
ExecStart=$PROJECT_DIR/venv/bin/gunicorn --workers 1 --worker-class gevent --bind 0.0.0.0:5060 app:app

[Install]
WantedBy=multi-user.target
EOF

# 5. Stop Old Service & Start New Service
systemctl stop assistx || true
systemctl disable assistx || true

systemctl daemon-reload
systemctl enable assistx_v2
systemctl restart assistx_v2

# 6. Configure NGINX (Reverse Proxy)
cat <<EOF > /etc/nginx/sites-available/assistx_v2
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5060;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
    }
}
EOF

ln -sf /etc/nginx/sites-available/assistx_v2 /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
systemctl restart nginx

# 7. Setup Firewall (Optional)
ufw allow 5060
ufw allow 80
ufw allow 443

echo "------------------------------------------------"
echo " DEPLOYMENT COMPLETE! "
echo " Dashboard running at: http://YOUR_VPS_IP/ (Port 80 via NGINX)"
echo " Direct access also available at: http://YOUR_VPS_IP:5060 "
echo "------------------------------------------------"
