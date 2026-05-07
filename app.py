import json
import os
import requests
import concurrent.futures
import re
import time
import threading
import sqlite3

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

# Warm-up period to avoid spam on startup
STARTUP_TIME = time.time()
WARMUP_DURATION = 120  # 2 minutes of silence after boot

# Use a global session for connection pooling (Huge CPU & Latency optimization)
GLOBAL_SESSION = requests.Session()



# Shared tracking dictionaries (Must be protected by STATE_LOCK)
LAST_ALERTS: Dict[str, datetime] = {}
OFFLINE_START: Dict[str, datetime] = {}
REAL_DOWNTIME_START: Dict[str, datetime] = {}
LAST_STATE: Dict[str, Dict[str, Any]] = {}
FAILED_ATTEMPTS: Dict[str, int] = {}
SUCCESS_ATTEMPTS: Dict[str, int] = {}
SERVER_WAS_OFFLINE: bool = False  # Tracks if the server itself experienced an outage

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

# Email alerts removed as per user request

# Database configuration
DB_FILE = os.path.join(os.path.dirname(__file__), 'history.db')

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        # Enable WAL mode for high concurrency and lower disk I/O lock overhead
        conn.execute('PRAGMA journal_mode=WAL;')
        conn.execute('PRAGMA synchronous=NORMAL;')
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
        conn.execute('''
            CREATE TABLE IF NOT EXISTS clients (
                id TEXT PRIMARY KEY,
                name TEXT,
                endpoint TEXT,
                location TEXT,
                anydesk_id TEXT,
                simcard_number TEXT,
                quota_link TEXT,
                offline_since DATETIME
            )
        ''')
        # Index for fast analytics queries (timestamp + client_id lookups)
        conn.execute('CREATE INDEX IF NOT EXISTS idx_telemetry_ts ON telemetry(timestamp, client_id);')
        conn.commit()

def migrate_json_to_db():
    if os.path.exists(CLIENTS_FILE):
        try:
            with open(CLIENTS_FILE, 'r') as f:
                clients = json.load(f)
            if clients:
                with sqlite3.connect(DB_FILE) as conn:
                    for c in clients:
                        conn.execute('''
                            INSERT OR IGNORE INTO clients (id, name, endpoint, location, anydesk_id, simcard_number, quota_link)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        ''', (c.get('id'), c.get('name'), c.get('endpoint'), c.get('location'), c.get('anydesk_id'), c.get('simcard_number'), c.get('quota_link')))
                    conn.commit()
            # Rename the file to .backup
            os.rename(CLIENTS_FILE, CLIENTS_FILE + ".backup")
            logger.info("Migrated clients.json to SQLite database successfully.")
        except Exception as e:
            logger.error(f"Failed to migrate clients.json: {e}")

init_db()
migrate_json_to_db()

def load_offline_state_from_db():
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT id, offline_since FROM clients WHERE offline_since IS NOT NULL").fetchall()
            with STATE_LOCK:
                for r in rows:
                    if r['offline_since']:
                        try:
                            # Parse string to datetime
                            OFFLINE_START[r['id']] = datetime.strptime(r['offline_since'], '%Y-%m-%d %H:%M:%S')
                        except ValueError:
                            pass
    except Exception as e:
        logger.error(f"Failed to load offline state: {e}")

load_offline_state_from_db()

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
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT id, name, endpoint, location, anydesk_id, simcard_number, quota_link FROM clients").fetchall()
            clients = []
            for r in rows:
                clients.append(dict(r))
            return clients
    except Exception as e:
        logger.error(f"Error loading clients from DB: {e}")
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
    except Exception as e:
        logger.error(f"Unexpected error scraping {client.get('name')}: {e}")
        result["error"] = "Unexpected Error: Invalid IP or format."
        
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
    global CLIENT_METRICS, SERVER_WAS_OFFLINE
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
                
            # Pre-check server connectivity once per cycle (lazy but shared)
            server_is_online = None # Lazy evaluation
            server_checked = False
                
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
            
            # --- WARM-UP GATE: Skip ALL alert processing during startup ---
            # During the first 2 minutes, the system is stabilizing.
            # sync_metrics_state() initializes all nodes as "offline", which would
            # cause the alert logic to think every node just went down and spam emails.
            # By skipping the ENTIRE alert block (not just email sending), we avoid
            # polluting OFFLINE_START / LAST_ALERTS with false data.
            uptime = time.time() - STARTUP_TIME
            if uptime < WARMUP_DURATION:
                if int(uptime) % 30 == 0:  # Log every ~30 seconds
                    logger.info(f"WARM-UP: {int(WARMUP_DURATION - uptime)}s remaining. All alerts paused.")
            else:
                # --- SERVER CONNECTIVITY GATE FOR EMAIL ALERTS ---
                # Check server connectivity before processing any alerts.
                # This prevents email spam when the server's own internet is down.
                if server_is_online is None:
                    server_is_online = check_server_online()
                
                if not server_is_online:
                    # Server has no internet — mark it and suppress ALL alerts.
                    # All "offline" readings are unreliable because the server can't reach anyone.
                    if not SERVER_WAS_OFFLINE:
                        logger.warning("SERVER INTERNET DOWN — All alerts suppressed until connectivity is restored.")
                    SERVER_WAS_OFFLINE = True
                    # Do NOT process any alerts this cycle
                else:
                    # Server is online. Check if we're recovering from a server outage.
                    if SERVER_WAS_OFFLINE:
                        # Server just came back online after being down.
                        # WIPE all tracking state so no false offline/recovery emails are sent.
                        # The system will start fresh as if it just booted up.
                        logger.info(
                            "SERVER INTERNET RESTORED — Resetting all alert tracking. "
                            "No false offline/recovery emails will be sent."
                        )
                        with STATE_LOCK:
                            LAST_ALERTS.clear()
                            OFFLINE_START.clear()
                            REAL_DOWNTIME_START.clear()
                        FAILED_ATTEMPTS.clear()
                        SUCCESS_ATTEMPTS.clear()
                        SERVER_WAS_OFFLINE = False
                        # Skip alert processing this cycle — let the next cycle
                        # establish a clean baseline of which nodes are truly online/offline.
                    else:
                        # Normal operation: server is online, was not offline before.
                        # Process alerts normally.
                        for r in results:
                            node_id = r['id']
                            is_actually_offline = (r['status'] == 'offline' or r['anydesk_status'] == 0)
                            
                            with STATE_LOCK:
                                if is_actually_offline:
                                    # POC is offline (unreachable or AnyDesk stopped)
                                    now = datetime.now()
                                    
                                    # Track start of downtime if not already tracked
                                    if node_id not in OFFLINE_START:
                                        offline_time = REAL_DOWNTIME_START.get(node_id, now)
                                        OFFLINE_START[node_id] = offline_time
                                        # Persist to DB
                                        try:
                                            with sqlite3.connect(DB_FILE) as conn:
                                                conn.execute('UPDATE clients SET offline_since = ? WHERE id = ?', (offline_time.strftime('%Y-%m-%d %H:%M:%S'), node_id))
                                                conn.commit()
                                        except Exception:
                                            pass
                                    
                                    # Log only once when first offline (no periodic reminders)
                                    if node_id not in LAST_ALERTS:
                                        LAST_ALERTS[node_id] = now
                                else:
                                    # POC is online/running
                                    if node_id in OFFLINE_START:
                                        # It was previously offline, now recovered!
                                        now = datetime.now()
                                        downtime = now - OFFLINE_START[node_id]
                                        duration_str = format_duration(downtime)
                                        
                                        # Clear tracking
                                        del OFFLINE_START[node_id]
                                        try:
                                            with sqlite3.connect(DB_FILE) as conn:
                                                conn.execute('UPDATE clients SET offline_since = NULL WHERE id = ?', (node_id,))
                                                conn.commit()
                                        except Exception:
                                            pass
                                            
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
def api_clients():
    if request.method == 'POST':
        if not session.get('logged_in'):
            return jsonify({"error": "Unauthorized"}), 401
            
        data = request.json
        if not data:
            return jsonify({"error": "Invalid payload"}), 400
            
        ip_val = data.get("ip", "").strip()
        if not re.match(r'^[0-9\.]+$', ip_val):
            return jsonify({"error": "Invalid IP Address format. Only numbers and dots are allowed."}), 400
            
        new_client = {
            "id": f"client-{os.urandom(4).hex()}",
            "name": data.get("name", "New POC"),
            "endpoint": f"http://{ip_val}:9800/metrics",
            "location": data.get("location", ""),
            "anydesk_id": data.get("anydesk_id", ""),
            "simcard_number": data.get("simcard_number", ""),
            "quota_link": data.get("quota_link", "")
        }
        
        try:
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute('''
                    INSERT INTO clients (id, name, endpoint, location, anydesk_id, simcard_number, quota_link)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (new_client['id'], new_client['name'], new_client['endpoint'], new_client['location'], new_client['anydesk_id'], new_client['simcard_number'], new_client['quota_link']))
                conn.commit()
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
        try:
            with sqlite3.connect(DB_FILE) as conn:
                cursor = conn.execute("DELETE FROM clients WHERE id = ?", (client_id,))
                conn.commit()
                if cursor.rowcount == 0:
                    return jsonify({"error": "Client not found"}), 404
            
            # Remove from tracking state
            with STATE_LOCK:
                if client_id in OFFLINE_START:
                    del OFFLINE_START[client_id]
                if client_id in LAST_ALERTS:
                    del LAST_ALERTS[client_id]
            
            # Instant sync for real-time UI response
            sync_metrics_state()
            return jsonify({"status": "deleted"}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 500
            
    if request.method == 'PUT':
        data = request.json
        if not data:
            return jsonify({"error": "Invalid payload"}), 400
            
        ip_val = data.get("ip", "").strip()
        if not re.match(r'^[0-9\.]+$', ip_val):
            return jsonify({"error": "Invalid IP Address format. Only numbers and dots are allowed."}), 400
            
        updated = False
        updated_client = None
        for itm in clients:
            client = cast(Dict[str, Any], itm)
            if client["id"] == client_id:
                client["name"] = data.get("name", client.get("name", "Unnamed"))
                # Make sure to update the endpoint URL using the provided clean IP
                client["endpoint"] = f"http://{ip_val}:9800/metrics"
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
            with sqlite3.connect(DB_FILE) as conn:
                conn.execute('''
                    UPDATE clients 
                    SET name = ?, endpoint = ?, location = ?, anydesk_id = ?, simcard_number = ?, quota_link = ?
                    WHERE id = ?
                ''', (updated_client["name"], updated_client["endpoint"], updated_client["location"], updated_client["anydesk_id"], updated_client["simcard_number"], updated_client["quota_link"], client_id))
                conn.commit()
            
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
    return jsonify({"error": "Email alerts are permanently disabled."}), 400

@app.route('/analytics')
@login_required
def analytics_page():
    return render_template('analytics.html')

@app.route('/api/analytics/summary', methods=['GET'])
@login_required
def analytics_summary():
    """Returns aggregated telemetry data for charts (daily/weekly/monthly)."""
    time_range = request.args.get('range', 'daily')
    
    # Determine time window and grouping
    now = datetime.now()
    if time_range == 'monthly':
        since = now - timedelta(days=30)
        # Group by day
        group_format = '%Y-%m-%d'
        label_format = '%d %b'
    elif time_range == 'weekly':
        since = now - timedelta(days=7)
        # Group by day
        group_format = '%Y-%m-%d'
        label_format = '%a %d'
    else:  # daily (default)
        since = now - timedelta(hours=24)
        # Group by hour
        group_format = '%Y-%m-%d %H'
        label_format = '%H:00'
    
    since_str = since.strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            
            # Get aggregated data grouped by time bucket
            cursor = conn.execute(f'''
                SELECT 
                    strftime('{group_format}', timestamp) as time_bucket,
                    COUNT(CASE WHEN status = 'online' THEN 1 END) as online_count,
                    COUNT(CASE WHEN status = 'offline' THEN 1 END) as offline_count,
                    ROUND(AVG(cpu_usage), 1) as avg_cpu,
                    ROUND(AVG(memory_usage), 1) as avg_memory,
                    COUNT(*) as total_entries
                FROM telemetry
                WHERE timestamp > ?
                GROUP BY time_bucket
                ORDER BY time_bucket ASC
            ''', (since_str,))
            
            rows = cursor.fetchall()
            
            labels = []
            online_counts = []
            offline_counts = []
            avg_cpus = []
            avg_mems = []
            
            for row in rows:
                try:
                    if time_range == 'daily':
                        dt = datetime.strptime(row['time_bucket'], '%Y-%m-%d %H')
                    else:
                        dt = datetime.strptime(row['time_bucket'], '%Y-%m-%d')
                    labels.append(dt.strftime(label_format))
                except ValueError:
                    labels.append(row['time_bucket'])
                
                online_counts.append(row['online_count'] or 0)
                offline_counts.append(row['offline_count'] or 0)
                avg_cpus.append(row['avg_cpu'] or 0)
                avg_mems.append(row['avg_memory'] or 0)
            
            return jsonify({
                'labels': labels,
                'online_counts': online_counts,
                'offline_counts': offline_counts,
                'avg_cpu': avg_cpus,
                'avg_memory': avg_mems,
                'range': time_range
            })
    except Exception as e:
        logger.error(f"Analytics query error: {e}")
        return jsonify({"error": str(e)}), 500

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
    app.run(host='0.0.0.0', port=5060, threaded=True, debug=False)
