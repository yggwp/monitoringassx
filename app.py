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
import copy
from flask import Flask, jsonify, render_template, request, Response, session, redirect, url_for
from functools import wraps
import queue

app = Flask(__name__)
app.secret_key = 'assistx-super-secret-key-2026'

# Simple Authentication Config
AUTH_USERNAME = "presales"
AUTH_PASSWORD = "presales"

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

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
# Separate lock for alert tracking and state management
STATE_LOCK = threading.Lock()
POLL_INTERVAL = 5  # Seconds

# Use a global session for connection pooling (Huge CPU & Latency optimization)
GLOBAL_SESSION = requests.Session()

# Email configuration
EMAIL_CONFIG = {
    "enabled": True,
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "sender_email": "presalesassistx@gmail.com",
    "sender_password": "uzsvxcqdncvqjpze",
    "receiver_email": "presales@assistxenterprise.ai"
}

# Shared tracking dictionaries (Must be protected by STATE_LOCK)
LAST_ALERTS: Dict[str, datetime] = {}
OFFLINE_START: Dict[str, datetime] = {}
REAL_DOWNTIME_START: Dict[str, datetime] = {}
LAST_STATE: Dict[str, Dict[str, Any]] = {}
FAILED_ATTEMPTS: Dict[str, int] = {}
SUCCESS_ATTEMPTS: Dict[str, int] = {}

# SSE Client Management
SSE_CLIENTS = []
SSE_LOCK = threading.Lock()

def broadcast_metrics(data):
    """Notify all connected SSE clients of new data."""
    # Create a stable snapshot for JSON serialization
    snapshot = [d.copy() for d in data] if data else []
    with SSE_LOCK:
        for q in SSE_CLIENTS:
            try:
                # Use non-blocking put to prevent memory leaks from slow/stale clients
                q.put_nowait(snapshot)
            except queue.Full:
                pass # If queue is full, drop the frame

def trigger_single_scrape(client_config):
    """Perform a dedicated scrape for one client and update shared state."""
    res = fetch_client_data(client_config)
    with METRICS_LOCK:
        global CLIENT_METRICS
        # Find and replace in global list
        for i, m in enumerate(CLIENT_METRICS):
            if m['id'] == res['id']:
                CLIENT_METRICS[i] = res
                break
        broadcast_metrics(CLIENT_METRICS)

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
            subject = f"RECOVERY: {full_name} Is ONLINE"
            color = "#28a745"
            icon = "🟢"
            status_title = "Status: Online"
            status_text = "ONLINE"
            downtime_html = f"<p><strong>Total Downtime:</strong> {duration_str}</p>"
        elif status == "Test":
            subject = f"TEST: SMTP Configuration Diagnostic"
            color = "#007bff"
            icon = "🛠️"
            status_title = "Diagnostic Email Test"
            status_text = "SYSTEM TEST"
            downtime_html = "<p style='color: #007bff;'><strong>Note:</strong> This is a simulation email to verify your SMTP settings. No real systems are offline.</p>"
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
        # Enable WAL mode for high concurrency and lower disk I/O lock overhead
        conn.execute('PRAGMA journal_mode=WAL;')
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

def sync_metrics_state():
    """
    Update global CLIENT_METRICS immediately from clients.json.
    Ensures UI updates instantly after CRUD operations.
    """
    clients = load_clients()
    with METRICS_LOCK:
        global CLIENT_METRICS
        # Map current metrics by ID for merging
        existing_metrics = {m['id']: m for m in CLIENT_METRICS}
        
        new_metrics_list = []
        for c in clients:
            client_id = c['id']
            if client_id in existing_metrics:
                # Update metadata but keep current telemetry
                m = existing_metrics[client_id].copy()
                endpoint = c.get("endpoint", "")
                m.update({
                    "name": c.get("name"),
                    "endpoint": endpoint,
                    "ip_address": urlparse(endpoint).hostname if endpoint else "N/A",
                    "location": c.get("location", "N/A"),
                    "anydesk_id": c.get("anydesk_id", "N/A"),
                    "simcard_number": c.get("simcard_number", "N/A"),
                    "quota_link": c.get("quota_link", "")
                })
                new_metrics_list.append(m)
            else:
                # New POC: Initialize with defaults
                endpoint = c.get("endpoint", "")
                new_metrics_list.append({
                    "id": client_id,
                    "name": c.get("name"),
                    "endpoint": endpoint,
                    "ip_address": urlparse(endpoint).hostname if endpoint else "N/A",
                    "location": c.get("location", "N/A"),
                    "anydesk_id": c.get("anydesk_id", "N/A"),
                    "simcard_number": c.get("simcard_number", "N/A"),
                    "quota_link": c.get("quota_link", ""),
                    "quota_text": "Pending...",
                    "status": "offline",
                    "anydesk_status": 0,
                    "cpu_usage": 0,
                    "memory_usage": 0,
                    "error": "Connecting..."
                })
        
        # Sort to maintain consistent order
        new_metrics_list.sort(key=lambda x: x["id"])
        CLIENT_METRICS = new_metrics_list
        # Trigger immediate broadcast of the base metadata
        broadcast_metrics(CLIENT_METRICS)

def log_telemetry(results: List[Dict[str, Any]]):
    try:
        with sqlite3.connect(DB_FILE) as conn:
            for r in results:
                client_id = r['id']
                current_status = r['status']
                current_anydesk = r['anydesk_status']
                
                # Check if state has changed under lock
                with STATE_LOCK:
                    last = LAST_STATE.get(client_id)
                    if last and last['status'] == current_status:
                        continue
                    # Update last state
                    LAST_STATE[client_id] = { 'status': current_status }
                
                conn.execute('''
                    INSERT INTO telemetry (client_id, node_name, status, anydesk_status, cpu_usage, memory_usage)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (client_id, r['name'], current_status, current_anydesk, r['cpu_usage'], r['memory_usage']))
                logger.info(f"LOGGED STATE CHANGE: {r['name']} is now {current_status.upper()}")
            conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Database logging error: {e}")
    except Exception as e:
        logger.error(f"Unexpected logging error: {e}")

LAST_ROTATION = 0.0

def rotate_data():
    """
    Clears data older than 7 days.
    """
    global LAST_ROTATION
    now_ts = time.time()
    
    # Throttle disk operations: Only run rotation once per hour to save CPU
    if now_ts - LAST_ROTATION < 3600:
        return
        
    LAST_ROTATION = now_ts

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
        # Using GLOBAL_SESSION reuses TCP connections (Keep-Alive), drastically cutting CPU overhead
        response = GLOBAL_SESSION.get(
            client["endpoint"], 
            timeout=7.0, 
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
        result["error"] = "Connection Timeout: Target node is offline."
    except requests.exceptions.ConnectionError:
        result["error"] = "Connection Failed: Target machine is offline or unreachable."
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
                q_resp = GLOBAL_SESSION.get(quota_link, timeout=7, headers=headers)
                
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

def check_server_online(host="8.8.8.8", port=53, timeout=3.0) -> bool:
    """Check if the VPS itself has internet connectivity."""
    import socket
    try:
        socket.setdefaulttimeout(timeout)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((host, port))
        s.close()
        return True
    except OSError:
        return False

def update_metrics_loop():
    """
    Background thread that periodically refreshes client metrics.
    """
    global CLIENT_METRICS
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
            
            # --- ROBUST DEBOUNCE LOGIC ---
            with METRICS_LOCK:
                prev_metrics = {m['id']: m for m in CLIENT_METRICS}
                
            server_is_online = None # Lazy evaluation
                
            for r in results:
                node_id = r['id']
                raw_offline = (r['status'] == 'offline' or r['anydesk_status'] == 0)
                
                prev = prev_metrics.get(node_id)
                
                # Determine current debounced state based on previous metrics
                current_debounced_state = 'online'
                if prev and (prev.get('status') == 'offline' or prev.get('anydesk_status') == 0):
                    current_debounced_state = 'offline'

                if raw_offline:
                    if FAILED_ATTEMPTS.get(node_id, 0) == 0:
                        REAL_DOWNTIME_START[node_id] = datetime.now()
                        
                    FAILED_ATTEMPTS[node_id] = FAILED_ATTEMPTS.get(node_id, 0) + 1
                    SUCCESS_ATTEMPTS[node_id] = 0
                    
                    is_solidly_offline = FAILED_ATTEMPTS[node_id] >= 12
                    
                    # Safety check: if a node reaches offline threshold, check server internet
                    if is_solidly_offline and current_debounced_state == 'online':
                        if server_is_online is None:
                            server_is_online = check_server_online()
                        if server_is_online is False:
                            logger.warning(f"Server network appears down! Suppressing offline alert for {r['name']}")
                            is_solidly_offline = False # don't transition
                    
                    if current_debounced_state == 'online':
                        if not is_solidly_offline:
                            # Not officially offline yet, pretend it's online
                            r['status'] = 'online'
                            r['anydesk_status'] = prev.get('anydesk_status', 1)
                            r['cpu_usage'] = prev.get('cpu_usage', 0)
                            r['memory_usage'] = prev.get('memory_usage', 0)
                            
                            if server_is_online is False:
                                r['error'] = 'Weak connection (Server network down, pausing alerts...)'
                            else:
                                r['error'] = f'Weak connection (Tolerating {FAILED_ATTEMPTS[node_id]}/12)...'
                    else:
                        # Already offline, clear any weak connection messages
                        if r.get('error') and 'Weak connection' in str(r.get('error')):
                            r['error'] = 'Connection Timeout: Target node is offline.'
                else:
                    SUCCESS_ATTEMPTS[node_id] = SUCCESS_ATTEMPTS.get(node_id, 0) + 1
                    FAILED_ATTEMPTS[node_id] = 0
                    
                    is_solidly_online = SUCCESS_ATTEMPTS[node_id] >= 6
                    
                    if current_debounced_state == 'offline':
                        if not is_solidly_online:
                            # Not officially online yet, pretend it's offline
                            r['status'] = 'offline'
                            r['anydesk_status'] = prev.get('anydesk_status', 0)
                            r['cpu_usage'] = prev.get('cpu_usage', 0)
                            r['memory_usage'] = prev.get('memory_usage', 0)
                            r['error'] = f'Recovering (Checking {SUCCESS_ATTEMPTS[node_id]}/6)...'
                    else:
                        # Already online, clear any recovering message
                        if r.get('error') and ('Checking' in str(r.get('error')) or 'Weak connection' in str(r.get('error'))):
                            r['error'] = None
            
            # Log to history database
            log_telemetry(results)
            
            # Check for offline locations and send alerts under lock
            for r in results:
                node_id = r['id']
                is_actually_offline = (r['status'] == 'offline' or r['anydesk_status'] == 0)
                
                with STATE_LOCK:
                    if is_actually_offline:
                        # POC is offline (unreachable or AnyDesk stopped)
                        now = datetime.now()
                        
                        # Track start of downtime if not already tracked
                        if node_id not in OFFLINE_START:
                            OFFLINE_START[node_id] = REAL_DOWNTIME_START.get(node_id, now)
                        
                        # Send alert only once when first offline (no periodic reminders)
                        if node_id not in LAST_ALERTS:
                            status_msg = "Offline" if r['status'] == 'offline' else "Stopped"
                            send_email_alert(r['name'], r['location'], status_msg)
                            LAST_ALERTS[node_id] = now
                    else:
                        # POC is online/running
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
            # Create a map for quick lookup
            results_map = {r['id']: r for r in results}
            
            # Update only the clients that are currently in the global list
            # and exist in our results. This avoids overwriting changes 
            # made by sync_metrics_state (CRUD ops) during our scrape cycle.
            for m in CLIENT_METRICS:
                client_id = m['id']
                if client_id in results_map:
                    res = results_map[client_id]
                    m.update({
                        "status": res["status"],
                        "anydesk_status": res["anydesk_status"],
                        "cpu_usage": res["cpu_usage"],
                        "memory_usage": res["memory_usage"],
                        "quota_text": res["quota_text"],
                        "error": res["error"]
                    })
            
            # Broadcast the updated metrics
            broadcast_metrics(CLIENT_METRICS)
        
        # Consistent server-side log for real-time verification (optional)
        # logger.debug("Metrics poller cycle complete.")
        
        time.sleep(POLL_INTERVAL)


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        if request.form.get('username') == AUTH_USERNAME and request.form.get('password') == AUTH_PASSWORD:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            error = 'Invalid credentials. Please try again.'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    return render_template('index.html')

@app.route('/api/stream')
@login_required
def stream():
    """SSE endpoint for real-time dashboard updates."""
    def event_stream():
        # Bounded queue prevents memory leaks if connection drops poorly
        q = queue.Queue(maxsize=10)
        with SSE_LOCK:
            SSE_CLIENTS.append(q)
            # Send initial state
            with METRICS_LOCK:
                q.put([d.copy() for d in CLIENT_METRICS])
        
        try:
            while True:
                data = q.get()
                yield f"data: {json.dumps(data)}\n\n"
        except GeneratorExit:
            with SSE_LOCK:
                SSE_CLIENTS.remove(q)

    return Response(event_stream(), mimetype="text/event-stream")

@app.route('/api/clients', methods=['GET', 'POST'])
@login_required
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
            # Instant sync for real-time UI response
            sync_metrics_state()
            
            # Start immediate background scrape for the new client
            threading.Thread(target=lambda: trigger_single_scrape(new_client), daemon=True).start()
            
            return jsonify({"status": "success", "client": new_client}), 201
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # Handle GET: Return cached metrics immediately
    with METRICS_LOCK:
        return jsonify(CLIENT_METRICS)

@app.route('/api/clients/<client_id>', methods=['PUT', 'DELETE'])
@login_required
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
            # Instant sync for real-time UI response
            sync_metrics_state()
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
            # Instant sync for real-time UI response
            sync_metrics_state()
            
            # Start immediate background scrape for the updated client
            threading.Thread(target=lambda: trigger_single_scrape(updated_client), daemon=True).start()
            
            return jsonify({"status": "updated", "client": updated_client}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500

@app.route('/api/clients/<client_id>/history', methods=['GET'])
@login_required
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
@login_required
def test_email():
    """Manual trigger to verify SMTP settings."""
    if not EMAIL_CONFIG["enabled"]:
        return jsonify({"error": "Email alerts are currently disabled in configuration."}), 400
    if not EMAIL_CONFIG["sender_email"] or not EMAIL_CONFIG["sender_password"]:
        return jsonify({"error": "Email credentials not configured."}), 400
        
    try:
        send_email_alert("Monitoring System", "Dashboard Server", "Test")
        return jsonify({"status": "success", "message": "Test email sent successfully."})
    except Exception as e:
        return jsonify({"error": f"SMTP Error: {str(e)}"}), 500

# Initialize background poller once at startup
def start_background_tasks():
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        logger.info("Initializing background monitoring thread...")
        thread = threading.Thread(target=update_metrics_loop, daemon=True)
        thread.start()

# Initialize data and background scraper unconditionally (Works for Flask & Gunicorn)
sync_metrics_state()
start_background_tasks()

if __name__ == '__main__':
    # Run heavily threaded for development
    app.run(host='0.0.0.0', port=5050, threaded=True, debug=False)
