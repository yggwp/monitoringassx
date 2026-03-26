console.log("%c DASHBOARD V2.0.2 - STABLE SYNC ACTIVE ", "background: #222; color: #bada55; font-size: 20px;");
const activeCards = new Map();
let isFetching = false;
let currentHistoryClientId = null;
let historyData = [];
let currentPage = 1;
const itemsPerPage = 10;

// Initialize the dashboard
document.addEventListener('DOMContentLoaded', () => {
    fetchClients();
    // Poll for updates every 5 seconds
    setInterval(fetchClients, 5000);
    
    // Global Event Delegation for Card Actions
    setupEventDelegation();
});

function setupEventDelegation() {
    const grid = document.getElementById('clients-grid');
    if (!grid) return;

    grid.addEventListener('click', (e) => {
        const btn = e.target.closest('.btn-icon');
        if (!btn) return;
        
        const card = btn.closest('.client-card');
        if (!card) return;

        const clientId = card.id.replace('client-', '');
        console.log(`[Interaction] Button clicked: ${btn.className} for client: ${clientId}`);
        
        if (btn.classList.contains('btn-edit')) {
            openEditModalById(clientId);
        } else if (btn.classList.contains('btn-history')) {
            openHistoryModalById(clientId);
        } else if (btn.classList.contains('btn-delete')) {
            deleteClient(clientId);
        }
    });
}

function updateSyncTime() {
    const now = new Date();
    // Quantize seconds to the nearest 5-second block (e.g. 05, 10, 15) as requested
    const seconds = Math.floor(now.getSeconds() / 5) * 5;
    now.setSeconds(seconds);
    const timeStr = now.toTimeString().split(' ')[0];
    
    const syncElem = document.getElementById('sync-status');
    if (syncElem) {
        const textElem = syncElem.querySelector('.sync-text');
        if (textElem) textElem.textContent = `Updated: ${timeStr}`;
    }
}

async function fetchClients() {
    if (isFetching) return;
    isFetching = true;

    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 4500);

        const response = await fetch(`/api/clients?t=${Date.now()}`, { signal: controller.signal });
        clearTimeout(timeoutId);

        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        
        const clients = await response.json();
        renderClientsGrid(clients);
        updateSyncTime();
    } catch (error) {
        if (error.name !== 'AbortError') {
            console.error('Fetch error:', error);
        }
    } finally {
        isFetching = false;
    }
}

function renderClientsGrid(clients) {
    const grid = document.getElementById('clients-grid');
    const template = document.getElementById('client-card-template');
    if (!grid || !template) return;

    // Remove cards no longer in the list
    const currentIds = new Set(clients.map(c => `client-${c.id}`));
    Array.from(grid.querySelectorAll('.client-card')).forEach(card => {
        if (!currentIds.has(card.id)) card.remove();
    });

    requestAnimationFrame(() => {
        clients.forEach(client => {
            const cardId = `client-${client.id}`;
            let card = document.getElementById(cardId);
            
            if (!card) {
                const clone = template.content.cloneNode(true);
                const cardEl = clone.querySelector('.client-card');
                cardEl.id = cardId;
                grid.appendChild(clone);
                card = document.getElementById(cardId);
            }

            if (card) {
                updateCardData(card, client);
            }
        });

        const loading = grid.querySelector('.loading-state');
        if (loading && clients.length > 0) loading.remove();
    });
}

function updateCardData(card, client) {
    try {
        const safeText = (selector, text) => {
            const el = card.querySelector(selector);
            if (el) el.textContent = text;
        };

        safeText('.client-name', client.name);
        safeText('.client-ip', client.ip_address);
        safeText('.client-location', client.location);
        safeText('.client-anydesk-id', client.anydesk_id);
        safeText('.client-simcard', client.simcard_number);

        const statusBadge = card.querySelector('.status-badge');
        const anydeskVal = card.querySelector('.anydesk-value');
        const metricsCont = card.querySelector('.metrics-container');
        const errorCont = card.querySelector('.card-error');

        if (client.status === 'online') {
            if (statusBadge) {
                statusBadge.className = 'status-badge status-online';
                const text = statusBadge.querySelector('.text');
                if (text) text.textContent = 'Online';
            }
            if (metricsCont) metricsCont.style.display = 'flex';
            if (errorCont) errorCont.style.display = 'none';

            if (anydeskVal) {
                anydeskVal.textContent = client.anydesk_status === 1 ? 'Running' : 'Stopped';
                anydeskVal.className = `anydesk-value ${client.anydesk_status === 1 ? 'value-running' : 'value-stopped'}`;
            }

            updateProgressBar(card.querySelector('.cpu-fill'), card.querySelector('.cpu-text'), client.cpu_usage);
            updateProgressBar(card.querySelector('.mem-fill'), card.querySelector('.mem-text'), client.memory_usage);
            
            const quotaCont = card.querySelector('.quota-status');
            if (client.quota_link && quotaCont) {
                quotaCont.style.display = 'block';
                const quotaValElem = quotaCont.querySelector('.quota-value');
                if (quotaValElem) {
                    quotaValElem.textContent = client.quota_text;
                    const match = client.quota_text.match(/([0-9.]+)\s*GB/i);
                    if (match) {
                        const gb = parseFloat(match[1]);
                        quotaValElem.classList.remove('quota-critical', 'quota-warning', 'quota-healthy');
                        if (gb < 2) quotaValElem.classList.add('quota-critical');
                        else if (gb < 5) quotaValElem.classList.add('quota-warning');
                        else quotaValElem.classList.add('quota-healthy');
                    }
                }
            } else if (quotaCont) {
                quotaCont.style.display = 'none';
            }
        } else {
            if (statusBadge) {
                statusBadge.className = 'status-badge status-offline';
                const text = statusBadge.querySelector('.text');
                if (text) text.textContent = 'Offline';
            }
            if (metricsCont) metricsCont.style.display = 'none';
            if (errorCont) {
                errorCont.style.display = 'flex';
                const errText = errorCont.querySelector('.error-text');
                if (errText) errText.textContent = client.error || 'Connection Failed';
            }
        }
    } catch (e) {
        console.error("Error updating card data:", e);
    }
}

function updateProgressBar(fill, text, value) {
    if (!fill || !text) return;
    const val = parseFloat(value) || 0;
    fill.style.width = `${val}%`;
    text.textContent = `${val.toFixed(1)}%`;
    fill.classList.remove('fill-low', 'fill-med', 'fill-high');
    if (val < 50) fill.classList.add('fill-low');
    else if (val < 80) fill.classList.add('fill-med');
    else fill.classList.add('fill-high');
}

// Modal Logic
function toggleModal(show, clientId = null) {
    console.log(`[Modal] toggleModal called: show=${show}, id=${clientId}`);
    const modal = document.getElementById('client-modal');
    const form = document.getElementById('client-form');
    const title = document.getElementById('modal-title');
    if (!modal || !form) return;

    if (show) {
        modal.classList.add('show');
        modal.style.display = 'flex'; // Ensure flex
        if (clientId) {
            if (title) title.textContent = 'Edit POC Location';
            fetch(`/api/clients?t=${Date.now()}`)
                .then(r => r.json())
                .then(clients => {
                    const client = clients.find(c => String(c.id) === String(clientId));
                    if (client) {
                        form.client_id.value = client.id;
                        form.name.value = client.name;
                        // Extract IP from endpoint
                        try {
                            const url = new URL(client.endpoint);
                            form.ip.value = url.hostname;
                        } catch(e) {
                            form.ip.value = client.endpoint || '';
                        }
                        form.location.value = client.location;
                        form.anydesk_id.value = client.anydesk_id;
                        form.simcard_number.value = client.simcard_number;
                        form.quota_link.value = client.quota_link;
                    }
                });
        } else {
            if (title) title.textContent = 'Add New POC Location';
            form.reset();
            form.client_id.value = '';
        }
    } else {
        modal.classList.remove('show');
        setTimeout(() => { if (!modal.classList.contains('show')) modal.style.display = 'none'; }, 300);
    }
}

async function openEditModalById(clientId) {
    toggleModal(true, clientId);
}

// History Modal
async function openHistoryModalById(clientId) {
    const modal = document.getElementById('history-modal');
    const nameElem = document.getElementById('history-client-name');
    if (!modal) return;

    const card = document.getElementById(`client-${clientId}`);
    const name = card ? card.querySelector('.client-name').textContent : clientId;
    
    currentHistoryClientId = clientId;
    currentPage = 1;
    if (nameElem) nameElem.textContent = name;
    
    modal.classList.add('show');
    modal.style.display = 'flex';
    fetchHistory();
}

function toggleHistoryModal(show) {
    const modal = document.getElementById('history-modal');
    if (!modal) return;
    if (show) {
        modal.classList.add('show');
        modal.style.display = 'flex';
    } else {
        modal.classList.remove('show');
        setTimeout(() => { if (!modal.classList.contains('show')) modal.style.display = 'none'; }, 300);
    }
}

async function fetchHistory() {
    const list = document.getElementById('history-table-body');
    const loading = document.getElementById('history-loading');
    const empty = document.getElementById('history-empty');
    
    if (list) list.innerHTML = '';
    if (loading) loading.style.display = 'block';
    if (empty) empty.style.display = 'none';

    try {
        const response = await fetch(`/api/clients/${currentHistoryClientId}/history`);
        historyData = await response.json();
        renderHistory();
    } catch (error) {
        list.innerHTML = '<tr><td colspan="4" style="text-align:center; color: #ef4444">Failed to load history</td></tr>';
    }
}

function renderHistory() {
    const list = document.getElementById('history-table-body');
    const loading = document.getElementById('history-loading');
    const empty = document.getElementById('history-empty');
    if (!list) return;

    if (loading) loading.style.display = 'none';
    
    if (historyData.length === 0) {
        if (empty) empty.style.display = 'block';
        list.innerHTML = '';
        return;
    }

    if (empty) empty.style.display = 'none';
    const start = (currentPage - 1) * itemsPerPage;
    const end = start + itemsPerPage;
    const paginatedItems = historyData.slice(start, end);
    const totalPages = Math.ceil(historyData.length / itemsPerPage) || 1;

    list.innerHTML = paginatedItems.map(item => `
        <tr>
            <td>${item.timestamp}</td>
            <td><span class="status-badge status-${item.status}">${item.status.toUpperCase()}</span></td>
            <td align="center">${item.cpu_usage}%</td>
            <td align="center">${item.memory_usage}%</td>
        </tr>
    `).join('');

    const currPageElem = document.getElementById('current-page');
    const totalPageElem = document.getElementById('total-pages');
    if (currPageElem) currPageElem.textContent = currentPage;
    if (totalPageElem) totalPageElem.textContent = totalPages;
    
    const prevBtn = document.getElementById('prev-page');
    const nextBtn = document.getElementById('next-page');
    if (prevBtn) prevBtn.disabled = currentPage === 1;
    if (nextBtn) nextBtn.disabled = currentPage === totalPages;
}

// Button Events
document.addEventListener('click', (e) => {
    if (e.target.id === 'prev-page') {
        if (currentPage > 1) { currentPage--; renderHistory(); }
    } else if (e.target.id === 'next-page') {
        const totalPages = Math.ceil(historyData.length / itemsPerPage);
        if (currentPage < totalPages) { currentPage++; renderHistory(); }
    }
});

async function runEmailTest() {
    const btn = document.getElementById('btn-test-email');
    if (!btn) return;
    btn.disabled = true;
    btn.textContent = 'Sending...';
    try {
        const response = await fetch('/api/test-email', { method: 'POST' });
        if (response.ok) alert('Test email sent! Check your inbox.');
        else alert('Error triggering email test.');
    } catch (error) {
        alert('Failed to trigger email test.');
    } finally {
        btn.disabled = false;
        btn.textContent = 'Test Email';
    }
}

async function deleteClient(clientId) {
    if (!confirm('Are you sure you want to delete this POC location?')) return;
    try {
        const response = await fetch(`/api/clients/${clientId}`, { method: 'DELETE' });
        if (response.ok) fetchClients();
    } catch (error) {
        console.error('Error deleting client:', error);
    }
}

// Form Submission
const clientForm = document.getElementById('client-form');
if (clientForm) {
    clientForm.onsubmit = async (e) => {
        e.preventDefault();
        const formData = new FormData(e.target);
        const data = Object.fromEntries(formData.entries());
        const clientId = data.client_id;
        const method = clientId ? 'PUT' : 'POST';
        const url = clientId ? `/api/clients/${clientId}` : '/api/clients';

        try {
            const response = await fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            if (response.ok) {
                toggleModal(false);
                fetchClients();
            } else {
                const err = await response.json();
                alert(`Error: ${err.error}`);
            }
        } catch (error) {
            console.error('Error saving client:', error);
        }
    };
}
