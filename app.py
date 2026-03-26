import json
import os
import requests
import concurrent.futures
import re
import time
import threading
import sqlite3
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta
from typing import Any, Dict, List, cast
from urllib.parse import urlparse
import logging
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# Professional Logging Setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Path to the clients configuration
CLIENTS_FILE = os.path.join(os.path.dirname(__file__), 'clients.json')

# Cache for Quota Scraper (Link -> {value, timestamp})
QUOTA_CACHE = {}
QUOTA_CACHE_DURATION = 600  # 10 minutes cache for quota

# Global state for client metrics
CLIENT_METRICS: List[Dict[str, Any]] = []
METRICS_LOCK = threading.Lock()
POLL_INTERVAL = 5  # Seconds (user preference)

# Email configuration (Optional - Fill to enable alerts)
# Best to use App Passwords for Gmail
EMAIL_CONFIG = {
    "enabled": False,  # Set to True when ready to use
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "sender_email": "YOUR_EMAIL@gmail.com",
    "sender_password": "YOUR_APP_PASSWORD",
    "receiver_email": "RECIPIENT_EMAIL@example.com"
}

# Alert tracking to avoid spamming (Node ID -> Last Alert Time)
LAST_ALERTS: Dict[str, datetime] = {}
# Track when a POC first went offline to calculate total downtime
OFFLINE_START: Dict[str, datetime] = {}
# Track when a node recovery alert was last sent
RECOVERY_ALERTS: Dict[str, bool] = {}
ALERT_COOLDOWN = timedelta(hours=1) 

def format_duration(delta: timedelta) -> str:
    """Helper to format a timedelta into a human-readable string."""
    seconds = int(delta.total_seconds())
    periods = [
        ('day', 60*60*24),
        ('hour', 60*60),
        ('minute', 60)
    ]
    parts = []
    for period_name, period_seconds in periods:
        if seconds >= period_seconds:
            period_value, seconds = divmod(seconds, period_seconds)
            parts.append(f"{period_value} {period_name}{'s' if period_value > 1 else ''}")
    if seconds > 0 or not parts:
        parts.append(f"{seconds} second{'s' if seconds != 1 else ''}")
    return ", ".join(parts)

def send_email_alert(poc_name: str, location: str, status: str, duration_str: str = ""):
    if not EMAIL_CONFIG["enabled"] or not EMAIL_CONFIG["sender_email"]:
        return

    try:
        # Theme configuration
        full_name = f"{poc_name} at {location}" if location != "N/A" else poc_name
        
        if status == "Online":
            subject = f"RECOVERY: {full_name} Is BACK"
            color = "#28a745"
            icon = "🟢"
            status_title = "Status: Online"
            status_text = "ONLINE"
            downtime_html = f"<p><strong>Total Downtime:</strong> {duration_str}</p>"
        else:
            subject = f"CRITICAL: {full_name} Is {status.upper()}"
            color = "#dc3545"
            icon = "🔴"
            status_title = f"Status: {status.upper()}"
            status_text = status.upper()
            downtime_html = ""

        html_body = f"""
        <html>
        <head>
            <style>
                body {{ margin: 0; padding: 0; background-color: #f4f4f4; }}
            </style>
        </head>
        <body style="font-family: Arial, sans-serif; background-color: #f4f4f4;">
            <div style="height: 40px;"></div>
            <div style="max-width: 550px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; overflow: hidden; border: 1px solid #e0e0e0; box-shadow: 0 4px 10px rgba(0,0,0,0.08);">
                <div style="background-color: {color}; color: #ffffff; padding: 15px 20px; text-align: center;">
                    <h2 style="margin: 0; font-size: 20px;">{icon} {status_title}</h2>
                </div>
                <div style="padding: 25px; color: #333333; line-height: 1.5;">
                    <p style="font-size: 15px; margin-top: 0;">The status of POC <strong>{poc_name}</strong> at <strong>{location}</strong> has changed:</p>
                    <div style="background-color: #f9f9f9; padding: 15px 20px; border-radius: 6px; margin: 15px 0; border: 1px solid #f0f0f0;">
                        <p style="margin: 3px 0; font-size: 14px;"><strong>POC Name:</strong> {poc_name}</p>
                        <p style="margin: 3px 0; font-size: 14px;"><strong>Location:</strong> {location}</p>
                        <p style="margin: 3px 0; font-size: 14px;"><strong>Current Status:</strong> <span style="background-color: {color}; color: #ffffff; padding: 2px 8px; border-radius: 10px; font-weight: bold; font-size: 11px;">{status_text}</span></p>
                        {downtime_html}
                    </div>
                    <p style="font-size: 13px; color: #666666; margin-bottom: 0;">Please check your dashboard for real-time monitoring and history logs.</p>
                </div>
                <div style="background-color: #f8f9fa; color: #999999; padding: 15px; text-align: center; font-size: 11px; border-top: 1px solid #eeeeee;">
                    Sent by <strong>POC Monitoring Assistant</strong><br>
                    Real-time monitoring system
                </div>
            </div>
            <div style="height: 40px;"></div>
        </body>
        </html>
        """

        msg = MIMEText(html_body, 'html')
        msg['Subject'] = subject
        msg['From'] = f"POC Monitoring Assistant <{EMAIL_CONFIG['sender_email']}>"
        msg['To'] = EMAIL_CONFIG['receiver_email']

        with smtplib.SMTP(EMAIL_CONFIG["smtp_server"], EMAIL_CONFIG["smtp_port"]) as server:
            server.starttls()
            server.login(EMAIL_CONFIG["sender_email"], EMAIL_CONFIG["sender_password"])
            server.send_message(msg)
        print(f"Final refined alert email sent successfully to {EMAIL_CONFIG['receiver_email']}")
    except Exception as e:
        logger.error(f"Failed to send final refined alert email: {e}")
        raise e

# Database configuration
DB_FILE = os.path.join(os.path.dirname(__file__), 'history.db')

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS telemetry (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                client_id TEXT,
                node_name TEXT,
                status TEXT,
                anydesk_status INTEGER,
                cpu_usage REAL,
                memory_usage REAL
            )
        ''')
        conn.commit()

init_db()

# Last known state to avoid redundant logging (client_id -> {status, anydesk_status})
LAST_STATE: Dict[str, Dict[str, Any]] = {}

def log_telemetry(results: List[Dict[str, Any]]):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            for r in results:
                client_id = r['id']
                current_status = r['status']
                current_anydesk = r['anydesk_status']
                
                # Check if state has changed (Only recording on Status transitions as requested)
                last = LAST_STATE.get(client_id)
                if last and last['status'] == current_status:
                    continue
                
                # Update last state
                LAST_STATE[client_id] = {
                    'status': current_status
                }
                
                conn.execute('''
                    INSERT INTO telemetry (client_id, node_name, status, anydesk_status, cpu_usage, memory_usage)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (client_id, r['name'], current_status, current_anydesk, r['cpu_usage'], r['memory_usage']))
                logger.info(f"LOGGED STATE CHANGE: {r['name']} is now {current_status.upper()}")
            conn.commit()
    except Exception as e:
        print(f"Database logging error: {e}")

def rotate_data():
    """
    Clears data older than 7 days.
    Specifically checks for Monday midnight to perform a full 'rewrite' as requested.
    """
    try:
        now = datetime.now()
        # Prune data older than 7 days (rolling window)
        seven_days_ago = (now - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute('DELETE FROM telemetry WHERE timestamp < ?', (seven_days_ago,))
            conn.commit()
            
        # If it's Monday 00:xx, log that maintenance occurred
        if now.weekday() == 0 and now.hour == 0 and now.minute < 5:
            print(f"[{now}] Weekly maintenance: 7-day data cleanup complete.")
            
    except Exception as e:
        print(f"Data rotation error: {e}")

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '-1'
    return response

def load_clients() -> List[Dict[str, Any]]:
    try:
        with open(CLIENTS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading {CLIENTS_FILE}: {e}")
        return []

def parse_prometheus_metrics(text):
    """
    Parses simple prometheus metrics text into a dictionary.
    Assumes format:
    metric_name value
    """
    metrics = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        # Supports name{labels} value
        match = re.match(r'^([a-zA-Z_][a-zA-Z0-9_]*)(?:\{.*\})?\s+(-?[0-9.]+)', line)
        if match:
            name, value = match.groups()
            metrics[name] = float(value)
    return metrics

def fetch_client_data(client: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fetches data from a single client endpoint.
    """
    endpoint = client.get("endpoint", "")
    ip_address = urlparse(endpoint).hostname if endpoint else "N/A"
    quota_link = client.get("quota_link", "")
    
    result = {
        "id": client.get("id"),
        "name": client.get("name"),
        "endpoint": endpoint,
        "ip_address": ip_address,
        "location": client.get("location", "N/A"),
        "anydesk_id": client.get("anydesk_id", "N/A"),
        "simcard_number": client.get("simcard_number", "N/A"),
        "quota_link": quota_link,
        "quota_text": "Not Configured",
        "status": "offline", # connection status
        "anydesk_status": 0,
        "cpu_usage": 0,
        "memory_usage": 0,
        "error": None
    }
    
    try:
        # Internal cache suppression to ensure real-time metrics from the node
        response = requests.get(
            client["endpoint"], 
            timeout=1.5, 
            headers={'Cache-Control': 'no-cache', 'Pragma': 'no-cache'}
        )
        if response.status_code == 200:
            result["status"] = "online"
            metrics = parse_prometheus_metrics(response.text)
            result["anydesk_status"] = int(metrics.get("anydesk_status", 0))
            result["cpu_usage"] = metrics.get("cpu_usage", 0.0)
            result["memory_usage"] = metrics.get("memory_usage", 0.0)
            
            # Professional log instead of print
            logger.info(f"SCRAPE {client['name']}: CPU={result['cpu_usage']}%, Mem={result['memory_usage']}%")
            
    except requests.exceptions.Timeout:
        result["error"] = "Connection Timeout: Unable to reach the client node."
    except requests.exceptions.ConnectionError:
        result["error"] = "Connection Failed: Target machine is offline or node unreachable."
    except requests.exceptions.RequestException as e:
        result["error"] = "Network Error: Could not retrieve metrics."
        
    if quota_link:
        # Check cache first
        now = time.time()
        if quota_link in QUOTA_CACHE and (now - QUOTA_CACHE[quota_link]['timestamp'] < QUOTA_CACHE_DURATION):
            result["quota_text"] = QUOTA_CACHE[quota_link]['value']
        else:
            try:
                # Use a slightly longer timeout for external web scraping
                # Also use a generic User-Agent to avoid being blocked
                headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
                q_resp = requests.get(quota_link, timeout=7, headers=headers)
                
                if q_resp.status_code == 200:
                    # Primary regex for the specific Digipos structure
                    match = re.search(r'Kuota Nasional.*?<td class="other-value">\s*(.*?)\s*</td>', q_resp.text, re.DOTALL | re.IGNORECASE)
                    
                    if match:
                        val = match.group(1).strip()
                        if not val: val = "0 GB"
                        result["quota_text"] = val
                        QUOTA_CACHE[quota_link] = {'value': val, 'timestamp': now}
                    else:
                        # Fallback: maybe the structure changed slightly, look for any 'other-value' after 'Kuota Nasional'
                        fallback_match = re.search(r'Kuota Nasional.*?class="other-value".*?>(.*?)<', q_resp.text, re.DOTALL | re.IGNORECASE)
                        if fallback_match:
                            val = fallback_match.group(1).strip()
                            result["quota_text"] = val
                            QUOTA_CACHE[quota_link] = {'value': val, 'timestamp': now}
                        else:
                            result["quota_text"] = "Format Error"
                else:
                    result["quota_text"] = f"HTTP {q_resp.status_code}"
            except requests.exceptions.Timeout:
                result["quota_text"] = "Timeout"
            except Exception as e:
                print(f"Quota Scrape Error for {quota_link}: {e}")
                result["quota_text"] = "Error"
            
    return result

def update_metrics_loop():
    """
    Background thread that periodically refreshes client metrics.
    """
    logger.info(f"Background thread active (Interval: {POLL_INTERVAL}s)")
    while True:
        clients = load_clients()
        results = []
        
        if clients:
            # Use ThreadPoolExecutor for concurrent fetching
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(clients), 20)) as executor:
                results = list(executor.map(fetch_client_data, clients))
            
            # Sort results by ID to keep order consistent
            results.sort(key=lambda x: x["id"])
            
            # Log to history database
            log_telemetry(results)
            
            # Check for offline locations and send alerts
            for r in results:
                node_id = r['id']
                is_actually_offline = (r['status'] == 'offline' or r['anydesk_status'] == 0)
                
                if is_actually_offline:
                    # Node is offline (unreachable or AnyDesk stopped)
                    now = datetime.now()
                    
                    # Track start of downtime if not already tracked
                    if node_id not in OFFLINE_START:
                        OFFLINE_START[node_id] = now
                    
                    # Send alert with cooldown
                    if node_id not in LAST_ALERTS or (now - LAST_ALERTS[node_id] > ALERT_COOLDOWN):
                        status_msg = "Unreachable" if r['status'] == 'offline' else "Stopped"
                        send_email_alert(r['name'], r['location'], status_msg)
                        LAST_ALERTS[node_id] = now
                else:
                    # Node is online/running
                    if node_id in OFFLINE_START:
                        # It was previously offline, now recovered!
                        now = datetime.now()
                        downtime = now - OFFLINE_START[node_id]
                        duration_str = format_duration(downtime)
                        
                        send_email_alert(r['name'], r['location'], "Online", duration_str)
                        
                        # Clear tracking
                        del OFFLINE_START[node_id]
                        if node_id in LAST_ALERTS:
                            del LAST_ALERTS[node_id] # Reset alert cooldown on recovery
            
            # Periodically check for data rotation
            rotate_data()
        
        with METRICS_LOCK:
            global CLIENT_METRICS
            CLIENT_METRICS = results
        
        # Consistent server-side log for real-time verification (optional)
        # logger.debug("Metrics poller cycle complete.")
        
        time.sleep(POLL_INTERVAL)


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/clients', methods=['GET', 'POST'])
def api_clients():
    if request.method == 'POST':
        data = request.json
        if not data:
            return jsonify({"error": "Invalid payload"}), 400
            
        new_client = {
            "id": f"client-{os.urandom(4).hex()}",
            "name": data.get("name", "New POC"),
            "endpoint": f"http://{data.get('ip')}:9800/metrics",
            "location": data.get("location", ""),
            "anydesk_id": data.get("anydesk_id", ""),
            "simcard_number": data.get("simcard_number", ""),
            "quota_link": data.get("quota_link", "")
        }
        
        clients = load_clients()
        clients.append(new_client)
        
        try:
            with open(CLIENTS_FILE, 'w') as f:
                json.dump(clients, f, indent=4)
            return jsonify({"status": "success", "client": new_client}), 201
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # Handle GET: Return cached metrics immediately
    with METRICS_LOCK:
        return jsonify(CLIENT_METRICS)

@app.route('/api/clients/<client_id>', methods=['PUT', 'DELETE'])
def manage_client(client_id):
    clients = load_clients()
    
    if request.method == 'DELETE':
        initial_count = len(clients)
        clients = [c for c in clients if c["id"] != client_id]
        if len(clients) == initial_count:
            return jsonify({"error": "Client not found"}), 404
            
        try:
            with open(CLIENTS_FILE, 'w') as f:
                json.dump(clients, f, indent=4)
            return jsonify({"status": "deleted"}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500
            
    if request.method == 'PUT':
        data = request.json
        if not data:
            return jsonify({"error": "Invalid payload"}), 400
            
        updated = False
        updated_client = None
        for itm in clients:
            client = cast(Dict[str, Any], itm)
            if client["id"] == client_id:
                client["name"] = data.get("name", client.get("name", "Unnamed"))
                # Make sure to update the endpoint URL using the provided clean IP
                client["endpoint"] = f"http://{data.get('ip')}:9800/metrics"
                client["location"] = data.get("location", client.get("location", ""))
                client["anydesk_id"] = data.get("anydesk_id", client.get("anydesk_id", ""))
                client["simcard_number"] = data.get("simcard_number", client.get("simcard_number", ""))
                client["quota_link"] = data.get("quota_link", client.get("quota_link", ""))
                updated = True
                updated_client = client
                break
                
        if not updated:
            return jsonify({"error": "Client not found"}), 404
            
        try:
            with open(CLIENTS_FILE, 'w') as f:
                json.dump(clients, f, indent=4)
            return jsonify({"status": "updated", "client": updated_client}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500

@app.route('/api/clients/<client_id>/history', methods=['GET'])
def get_client_history(client_id):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute('''
                SELECT timestamp, status, anydesk_status, cpu_usage, memory_usage 
                FROM telemetry 
                WHERE client_id = ? 
                ORDER BY timestamp DESC 
                LIMIT 100
            ''', (client_id,))
            history = [dict(row) for row in cursor.fetchall()]
            return jsonify(history)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/test-email', methods=['POST'])
def test_email():
    """Manual trigger to verify SMTP settings."""
    if not EMAIL_CONFIG["enabled"]:
        return jsonify({"error": "Email alerts are currently disabled in configuration."}), 400
    if not EMAIL_CONFIG["sender_email"] or not EMAIL_CONFIG["sender_password"]:
        return jsonify({"error": "Email credentials not configured."}), 400
        
    try:
        send_email_alert("BSN", "Bekasi", "UNREACHABLE", "0 minutes (Diagnostic)")
        return jsonify({"status": "success", "message": "Test email sent successfully."})
    except Exception as e:
        return jsonify({"error": f"SMTP Error: {str(e)}"}), 500

if __name__ == '__main__':
    # Start the background polling thread only once in the main process
    # Use reloader guard to ensure it runs in the same process as the API
    if os.environ.get("WERKZEUG_RUN_MAIN"):
        polling_thread = threading.Thread(target=update_metrics_loop, daemon=True)
        polling_thread.start()
        
    # Run heavily threaded for development
    app.run(host='0.0.0.0', port=5050, threaded=True, debug=True)
