# Panduan Instalasi Anydesk Exporter di Linux

Script `anydesk_exporter.py` ini akan di-setup sebagai **Background Service** menggunakan `systemd` agar program ini akan berjalan secara tersembunyi (*background*) dan akan menyala otomatis setiap kali PC Linux Klien (POC) baru dihidupkan/di-restart.

## 1. Persiapan Awal (Install Python)
Pastikan sistem operasi klien sudah memiliki Python 3:
```bash
sudo apt update
sudo apt install python3 python3-pip python3-venv -y
```

## 2. Pindahkan File ke Folder Sistem
Buat direktori khusus di `/opt` untuk meletekkan script monitoring, lalu salin/pindahkan file `anydesk_exporter.py` dan `requirements.txt` ke dalamnya. (Asumsi: saat ini Anda berada di folder tempat file tersebut di-download)
```bash
sudo mkdir -p /opt/poc-monitoring
sudo cp anydesk_exporter.py /opt/poc-monitoring/
sudo cp requirements.txt /opt/poc-monitoring/
```

## 3. Install Dependensi (Virtual Environment)
Sangat direkomendasikan menggunakan *Virtual Environment* (venv) supaya package `flask` dan `psutil` terisolasi dan tidak bertabrakan dengan sistem bawaan Linux.
```bash
cd /opt/poc-monitoring
sudo python3 -m venv venv
sudo /opt/poc-monitoring/venv/bin/pip install -r requirements.txt
```

## 4. Buat Systemd Service
Inilah kunci ager aplikasi ini berjalan otomatis. Kita akan membuat sebuah *Service File*:
```bash
sudo nano /etc/systemd/system/anydesk-exporter.service
```
Lalu, **Copy dan Paste** teks di bawah ini ke dalam editor `nano` tersebut:
```ini
[Unit]
Description=Anydesk Monitoring Exporter (Port 9800)
After=network.target

[Service]
User=root
WorkingDirectory=/opt/poc-monitoring
ExecStart=/opt/poc-monitoring/venv/bin/python anydesk_exporter.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
*(Tekan `CTRL+X`, lalu `Y`, dan tekan `Enter` untuk menyimpannya)*

## 5. Mengaktifkan Service
Jalankan beberapa command berikut secara berurutan untuk me-load *service* yang baru saja dibuat agar menyala secara otomatis:
```bash
sudo systemctl daemon-reload
sudo systemctl enable anydesk-exporter
sudo systemctl start anydesk-exporter
```

## 6. Uji Coba Status Exporter (Selesai!)
Untuk memastikan *exporter* telah berjalan dengan lancar dan tidak ada error:
```bash
sudo systemctl status anydesk-exporter
```
*(Anda seharusnya melihat tulisan berwarna hijau **"active (running)"**)*

Coba tes tarikan metric di komputer milik klien secara lokal lewat terminal:
```bash
curl http://localhost:9800/metrics
```
Jika command `curl` langsung mengeluarkan teks berisi `anydesk_status`, `cpu_usage`, dan `memory_usage`, maka instalasi Linux ini dianggap **BERHASIL/SUKSES 100%**. Dashboard utama Anda kini sudah bisa menghubungi IP Klien ini.

---

## 7. Cara Melakukan Update/Pembaruan Skrip (Jika sudah Terinstall)
Jika ada pembaruan *bug-fix* atau optimalisasi baru pada skrip `anydesk_exporter.py` dan Anda ingin memperbaruinya pada klien yang perakitannya sudah berjalan:

1. **Siapkan Skrip Terbaru**: Download atau pindahkan file `anydesk_exporter.py` yang terbaru ke PC Linux Klien tersebut.
2. **Timpa file lamanya** di folder tempat instalasi sistem `/opt`:
```bash
sudo cp anydesk_exporter.py /opt/poc-monitoring/anydesk_exporter.py
```
3. **Restart Layanannya** agar file Python baru langsung diproses di dalam memori Background Service:
```bash
sudo systemctl restart anydesk-exporter
```
*(Selesai! Anda tidak perlu melakukan ulang langkah konfigurasi dari awal atau install ulang package).*
