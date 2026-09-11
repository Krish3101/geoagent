let sessionId = localStorage.getItem("geoagent_session_id");
let currentTaskId = null;
let eventSource = null;

const sessionBadge = document.getElementById("session-badge");
const btnNewSession = document.getElementById("btn-new-session");
const aoiDisplay = document.getElementById("aoi-display");
const chatTranscript = document.getElementById("chat-transcript");
const chatForm = document.getElementById("chat-form");
const promptInput = document.getElementById("prompt-input");
const btnSend = document.getElementById("btn-send");
const aoiFileInput = document.getElementById("aoi-file-input");
const uploadStatus = document.getElementById("upload-status");
const taskStatusBadge = document.getElementById("task-status-badge");
const timeline = document.getElementById("timeline");
const artifactList = document.getElementById("artifact-list");
const artifactCountBadge = document.getElementById("artifact-count-badge");
const mapInfo = document.getElementById("map-info");

const map = L.map("map").setView([40.7829, -73.9654], 13);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: "&copy; OpenStreetMap contributors",
  maxZoom: 19,
}).addTo(map);

const aoiLayerGroup = L.featureGroup().addTo(map);
const vectorLayerGroup = L.featureGroup().addTo(map);

// Color palette for layers
const LAYER_COLORS = {
  boundary: "#2563eb",
  buildings: "#dc2626",
  roads: "#ea580c",
  waterways: "#0284c7",
  landuse: "#16a34a",
  amenities: "#7c3aed",
  natural: "#059669",
};

async function init() {
  if (sessionId) {
    try {
      const res = await fetch(`/api/sessions/${sessionId}`);
      if (res.ok) {
        const data = await res.json();
        updateSessionUI(data);
        await loadMessages();
        return;
      }
    } catch (e) {
      console.warn("Could not restore session:", e);
    }
  }
  await createNewSession();
}

async function createNewSession() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  try {
    const res = await fetch("/api/sessions", { method: "POST" });
    const data = await res.json();
    sessionId = data.id;
    localStorage.setItem("geoagent_session_id", sessionId);
    updateSessionUI(data);
    chatTranscript.innerHTML = `
      <div class="system-bubble welcome-msg">
        <strong>Welcome to GeoAgent.</strong> Ask for spatial data by place name, or drop a <code>.geojson</code> file to set your Area of Interest.
      </div>
    `;
    aoiLayerGroup.clearLayers();
    vectorLayerGroup.clearLayers();
    timeline.innerHTML = '<div class="timeline-empty">Waiting for a request...</div>';
    artifactList.innerHTML = '<div class="artifacts-empty">Generated GIS files will appear here.</div>';
    artifactCountBadge.textContent = "0 files";
    taskStatusBadge.className = "badge badge-neutral";
    taskStatusBadge.textContent = "Idle";
    mapInfo.textContent = "No layers loaded";
  } catch (err) {
    console.error("Failed to create session:", err);
  }
}

function updateSessionUI(sessionData) {
  sessionBadge.textContent = `Session: ${sessionData.id.slice(0, 8)}…`;
  if (sessionData.aoi) {
    renderAOI(sessionData.aoi);
  } else {
    aoiDisplay.textContent = "None";
  }
}

function renderAOI(aoi) {
  aoiDisplay.textContent = `${aoi.name} (${aoi.area_km2} km²)`;
  aoiLayerGroup.clearLayers();
  if (aoi.geometry) {
    const aoiLayer = L.geoJSON(aoi.geometry, {
      style: {
        color: "#2563eb",
        weight: 3,
        dashArray: "4, 4",
        fillColor: "#3b82f6",
        fillOpacity: 0.1,
      },
    });
    aoiLayerGroup.addLayer(aoiLayer);
    map.fitBounds(aoiLayer.getBounds(), { padding: [20, 20] });
    mapInfo.textContent = `AOI: ${aoi.name} (${aoi.area_km2} km²)`;
  }
}

async function loadMessages() {
  try {
    const res = await fetch(`/api/sessions/${sessionId}/messages`);
    if (!res.ok) return;
    const messages = await res.json();
    chatTranscript.innerHTML = "";
    for (const msg of messages) {
      appendMessage(msg.role, msg.content);
    }
  } catch (err) {
    console.error("Failed to load messages:", err);
  }
}

function appendMessage(role, content) {
  const bubble = document.createElement("div");
  bubble.className = `message-bubble ${role}-bubble`;
  bubble.textContent = content;
  chatTranscript.appendChild(bubble);
  chatTranscript.scrollTop = chatTranscript.scrollHeight;
}

promptInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    chatForm.dispatchEvent(new Event("submit", { cancelable: true }));
  }
});

chatForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const content = promptInput.value.trim();
  if (!content) return;

  promptInput.value = "";
  btnSend.disabled = true;
  appendMessage("user", content);

  try {
    const res = await fetch(`/api/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });

    if (!res.ok) {
      const err = await res.json();
      appendMessage("system", `Error: ${err.detail || "Failed to submit message"}`);
      btnSend.disabled = false;
      return;
    }

    const data = await res.json();
    currentTaskId = data.task_id;
    startTaskListening(data.task_id, data.events_url);
  } catch (err) {
    appendMessage("system", `Network error: ${err.message}`);
    btnSend.disabled = false;
  }
});

// SSE Live Events Streaming
function startTaskListening(taskId, eventsUrl) {
  if (eventSource) {
    eventSource.close();
  }

  taskStatusBadge.className = "badge badge-running";
  taskStatusBadge.textContent = "Running";
  timeline.innerHTML = "";

  eventSource = new EventSource(eventsUrl);

  eventSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      appendTimelineEvent(data);

      if (data.stage === "done") {
        taskStatusBadge.className = "badge badge-succeeded";
        taskStatusBadge.textContent = "Succeeded";
        finishTask(taskId);
      } else if (data.stage === "error") {
        taskStatusBadge.className = "badge badge-failed";
        taskStatusBadge.textContent = "Failed";
        finishTask(taskId);
      }
    } catch (e) {
      console.error("Error parsing event:", e);
    }
  };

  eventSource.onerror = () => {
    // SSE disconnected or closed
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    btnSend.disabled = false;
  };
}

function appendTimelineEvent(ev) {
  const item = document.createElement("div");
  item.className = "timeline-item";

  const stage = document.createElement("span");
  stage.className = "timeline-stage";
  stage.textContent = ev.stage;

  const msg = document.createElement("span");
  msg.className = "timeline-msg";
  msg.textContent = ev.message;

  item.appendChild(stage);
  item.appendChild(msg);
  timeline.appendChild(item);
  timeline.scrollTop = timeline.scrollHeight;
}

async function finishTask(taskId) {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  btnSend.disabled = false;

  await loadMessages();

  // Refresh session info to catch updated AOI
  const res = await fetch(`/api/sessions/${sessionId}`);
  if (res.ok) {
    const data = await res.json();
    updateSessionUI(data);
  }

  await loadArtifacts(taskId);
}

async function loadArtifacts(taskId) {
  try {
    const res = await fetch(`/api/tasks/${taskId}/artifacts`);
    if (!res.ok) return;
    const data = await res.json();

    const all = [...(data.vector || []), ...(data.raster || [])];
    artifactCountBadge.textContent = `${all.length} file${all.length === 1 ? "" : "s"}`;

    if (all.length === 0) {
      artifactList.innerHTML = '<div class="artifacts-empty">No files produced for this task.</div>';
      return;
    }

    artifactList.innerHTML = "";
    for (const art of all) {
      renderArtifactCard(art);
      if (art.filename.endsWith(".geojson")) {
        loadGeoJsonToMap(art);
      }
    }
  } catch (err) {
    console.error("Failed to load artifacts:", err);
  }
}

function renderArtifactCard(art) {
  const card = document.createElement("div");
  card.className = "artifact-card";

  const sizeKb = (art.size_bytes / 1024).toFixed(1);
  let metaInfo = `${sizeKb} KB`;
  if (art.meta) {
    if (art.meta.feature_count !== undefined) {
      metaInfo += ` • ${art.meta.feature_count} features`;
    }
    if (art.meta.resolution_m) {
      metaInfo += ` • ${art.meta.resolution_m}m res`;
    }
    if (art.meta.crs) {
      metaInfo += ` • ${art.meta.crs}`;
    }
  }

  card.innerHTML = `
    <div class="artifact-info">
      <div class="artifact-name" title="${art.filename}">${art.filename}</div>
      <div class="artifact-meta">${metaInfo}</div>
    </div>
    <a href="${art.download_url}" class="artifact-download-btn" download="${art.filename}">Download</a>
  `;
  artifactList.appendChild(card);
}

async function loadGeoJsonToMap(art) {
  try {
    const res = await fetch(art.download_url);
    if (!res.ok) return;
    const geojson = await res.json();

    const layerName = art.meta?.layer || "vector";
    const color = LAYER_COLORS[layerName] || "#4f46e5";

    const layer = L.geoJSON(geojson, {
      style: {
        color: color,
        weight: 2,
        fillColor: color,
        fillOpacity: 0.35,
      },
      pointToLayer: (feature, latlng) => {
        return L.circleMarker(latlng, {
          radius: 5,
          color: color,
          fillColor: color,
          fillOpacity: 0.8,
        });
      },
    });

    vectorLayerGroup.addLayer(layer);
    const bounds = vectorLayerGroup.getBounds();
    if (bounds.isValid()) {
      map.fitBounds(bounds, { padding: [20, 20] });
    }
    mapInfo.textContent = `Displaying: ${art.filename}`;
  } catch (err) {
    console.error("Failed to load GeoJSON onto map:", err);
  }
}

// Bring-Your-Own AOI File Upload
aoiFileInput.addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;

  uploadStatus.textContent = "Uploading AOI...";
  const reader = new FileReader();

  reader.onload = async (evt) => {
    try {
      const geojson = JSON.parse(evt.target.result);
      const res = await fetch(`/api/sessions/${sessionId}/aoi`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(geojson),
      });

      if (!res.ok) {
        const err = await res.json();
        uploadStatus.textContent = `Upload failed: ${err.detail || "Invalid GeoJSON"}`;
        return;
      }

      const aoi = await res.json();
      renderAOI(aoi);
      uploadStatus.textContent = `Uploaded: ${aoi.name}`;
      await loadMessages();
    } catch (err) {
      uploadStatus.textContent = `Invalid file: ${err.message}`;
    } finally {
      aoiFileInput.value = "";
    }
  };

  reader.readAsText(file);
});

btnNewSession.addEventListener("click", () => {
  createNewSession();
});

init();
