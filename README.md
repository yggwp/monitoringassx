# AssistX POC Monitoring Dashboard

![Status](https://img.shields.io/badge/Status-Active-success?style=for-the-badge)
![Tech](https://img.shields.io/badge/Stack-Flask_%7C_SQLite_%7C_JS-blue?style=for-the-badge)

AssistX is a lightweight, real-time monitoring dashboard designed to track AnyDesk status and system telemetry (CPU/Memory) across multiple POC (Proof of Concept) locations. It features a modern glassmorphism UI, automated internet quota scraping, and persistent historical logging.

## ✨ Key Features

- **Real-time Monitoring**: Parallel polling of node metrics every 10 seconds.
- **AnyDesk Tracking**: Instant visibility into whether AnyDesk services are running or stopped.
- **Internet Quota Scraper**: Automated Telkomsel Digipos quota tracking with a 10-minute intelligent cache.
- **Telemetry History**: 7-day rolling historical logs with specialized visualizations for CPU and Memory load.
- **Smart Alerting**: SMTP email alerts for node failures (Critical) and recoveries (with downtime calculation), featuring a 1-hour anti-spam cooldown.
- **Modern UI/UX**: Premium glassmorphism aesthetic using the 'Outfit' font and responsive design.

## 🛠️ Tech Stack

- **Backend**: Python 3.9+, Flask, Concurrent Futures (Threading).
- **Frontend**: Vanilla JavaScript (ES6+), CSS3 (Glassmorphism), HTML5.
- **Data**: SQLite (Telemetry logs), JSON (Client configuration).

## 🚀 Quick Start

### 1. Requirements
Ensure you have Python 3.x installed.

### 2. Installation
Clone the repository and install the dependencies:
```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

### 3. Running the Dashboard
Start the Flask server:
```bash
python3 app.py
```
Access the dashboard at: **[http://localhost:5050](http://localhost:5050)**

---

## ⚙️ Configuration

### Adding POC Nodes
You can add new nodes directly from the UI by clicking **"+ Add New POC"**. You will need:
- **IP Address**: The target machine IP (assumes port 9800 is open for metrics).
- **AnyDesk ID**: For quick reference and remote access.
- **Quota Link**: (Optional) The Telkomsel Digipos simcard checking URL.

### SMTP Alerts (Optional)
To enable email alerts, edit the `EMAIL_CONFIG` dictionary in `app.py`:
```python
EMAIL_CONFIG = {
    "enabled": True,
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "sender_email": "your-email@gmail.com",
    "sender_password": "your-app-password",
    "receiver_email": "target-alert@email.com"
}
```

---

## 📂 Project Structure

- `app.py`: Core backend logic, API endpoints, and background polling thread.
- `clients.json`: Stores node configuration and location metadata.
- `history.db`: SQLite database for 7-day telemetry logs.
- `templates/index.html`: Main dashboard UI structure.
- `static/js/main.js`: Frontend logic, real-time state management, and modal handling.
- `static/css/styles.css`: Glassmorphism design system.

## 🧹 Maintenance
The system automatically performs **Weekly Data Rotation** (every Monday at 00:00) to prune telemetry logs older than 7 days, keeping the `history.db` file small and performant.

---

*Built with ♥ for the AssistX Team.*