// The results pane: the Leaflet map, its layers and NDVI legend, and the list of files.
let ndviOverlay = null;
let ndviLegend = null;
let aoiName = null;

const aoiDisplay = document.getElementById("aoi-display");
const artifactList = document.getElementById("artifact-list");
const artifactCountBadge = document.getElementById("artifact-count-badge");
const mapInfo = document.getElementById("map-info");

const map = L.map("map").setView([40.7829, -73.9654], 13);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
}).addTo(map);

const aoiLayerGroup = L.featureGroup().addTo(map);
const vectorLayerGroup = L.featureGroup().addTo(map);
// artifact id -> feature count, for every layer drawn on the map (NDVI counts as 0)
const drawnLayers = new Map();

// Okabe-Ito colours: one per layer, still distinguishable with colour blindness
const LAYER_COLORS = {
  boundary: "#0072B2",
  buildings: "#D55E00",
  roads: "#E69F00",
  waterways: "#56B4E9",
  landuse: "#009E73",
  amenities: "#CC79A7",
  natural: "#F0E442",
};

// Same classes and colours as the preview PNG made in app/geo/ndvi.py
const NDVI_CLASSES = [
  ["#5b7083", "< 0.0", "Water, cloud, snow"],
  ["#8c510a", "0.0–0.15", "Bare soil, built-up"],
  ["#d8b365", "0.15–0.30", "Sparse vegetation"],
  ["#c7eae5", "0.30–0.50", "Moderate"],
  ["#5ab4ac", "0.50–0.70", "Healthy"],
  ["#01665e", "≥ 0.70", "Dense canopy"],
];

// Empties the map and the file list, for a new chat.
function clearResults() {
  aoiLayerGroup.clearLayers();
  vectorLayerGroup.clearLayers();
  removeNdvi();
  drawnLayers.clear();

  artifactList.replaceChildren();
  const artEmpty = document.createElement("div");
  artEmpty.className = "artifacts-empty";
  artEmpty.textContent = "Files you can download will appear here.";
  artifactList.appendChild(artEmpty);
  artifactCountBadge.textContent = "0 files";
  updateMapInfo();
}

function renderAOI(aoi) {
  aoiName = aoi.name;
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
  }
  updateMapInfo();
}

function updateMapInfo() {
  if (drawnLayers.size > 0) {
    // the NDVI overlay is stored as null, so it counts as a layer but not as features
    const counts = [...drawnLayers.values()].filter((n) => n !== null);
    const features = counts.reduce((a, b) => a + b, 0);
    let text = plural(drawnLayers.size, "layer");
    if (counts.length > 0) text += ` · ${plural(features, "feature")}`;
    mapInfo.textContent = text;
  } else if (aoiName) {
    mapInfo.textContent = `Area: ${aoiName}`;
  } else {
    mapInfo.textContent = "No layers loaded";
  }
}

async function loadArtifacts(taskId) {
  try {
    const res = await fetch(`/api/tasks/${taskId}/artifacts`);
    if (!res.ok) return;
    const artifacts = await res.json();

    const all = Array.isArray(artifacts) ? artifacts : [];
    artifactCountBadge.textContent = `${all.length} file${all.length === 1 ? "" : "s"}`;

    artifactList.replaceChildren();
    if (all.length === 0) {
      const emptyDiv = document.createElement("div");
      emptyDiv.className = "artifacts-empty";
      emptyDiv.textContent = "No files produced for this task.";
      artifactList.appendChild(emptyDiv);
      return;
    }

    for (const art of all) {
      renderArtifactCard(art);
      if (art.filename.endsWith(".geojson")) {
        loadGeoJsonToMap(art);
      } else if (art.filename.endsWith("_preview.png")) {
        // only the PNG preview goes on the map; the GeoTIFF is a download only
        loadRasterOverlayToMap(art);
      }
    }
  } catch (err) {
    console.error("Failed to load artifacts:", err);
  }
}

function renderArtifactCard(art) {
  const card = document.createElement("div");
  card.className = "artifact-card";

  let metaInfo = formatSize(art.size_bytes);
  if (art.meta) {
    if (art.meta.feature_count !== undefined) {
      metaInfo += ` • ${plural(art.meta.feature_count, "feature")}`;
    }
    if (art.meta.resolution_m) {
      metaInfo += ` • ${art.meta.resolution_m}m res`;
    }
    if (art.meta.mean_ndvi !== undefined) {
      metaInfo += ` • Mean NDVI: ${art.meta.mean_ndvi}`;
    }
    if (art.meta.crs) {
      metaInfo += ` • ${art.meta.crs}`;
    }
  }

  const infoDiv = document.createElement("div");
  infoDiv.className = "artifact-info";

  const nameDiv = document.createElement("div");
  nameDiv.className = "artifact-name";
  nameDiv.title = art.filename;
  nameDiv.textContent = art.filename; // XSS-safe

  const metaDiv = document.createElement("div");
  metaDiv.className = "artifact-meta";
  metaDiv.textContent = metaInfo;

  infoDiv.appendChild(nameDiv);
  infoDiv.appendChild(metaDiv);

  const downloadBtn = document.createElement("a");
  downloadBtn.className = "artifact-download-btn";
  downloadBtn.href = art.download_url;
  downloadBtn.setAttribute("download", art.filename);
  downloadBtn.textContent = "Download";

  card.appendChild(infoDiv);
  card.appendChild(downloadBtn);
  artifactList.appendChild(card);
}

function formatSize(bytes) {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function plural(n, word) {
  return `${n.toLocaleString()} ${word}${n === 1 ? "" : "s"}`;
}

async function loadGeoJsonToMap(art) {
  if (drawnLayers.has(art.id)) return;
  drawnLayers.set(art.id, 0);
  try {
    const res = await fetch(art.download_url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const geojson = await res.json();

    const layerName = art.meta?.layer || "vector";
    const color = LAYER_COLORS[layerName] || "#000000";

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
    drawnLayers.set(art.id, geojson.features ? geojson.features.length : 0);
    updateMapInfo();
  } catch (err) {
    // forget it, so it isn't counted and a later reload can try again
    drawnLayers.delete(art.id);
    console.error("Failed to load GeoJSON onto map:", err);
  }
}

function loadRasterOverlayToMap(art) {
  if (drawnLayers.has(art.id)) return;

  const bounds = art.bounds;
  if (!bounds || bounds.length !== 4) return;

  // bounds: [minx, miny, maxx, maxy] -> Leaflet: [[south, west], [north, east]]
  const leafletBounds = [
    [bounds[1], bounds[0]],
    [bounds[3], bounds[2]],
  ];

  removeNdvi();
  drawnLayers.set(art.id, null);
  ndviOverlay = L.imageOverlay(art.download_url, leafletBounds, {
    opacity: 0.75,
    interactive: false,
    alt: `NDVI raster for ${aoiName || "the area"}, ${art.meta?.date || ""}`,
    attribution: "Contains modified Copernicus Sentinel data, via Microsoft Planetary Computer",
  }).addTo(map);
  ndviLegend = buildNdviLegend(art.meta || {});
  ndviLegend.addTo(map);

  map.fitBounds(leafletBounds, { padding: [20, 20] });
  updateMapInfo();
}

function removeNdvi() {
  if (ndviOverlay) map.removeLayer(ndviOverlay);
  if (ndviLegend) ndviLegend.remove();
  ndviOverlay = null;
  ndviLegend = null;
  // only one NDVI overlay is shown at a time
  for (const [id, n] of drawnLayers) if (n === null) drawnLayers.delete(id);
}

// Static legend for the 6 NDVI classes, with the scene date and mean NDVI
function buildNdviLegend(meta) {
  const legend = L.control({ position: "bottomleft" });
  legend.onAdd = () => {
    const box = L.DomUtil.create("div", "ndvi-legend");
    box.setAttribute("role", "group");
    box.setAttribute("aria-label", "NDVI legend");

    const title = document.createElement("strong");
    title.textContent = "NDVI (vegetation index)";
    box.appendChild(title);

    const list = document.createElement("ul");
    for (const [color, range, label] of NDVI_CLASSES) {
      const item = document.createElement("li");
      const swatch = document.createElement("span");
      swatch.className = "swatch";
      swatch.style.background = color;
      swatch.setAttribute("aria-hidden", "true");
      item.append(swatch, `${range} ${label}`);
      list.appendChild(item);
    }
    box.appendChild(list);

    const caption = document.createElement("div");
    caption.className = "legend-caption";
    const date = meta.date ? ` · ${meta.date}` : "";
    caption.textContent = `Sentinel-2 L2A${date}`;
    box.appendChild(caption);

    if (meta.mean_ndvi !== undefined) {
      const mean = document.createElement("div");
      mean.className = "legend-mean";
      mean.textContent = `Mean NDVI ${meta.mean_ndvi}`;
      box.appendChild(mean);
    }
    return box;
  };
  return legend;
}
