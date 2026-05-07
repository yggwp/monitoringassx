#!/bin/bash

# Pastikan script dijalankan sebagai root
if [ "$EUID" -ne 0 ]; then
  echo "Harap jalankan dengan sudo (sudo bash setup_ngrok.sh)"
  exit 1
fi

echo "=========================================="
echo " MENGINSTALL DAN MENGKONFIGURASI NGROK "
echo "=========================================="

# 1. Install Ngrok via APT
echo "-> Menambahkan repository Ngrok..."
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | tee /etc/apt/keyrings/ngrok.asc >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/ngrok.asc] https://ngrok-agent.s3.amazonaws.com buster main" | tee /etc/apt/sources.list.d/ngrok.list

echo "-> Install paket ngrok..."
apt update && apt install -y ngrok

# 2. Set Authtoken
echo "-> Menyimpan Authtoken..."
ngrok config add-authtoken 3DP7KRmPWl6VojsWSW6dBVgBnxG_4YgoGhyUiUA9EvDvAU4J5

# 3. Membuat Systemd Service untuk Ngrok
echo "-> Membuat service latar belakang..."
cat <<EOF > /etc/systemd/system/assistx_ngrok.service
[Unit]
Description=AssistX Ngrok Tunnel
After=network.target

[Service]
User=root
# Mengarahkan domain ngrok ke port 5060 (langsung ke aplikasi Python kita)
ExecStart=/usr/bin/ngrok http --domain=depravity-wolf-cringe.ngrok-free.dev 5060
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 4. Menyalakan Service
echo "-> Menyalakan tunnel..."
systemctl daemon-reload
systemctl enable assistx_ngrok
systemctl restart assistx_ngrok

echo "=========================================="
echo " SELESAI! NGROK TUNNEL SUDAH AKTIF! "
echo "=========================================="
echo "URL Aplikasi Anda: https://depravity-wolf-cringe.ngrok-free.dev"
echo "Sekarang Anda bisa langsung membuka aplikasi APK di HP!"
