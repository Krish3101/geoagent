// V3 WebSocket and Fetch Logic

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

const API_BASE = "http://localhost:8000";
const SESSION_KEY = "geoagent_v4_session";
let currentMode = 'vector';
let sessionId = localStorage.getItem(SESSION_KEY);
let selectedFile = null;
let currentRunId = null;
let currentWs = null;

// Map Initialization
let map = L.map('map-preview').setView([0, 0], 2);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
    subdomains: 'abcd',
    maxZoom: 20
}).addTo(map);

let currentGeoJsonLayer = null;

async function init() {
    const modeToggle = document.getElementById('agent-mode');
    modeToggle.addEventListener('change', (e) => {
        currentMode = e.target.value;
        sessionId = localStorage.getItem(SESSION_KEY);
        
        chatMessages.innerHTML = '';
        const welcome = currentMode === 'vector' 
            ? "Hello! I'm the Vector Agent. I can download boundaries and vector features for your areas of interest. What would you like to explore?"
            : "Hello! I'm the Raster Agent. I can process satellite imagery and DEM files. What would you like to explore?";
        appendMessage('agent', welcome);
        
        if (sessionId) {
            sessionDisplay.textContent = `Session: ${sessionId}`;
            loadHistory();
        } else {
            sessionDisplay.textContent = `Session: Initializing`;
        }
    });

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
            reader.onload = function (e) {
                try { displayOnMap(JSON.parse(e.target.result)); } catch (err) { }
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
        if (currentGeoJsonLayer) map.removeLayer(currentGeoJsonLayer);
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
    }
}

function displayOnMap(geojson) {
    if (currentGeoJsonLayer) map.removeLayer(currentGeoJsonLayer);
    currentGeoJsonLayer = L.geoJSON(geojson, {
        style: { color: '#0ea5e9', weight: 3, fillOpacity: 0.2 }
    }).addTo(map);
    map.fitBounds(currentGeoJsonLayer.getBounds(), { padding: [50, 50] });
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
        const data = await res.json();
        chatMessages.innerHTML = '';
        data.messages.forEach(msg => appendMessage(msg.role, msg.content, false));
        scrollToBottom();
    } catch (e) {
        console.error(e);
    }
}

async function sendMessage() {
    const text = chatInput.value.trim();
    if (!text && !selectedFile) return;

    appendMessage('user', text || `[Uploaded File: ${selectedFile.name}]`);
    chatInput.value = '';
    chatInput.disabled = true;
    sendBtn.disabled = true;

    try {
        const payload = { message: text };
        if (sessionId) payload.session_id = sessionId;

        // Simplified for v3, handling file uploads would be separate logic routed to backend
        const token = localStorage.getItem('geoagent_token') || 'default_secret_token_123';
        const res = await fetch(`${API_BASE}/api/chat`, {
            method: 'POST',
            headers: { 
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify(payload)
        });
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
        appendMessage('agent', 'Error parsing request.');
        enableInput();
    }
}

function enableInput() {
    chatInput.disabled = false;
    sendBtn.disabled = false;
    chatInput.focus();
    const spinner = document.getElementById('agent-spinner');
    if (spinner) spinner.remove();
}

function startWebSocketStreaming(runId) {
    activeRunCard.style.display = 'flex';
    logPanel.textContent = '';

    // Connect WebSocket to FastAPI V3
    const wsProtocol = API_BASE.replace('http', 'ws');
    currentWs = new WebSocket(`${wsProtocol}/ws/task/${runId}`);

    currentWs.onmessage = function (event) {
        // Assume FastAPI sends raw status or log text
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
        appendMessage("agent", "Process finished.");
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
                `<a href="${API_BASE}${f.url}" download class="artifact-link" style="display: block; margin: 4px 0; color: #0ea5e9;">📄 ${f.name}</a>`
            ).join('');
        }
        if (data.raster && data.raster.length > 0) {
            rPanel.innerHTML = data.raster.map(f =>
                `<a href="${API_BASE}${f.url}" download class="artifact-link" style="display: block; margin: 4px 0; color: #0ea5e9;">📄 ${f.name}</a>`
            ).join('');
        }
    } catch (err) {
        console.error("Failed fetching artifacts", err);
    }
}

init();
