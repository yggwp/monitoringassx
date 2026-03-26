# 🚀 AssistX POC Monitoring Assistant

A premium, real-time monitoring dashboard designed for managing multiple POC (Proof of Concept) locations with ease. Built with **Flask**, **SQLite**, and a stunning **Glassmorphism** UI.

![Dashboard Preview](https://via.placeholder.com/1200x600/0a0b12/f0f2f8?text=AssistX+POC+Monitoring+Dashboard+Preview)

## ✨ Features

-   **💎 Premium Glassmorphism UI**: Modern, responsive design with backdrop blurs and floating aesthetics.
-   **⏱️ Real-time Telemetry**: 5-second polling interval for CPU, Memory, and AnyDesk status.
-   **📊 7-Day History Logs**: Automated telemetry recording with 7-day rolling window and weekly maintenance.
-   **📩 Intelligent SMTP Alerts**:
    *   **Offline/Unreachable Notifications**: Immediate alerts when a location is down.
    *   **Recovery Emails**: Automatic notification when a location is back online, including total downtime duration.
    *   **Cooldown Management**: Prevents inbox spamming during flapping network conditions.
-   **🔋 Internet Quota Tracking**: Integrated Telkomsel/Digipos quota scraping with intelligent 10-minute caching.
-   **🛠️ CRUD Management**: Easily add, edit, or remove POC locations directly from the dashboard.

## 🏗️ Architecture

-   **Backend**: Python Flask with `concurrent.futures` for high-performance parallel polling.
-   **Frontend**: Vanilla JavaScript (ES6+) with requestAnimationFrame for smooth UI transitions.
-   **Storage**: SQLite for lightweight telemetry persistence.
-   **Security**: Thread-safe state management and sanitized credential handling for deployment.

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.10+
- Virtual Environment (`venv`)

### 2. Installation
```bash
git clone https://github.com/yourusername/poc-monitoring.git
cd poc-monitoring
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configuration
1.  Setup your POC locations in `clients.json`.
2.  Configure SMTP in `app.py` (using Google App Passwords).

### 4. Run Application
```bash
python app.py
```
Access the dashboard at `http://localhost:5050`.

## 🌐 Deployment

For production deployment on **Ubuntu 24.04**, we provide an automated script:

```bash
sudo bash setup_vps.sh
```
Refer to [Deployment Guide](./docs/deployment_guide.md) for detailed steps.

## 🛡️ Security Note

Always use **Environment Variables** or `.env` files for SMTP credentials in production. Never commit your `app.py` with real passwords to public repositories.

---

Built with ❤️ by **AssistX Enterprise AI**