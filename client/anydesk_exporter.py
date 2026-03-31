from flask import Flask, Response
import psutil
import threading
import time

app = Flask(__name__)

# Cache metrics global agar Endpoint /metrics tidak lemot
metrics_cache = {
    "anydesk_status": 0,
    "cpu_usage": 0.0,
    "memory_usage": 0.0
}

def check_anydesk_status():
    """
    Cross-platform check using psutil.
    Works for both Linux ('anydesk') and Windows ('AnyDesk.exe').
    """
    for proc in psutil.process_iter(['name']):
        try:
            name = proc.info['name']
            if name and 'anydesk' in name.lower():
                return 1
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return 0

def update_metrics_loop():
    """
    Background thread yang mengecek status secara real-time setiap 5 detik.
    """
    while True:
        try:
            metrics_cache["anydesk_status"] = check_anydesk_status()
            # interval=1 digunakan di sini karena ini berjalan di background thread (aman)
            # Waktu proses total = interval 1 detik + sleep 4 detik = 5 detik.
            metrics_cache["cpu_usage"] = psutil.cpu_percent(interval=1)
            metrics_cache["memory_usage"] = psutil.virtual_memory().percent
        except Exception as e:
            print(f"Error ngecek metrik: {e}")
        
        # Jeda 4 detik (ditambah interval 1 detik di atas = 5 detik)
        time.sleep(4)

@app.route('/metrics')
def metrics():
    # Langsung mengambil dari cache! Tidak membebani CPU Client saat web dashboard me-refresh.
    data = f"""# HELP anydesk_status Status AnyDesk (1=running, 0=stopped)
# TYPE anydesk_status gauge
anydesk_status {metrics_cache['anydesk_status']}

# HELP cpu_usage CPU Usage %
# TYPE cpu_usage gauge
cpu_usage {metrics_cache['cpu_usage']}

# HELP memory_usage Memory Usage %
# TYPE memory_usage gauge
memory_usage {metrics_cache['memory_usage']}
"""
    return Response(data, mimetype='text/plain')

if __name__ == '__main__':
    # Jalankan background checker (Thread Terpisah)
    threading.Thread(target=update_metrics_loop, daemon=True).start()
    
    # Listen on all interfaces, port 9800
    app.run(host='0.0.0.0', port=9800)
