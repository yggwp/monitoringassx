// Analytics Dashboard - Chart & Data Logic
// Uses Chart.js for rendering, fetches from /api/analytics/summary and /api/clients

let statusChart = null;
let resourceChart = null;
let currentRange = 'daily';

// Chart.js global defaults for dark theme
Chart.defaults.color = '#8e95ab';
Chart.defaults.borderColor = 'rgba(255, 255, 255, 0.06)';
Chart.defaults.font.family = "'Outfit', sans-serif";

document.addEventListener('DOMContentLoaded', () => {
    loadSummaryCards();
    loadChartData('daily');
    setupTabs();

    // Auto-refresh summary cards every 10 seconds
    setInterval(loadSummaryCards, 10000);
});

function setupTabs() {
    document.querySelectorAll('.chart-tab').forEach(tab => {
        tab.addEventListener('click', () => {
            document.querySelectorAll('.chart-tab').forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            currentRange = tab.dataset.range;
            loadChartData(currentRange);
        });
    });
}

// ========================================
// SUMMARY CARDS (Live from /api/clients)
// ========================================
async function loadSummaryCards() {
    try {
        const response = await fetch(`/api/clients?t=${Date.now()}`);
        if (!response.ok) return;
        const clients = await response.json();

        const total = clients.length;
        const online = clients.filter(c => c.status === 'online' && c.anydesk_status === 1).length;
        const offline = clients.filter(c => c.status === 'offline' || c.anydesk_status === 0).length;
        const critical = clients.filter(c =>
            c.status === 'online' && (c.cpu_usage >= 95 || c.memory_usage >= 95)
        ).length;
        const uptimePercent = total > 0 ? ((online / total) * 100) : 0;

        setText('stat-total', total);
        setText('stat-online', online);
        setText('stat-offline', offline);
        setText('stat-critical', critical);
        setText('uptime-label', uptimePercent.toFixed(1) + '%');

        const fill = document.getElementById('uptime-fill');
        if (fill) {
            fill.style.width = uptimePercent + '%';
            fill.classList.remove('low', 'medium');
            if (uptimePercent < 50) fill.classList.add('low');
            else if (uptimePercent < 80) fill.classList.add('medium');
        }

        // Update node table
        renderNodeTable(clients);
    } catch (e) {
        console.error('Failed to load summary:', e);
    }
}

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value;
}

function renderNodeTable(clients) {
    const tbody = document.getElementById('nodes-table-body');
    if (!tbody) return;

    const sorted = [...clients].sort((a, b) => {
        const aOff = (a.status === 'offline' || a.anydesk_status === 0) ? 0 : 1;
        const bOff = (b.status === 'offline' || b.anydesk_status === 0) ? 0 : 1;
        if (aOff !== bOff) return aOff - bOff;
        return Math.max(b.cpu_usage || 0, b.memory_usage || 0) - Math.max(a.cpu_usage || 0, a.memory_usage || 0);
    });

    tbody.innerHTML = sorted.map(c => {
        const isOffline = c.status === 'offline' || c.anydesk_status === 0;
        const badge = isOffline
            ? '<span class="a-badge a-badge-offline">Offline</span>'
            : '<span class="a-badge a-badge-online">Online</span>';

        const cpu = parseFloat(c.cpu_usage) || 0;
        const mem = parseFloat(c.memory_usage) || 0;

        let alertTag = '<span class="a-alert-tag a-alert-ok">OK</span>';
        if (isOffline) {
            alertTag = '<span class="a-alert-tag a-alert-critical">Down</span>';
        } else if (cpu >= 95 && mem >= 95) {
            alertTag = '<span class="a-alert-tag a-alert-critical">CPU + MEM</span>';
        } else if (cpu >= 95) {
            alertTag = '<span class="a-alert-tag a-alert-critical">High CPU</span>';
        } else if (mem >= 95) {
            alertTag = '<span class="a-alert-tag a-alert-critical">High MEM</span>';
        }

        return `<tr>
            <td><strong>${c.name || 'Unknown'}</strong></td>
            <td align="center" style="color: var(--text-secondary)">${c.location || '—'}</td>
            <td align="center">${badge}</td>
            <td align="center" class="${cpu >= 95 ? 'value-critical' : 'value-normal'}">${isOffline ? '—' : cpu.toFixed(1) + '%'}</td>
            <td align="center" class="${mem >= 95 ? 'value-critical' : 'value-normal'}">${isOffline ? '—' : mem.toFixed(1) + '%'}</td>
            <td align="center">${alertTag}</td>
        </tr>`;
    }).join('');
}

// ========================================
// CHARTS (Historical from /api/analytics/summary)
// ========================================
async function loadChartData(range) {
    try {
        const response = await fetch(`/api/analytics/summary?range=${range}&t=${Date.now()}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();

        renderStatusChart(data);
        renderResourceChart(data);
    } catch (e) {
        console.error('Failed to load chart data:', e);
    }
}

function renderStatusChart(data) {
    const ctx = document.getElementById('statusChart');
    if (!ctx) return;

    if (statusChart) statusChart.destroy();

    statusChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: data.labels,
            datasets: [
                {
                    label: 'Online Events',
                    data: data.online_counts,
                    backgroundColor: 'rgba(16, 185, 129, 0.6)',
                    borderColor: '#10b981',
                    borderWidth: 1,
                    borderRadius: 4,
                    barPercentage: 0.7,
                    categoryPercentage: 0.8,
                },
                {
                    label: 'Offline Events',
                    data: data.offline_counts,
                    backgroundColor: 'rgba(239, 68, 68, 0.6)',
                    borderColor: '#ef4444',
                    borderWidth: 1,
                    borderRadius: 4,
                    barPercentage: 0.7,
                    categoryPercentage: 0.8,
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: {
                    position: 'top',
                    labels: { boxWidth: 12, padding: 16, usePointStyle: true, pointStyle: 'rectRounded' }
                },
                tooltip: {
                    backgroundColor: 'rgba(20, 22, 37, 0.95)',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                    titleFont: { weight: '600' },
                    padding: 12,
                    cornerRadius: 8,
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { maxRotation: 45, font: { size: 11 } }
                },
                y: {
                    beginAtZero: true,
                    grid: { color: 'rgba(255,255,255,0.04)' },
                    ticks: { precision: 0, font: { size: 11 } }
                }
            }
        }
    });
}

function renderResourceChart(data) {
    const ctx = document.getElementById('resourceChart');
    if (!ctx) return;

    if (resourceChart) resourceChart.destroy();

    resourceChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: data.labels,
            datasets: [
                {
                    label: 'Avg CPU %',
                    data: data.avg_cpu,
                    borderColor: '#818cf8',
                    backgroundColor: 'rgba(129, 140, 248, 0.08)',
                    fill: true,
                    tension: 0.4,
                    pointRadius: 3,
                    pointHoverRadius: 6,
                    pointBackgroundColor: '#818cf8',
                    borderWidth: 2,
                },
                {
                    label: 'Avg Memory %',
                    data: data.avg_memory,
                    borderColor: '#06b6d4',
                    backgroundColor: 'rgba(6, 182, 212, 0.08)',
                    fill: true,
                    tension: 0.4,
                    pointRadius: 3,
                    pointHoverRadius: 6,
                    pointBackgroundColor: '#06b6d4',
                    borderWidth: 2,
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { intersect: false, mode: 'index' },
            plugins: {
                legend: {
                    position: 'top',
                    labels: { boxWidth: 12, padding: 16, usePointStyle: true, pointStyle: 'circle' }
                },
                tooltip: {
                    backgroundColor: 'rgba(20, 22, 37, 0.95)',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                    titleFont: { weight: '600' },
                    padding: 12,
                    cornerRadius: 8,
                    callbacks: {
                        label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y}%`
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { maxRotation: 45, font: { size: 11 } }
                },
                y: {
                    beginAtZero: true,
                    max: 100,
                    grid: { color: 'rgba(255,255,255,0.04)' },
                    ticks: {
                        callback: v => v + '%',
                        font: { size: 11 }
                    }
                }
            }
        }
    });
}
