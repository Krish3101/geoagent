// The conversation pane: the chat session, sending messages, and live progress.
let sessionId = localStorage.getItem("geoagent_session_id");
let eventSource = null;

const btnNewSession = document.getElementById("btn-new-session");
const chatTranscript = document.getElementById("chat-transcript");
const chatForm = document.getElementById("chat-form");
const promptInput = document.getElementById("prompt-input");
const btnSend = document.getElementById("btn-send");
const taskStatusBadge = document.getElementById("task-status-badge");
const timeline = document.getElementById("timeline");

const STAGE_LABELS = {
  starting: "Starting",
  resolve_area: "Finding the place",
  extract_vector: "Downloading OSM layers",
  fetch_ndvi: "Computing NDVI",
  done: "Done",
  error: "Failed",
};

const EXAMPLE_PROMPTS = [
  "Boundary of Central Park",
  "Buildings and roads in Soho, New York",
  "NDVI of Central Park, July 2025",
];

// The empty-chat screen: a short intro and example prompts that fill the input box.
function welcome() {
  const bubble = document.createElement("div");
  bubble.className = "system-bubble welcome-msg";
  const title = document.createElement("strong");
  title.textContent = "Welcome to GeoAgent.";
  bubble.append(
    title,
    " The LLM picks tools; Python computes every coordinate. Results appear on the map and as downloadable GeoJSON/GeoTIFF files."
  );

  const chips = document.createElement("div");
  chips.className = "prompt-chips";
  for (const prompt of EXAMPLE_PROMPTS) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip-btn";
    chip.textContent = prompt;
    chip.addEventListener("click", () => {
      promptInput.value = prompt;
      promptInput.focus();
    });
    chips.appendChild(chip);
  }
  bubble.appendChild(chips);
  chatTranscript.replaceChildren(bubble);
}

async function init() {
  welcome();
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
  // The closed stream will never call finishTask for a running task
  btnSend.disabled = false;
  btnSend.textContent = "Send";
  chatTranscript.removeAttribute("aria-busy");
  try {
    const res = await fetch("/api/sessions", { method: "POST" });
    const data = await res.json();
    sessionId = data.id;
    localStorage.setItem("geoagent_session_id", sessionId);
    updateSessionUI(data);

    welcome();
    clearResults();

    timeline.replaceChildren();
    const tlEmpty = document.createElement("div");
    tlEmpty.className = "timeline-empty";
    tlEmpty.textContent = "Nothing running";
    timeline.appendChild(tlEmpty);

    taskStatusBadge.className = "badge badge-neutral";
    taskStatusBadge.textContent = "Idle";
  } catch (err) {
    console.error("Failed to create session:", err);
  }
}

function updateSessionUI(sessionData) {
  if (sessionData.aoi) {
    renderAOI(sessionData.aoi);
  } else {
    aoiName = null;
    aoiDisplay.textContent = "not set yet";
  }
}

async function loadMessages() {
  try {
    const res = await fetch(`/api/sessions/${sessionId}/messages`);
    if (!res.ok) return;
    const messages = await res.json();
    if (messages.length === 0) {
      welcome();
      return;
    }
    chatTranscript.replaceChildren();
    let lastTaskId = null;
    for (const msg of messages) {
      appendMessage(msg.role, msg.content, msg.role === "assistant" && msg.task_status === "failed");
      if (msg.task_id) lastTaskId = msg.task_id;
    }
    if (lastTaskId) await loadArtifacts(lastTaskId);
  } catch (err) {
    console.error("Failed to load messages:", err);
  }
}

function appendMessage(role, content, isError = false) {
  const bubble = document.createElement("div");
  bubble.className = `message-bubble ${role}-bubble`;
  if (isError) {
    bubble.classList.add("error-bubble");
    bubble.setAttribute("role", "alert");
  }
  bubble.textContent = content.replace(/\*\*(.+?)\*\*/g, "$1");
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
  if (btnSend.disabled) return;

  const content = promptInput.value.trim();
  if (!content) return;

  promptInput.value = "";
  btnSend.disabled = true;
  btnSend.textContent = "Working…";

  appendMessage("user", content);

  try {
    const res = await fetch(`/api/sessions/${sessionId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });

    if (res.status === 409) {
      appendMessage("assistant", "Another task is already in progress for this session. Please wait.", true);
      btnSend.disabled = false;
      btnSend.textContent = "Send";
      return;
    }

    if (!res.ok) {
      const err = await res.json();
      appendMessage("assistant", `Error: ${formatDetail(err.detail)}`, true);
      btnSend.disabled = false;
      btnSend.textContent = "Send";
      return;
    }

    const data = await res.json();
    startTaskListening(data.task_id, data.events_url);
  } catch (err) {
    console.error("Failed to send message:", err);
    appendMessage("assistant", "Could not send message. Please check server.", true);
    btnSend.disabled = false;
    btnSend.textContent = "Send";
  }
});

// FastAPI sends a string for HTTPException but a list of objects for 422 validation errors
function formatDetail(detail) {
  if (!detail) return "Request failed";
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d && d.msg ? d.msg : String(d)))
      .join("; ");
  }
  return "Request failed";
}

function startTaskListening(taskId, eventsUrl) {
  timeline.replaceChildren();
  taskStatusBadge.className = "badge badge-running";
  taskStatusBadge.textContent = "Running";
  chatTranscript.setAttribute("aria-busy", "true");
  listen(taskId, eventsUrl);
}

function listen(taskId, eventsUrl) {
  if (eventSource) {
    eventSource.close();
  }
  eventSource = new EventSource(eventsUrl);

  eventSource.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data);
      appendTimelineEvent(ev);

      if (ev.stage === "done" || ev.stage === "error") {
        eventSource.close();
        eventSource = null;
        finishTask(taskId, ev.stage === "done");
      }
    } catch (err) {
      console.error("SSE parse error:", err);
    }
  };

  eventSource.onerror = async () => {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    // the stream dropped: the task has finished once its reply is in the messages
    const reply = await findReply(taskId);
    if (reply) {
      finishTask(taskId, reply.task_status === "succeeded");
    } else {
      // still running (or the server is briefly unreachable): stay in the Working state;
      // the stream replays every event, so start the list again
      setTimeout(() => {
        timeline.replaceChildren();
        listen(taskId, eventsUrl);
      }, 2000);
    }
  };
}

async function findReply(taskId) {
  try {
    const res = await fetch(`/api/sessions/${sessionId}/messages`);
    if (res.ok) {
      return (await res.json()).find((m) => m.task_id === taskId && m.role === "assistant");
    }
  } catch (err) {
    console.error("Failed to load messages:", err);
  }
  return undefined;
}

function appendTimelineEvent(ev) {
  const item = document.createElement("div");
  item.className = "timeline-item";
  if (ev.stage === "error") {
    item.classList.add("error");
  }

  const stageSpan = document.createElement("span");
  stageSpan.className = "timeline-stage";
  stageSpan.textContent = STAGE_LABELS[ev.stage] || ev.stage;

  const msgSpan = document.createElement("span");
  msgSpan.className = "timeline-msg";
  msgSpan.textContent = ev.message;

  item.appendChild(stageSpan);
  item.appendChild(msgSpan);
  timeline.appendChild(item);
  timeline.scrollTop = timeline.scrollHeight;
}

async function finishTask(taskId, succeeded) {
  taskStatusBadge.className = succeeded ? "badge badge-succeeded" : "badge badge-failed";
  taskStatusBadge.textContent = succeeded ? "Succeeded" : "Failed";
  btnSend.disabled = false;
  btnSend.textContent = "Send";
  chatTranscript.removeAttribute("aria-busy");

  await refreshSession();
  await loadMessages();
  await loadArtifacts(taskId);
}

async function refreshSession() {
  const res = await fetch(`/api/sessions/${sessionId}`);
  if (res.ok) {
    const data = await res.json();
    updateSessionUI(data);
  }
}

btnNewSession.addEventListener("click", () => {
  createNewSession();
});

init();
