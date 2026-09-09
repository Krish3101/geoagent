// GeoAgent Client Application Logic

const chatMessages = document.getElementById('chat-messages');
const chatInput = document.getElementById('chat-input');
const sendBtn = document.getElementById('send-btn');
const sessionDisplay = document.getElementById('session-display');
const activeRunCard = document.getElementById('active-run-card');
const logPanel = document.getElementById('log-panel');
const fileUpload = document.getElementById('file-upload');
const fileDisplay = document.getElementById('file-display');
const fileName = document.getElementById('file-name');
const removeFileBtn = document.getElementById('remove-file');

const API_BASE = window.location.origin;
const SESSION_KEY = "geoagent_session_id";
let sessionId = localStorage.getItem(SESSION_KEY);
let selectedFile = null;
let currentRunId = null;
let currentWs = null;

// Map Initialization
let map = L.map('map-preview').setView([20, 0], 2);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    subdomains: 'abcd',
    maxZoom: 20
}).addTo(map);

let currentGeoJsonLayer = null;

function getAuthToken() {
    return localStorage.getItem('geoagent_token') || 'default_secret_token_123';
}

async function init() {
    sendBtn.addEventListener('click', sendMessage);
    chatInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') sendMessage();
    });

    fileUpload.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            selectedFile = e.target.files[0];
            fileName.textContent = selectedFile.name;
            fileDisplay.style.display = 'inline-flex';

            const reader = new FileReader();
            reader.onload = function (event) {
                try { 
                    const geojson = JSON.parse(event.target.result);
                    displayOnMap(geojson); 
                } catch (err) {
                    console.error("Invalid GeoJSON file:", err);
                }
            };
            if (selectedFile.name.endsWith('.geojson') || selectedFile.name.endsWith('.json')) {
                reader.readAsText(selectedFile);
            }
        }
    });

    removeFileBtn.addEventListener('click', () => {
        selectedFile = null;
        fileUpload.value = '';
        fileDisplay.style.display = 'none';
        if (currentGeoJsonLayer) {
            map.removeLayer(currentGeoJsonLayer);
            currentGeoJsonLayer = null;
        }
        document.getElementById('open-map-btn').style.display = 'none';
    });

    document.getElementById('open-map-btn').addEventListener('click', () => {
        document.getElementById('map-modal').style.display = 'flex';
        document.getElementById('open-map-btn').classList.remove('glow');
        setTimeout(() => map.invalidateSize(), 50);
    });

    document.getElementById('close-map-btn').addEventListener('click', () => {
        document.getElementById('map-modal').style.display = 'none';
    });

    if (sessionId) {
        sessionDisplay.textContent = `Session: ${sessionId}`;
        await loadHistory();
    } else {
        sessionDisplay.textContent = `Session: New`;
    }
}

function displayOnMap(geojson) {
    if (currentGeoJsonLayer) map.removeLayer(currentGeoJsonLayer);
    currentGeoJsonLayer = L.geoJSON(geojson, {
        style: { color: '#0ea5e9', weight: 3, fillOpacity: 0.25 }
    }).addTo(map);
    map.fitBounds(currentGeoJsonLayer.getBounds(), { padding: [40, 40] });
    document.getElementById('open-map-btn').style.display = 'inline-block';
}

function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function appendMessage(role, content, showSpinner = false) {
    const div = document.createElement('div');
    div.className = `message ${role}`;
    div.innerHTML = content + (showSpinner ? '<span class="spinner" id="agent-spinner"></span>' : '');
    chatMessages.appendChild(div);
    scrollToBottom();
}

async function loadHistory() {
    try {
        const res = await fetch(`${API_BASE}/api/session/${sessionId}/history`);
        if (!res.ok) return;
        const data = await res.json();
        chatMessages.innerHTML = '';
        if (data.messages && data.messages.length > 0) {
            data.messages.forEach(msg => appendMessage(msg.role, msg.content, false));
        } else {
            appendMessage('agent', "Hello! I'm GeoAgent, your conversational geospatial assistant. Ask me to download administrative boundaries or OSM map features, fetch Sentinel-2 satellite imagery, compute vegetation indices (NDVI), or attach a custom geometry.");
        }
        scrollToBottom();
    } catch (e) {
        console.error("Failed to load conversation history:", e);
    }
}

async function uploadSelectedGeometry() {
    if (!selectedFile) return true;
    
    const token = getAuthToken();
    const formData = new FormData();
    formData.append('file', selectedFile);
    
    const currentSession = sessionId || ('s_' + Math.random().toString(36).substring(2, 9));
    
    try {
        const res = await fetch(`${API_BASE}/api/upload_geometry?session_id=${currentSession}`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${token}`
            },
            body: formData
        });
        
        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || 'Upload failed');
        }
        
        sessionId = currentSession;
        localStorage.setItem(SESSION_KEY, sessionId);
        sessionDisplay.textContent = `Session: ${sessionId}`;
        
        selectedFile = null;
        fileUpload.value = '';
        fileDisplay.style.display = 'none';
        return true;
    } catch (err) {
        appendMessage('agent', `Failed to upload geometry: ${err.message}`);
        return false;
    }
}

async function sendMessage() {
    const text = chatInput.value.trim();
    if (!text && !selectedFile) return;

    disableInput();

    // If a geometry file is attached, upload it first to set the active area (FR-15, FR-18)
    if (selectedFile) {
        const fileNameAttached = selectedFile.name;
        const success = await uploadSelectedGeometry();
        if (!success) {
            enableInput();
            return;
        }
        appendMessage('user', text ? `${text}\n[Attached geometry: ${fileNameAttached}]` : `[Attached geometry: ${fileNameAttached}]`);
    } else {
        appendMessage('user', text);
    }

    chatInput.value = '';

    try {
        const payload = { message: text || "Process the uploaded area of interest." };
        if (sessionId) payload.session_id = sessionId;

        const token = getAuthToken();
        const res = await fetch(`${API_BASE}/api/chat`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify(payload)
        });

        if (!res.ok) {
            const err = await res.json();
            throw new Error(err.detail || `Server error: ${res.status}`);
        }

        const data = await res.json();

        if (data.session_id) {
            sessionId = data.session_id;
            localStorage.setItem(SESSION_KEY, sessionId);
            sessionDisplay.textContent = `Session: ${sessionId}`;
        }

        if (data.status === 'started' && data.run_id) {
            appendMessage('agent', data.reply, true);
            startWebSocketStreaming(data.run_id);
        } else {
            appendMessage('agent', data.reply);
            enableInput();
        }
    } catch (e) {
        appendMessage('agent', `Error: ${e.message}`);
        enableInput();
    }
}

function disableInput() {
    chatInput.disabled = true;
    sendBtn.disabled = true;
    fileUpload.disabled = true;
}

function enableInput() {
    chatInput.disabled = false;
    sendBtn.disabled = false;
    fileUpload.disabled = false;
    chatInput.focus();
    const spinner = document.getElementById('agent-spinner');
    if (spinner) spinner.remove();
}

function startWebSocketStreaming(runId) {
    activeRunCard.style.display = 'flex';
    logPanel.textContent = '';
    currentRunId = runId;

    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${wsProtocol}//${window.location.host}/ws/task/${runId}`;
    currentWs = new WebSocket(wsUrl);

    currentWs.onmessage = function (event) {
        logPanel.textContent += event.data + "\n";
        logPanel.scrollTop = logPanel.scrollHeight;

        if (event.data.includes("Task Completed") || event.data.includes("Task Failed")) {
            currentWs.close();
            if (event.data.includes("Task Completed")) {
                fetchAndShowArtifacts(runId);
            }
        }
    };

    currentWs.onclose = function () {
        enableInput();
    };

    currentWs.onerror = function () {
        enableInput();
    };
}

async function fetchAndShowArtifacts(runId) {
    try {
        const res = await fetch(`${API_BASE}/api/task/${runId}/artifacts`);
        const data = await res.json();

        const vPanel = document.getElementById('vector-artifacts-panel');
        const rPanel = document.getElementById('raster-artifacts-panel');
        
        if (data.vector && data.vector.length > 0) {
            vPanel.innerHTML = data.vector.map(f =>
                `<div class="artifact-item"><a href="${API_BASE}${f.url}" download class="artifact-link">📄 ${f.name}</a></div>`
            ).join('');
        } else {
            vPanel.innerHTML = '<div class="small-text">No vector files.</div>';
        }

        if (data.raster && data.raster.length > 0) {
            rPanel.innerHTML = data.raster.map(f =>
                `<div class="artifact-item"><a href="${API_BASE}${f.url}" download class="artifact-link">📄 ${f.name}</a></div>`
            ).join('');
        } else {
            rPanel.innerHTML = '<div class="small-text">No raster files.</div>';
        }
    } catch (err) {
        console.error("Failed fetching artifacts:", err);
    }
}

init();
