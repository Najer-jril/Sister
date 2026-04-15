<template>
  <div class="app-container">
    <header class="topbar">
      <div class="topbar-left">
        <span class="jester-badge">SISTER Tugas 2</span>
        <h1 class="topbar-title">Simulasi Req-Resp dan Pub-Sub</h1>
      </div>
      <div class="topbar-right">
        <div class="status-indicator" :class="globalStatus">
          <span class="status-dot"></span>
          <span class="status-text">{{ globalStatusLabel }}</span>
        </div>
        <span class="clock">{{ clock }}</span>
      </div>
    </header>

    <div class="layout-grid">
      <aside class="panel sidebar-left">
        <section class="control-group">
          <h2 class="section-title">Latency Simulation</h2>
          <div class="slider-container">
            <span class="mono-value"> {{ latency }}ms </span>

            <input
              type="range"
              v-model.number="latency"
              min="0"
              max="3000"
              step="100"
              class="apple-slider"
              :style="sliderStyle"
            />
          </div>
        </section>

        <section class="control-group">
          <h2 class="section-title">Model Selection</h2>
          <div class="model-list">
            <button
              v-for="m in models"
              :key="m.id"
              class="apple-btn model-btn"
              :class="{ active: activeModel === m.id, disabled: isBusy }"
              @click="!isBusy && runModel(m.id)"
            >
              <div class="model-icon-wrap" :class="'theme-' + m.theme">
                <span class="model-tag">{{ m.tag }}</span>
              </div>
              <span class="model-name">{{ m.name }}</span>
            </button>
          </div>
        </section>
      </aside>

      <main class="panel canvas-area">
        <div class="canvas-stage">
          <div class="main-bg"></div>

          <div
            v-for="node in topologyNodes"
            :key="node.id"
            class="stage-node"
            :class="[node.type, { 'is-active': activeNodes.includes(node.id) }]"
            :style="{ left: node.x + '%', top: node.y + '%' }"
          >
            <div class="node-graphic">
              <span class="node-icon">{{ node.icon }}</span>
              <div class="node-pulse-ring"></div>
            </div>
            <div class="node-info">
              <h3 class="node-label">{{ node.label }}</h3>
              <span class="node-sub">{{ node.sub }}</span>
            </div>
          </div>

          <svg
            class="connection-layer"
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
          >
            <defs>
              <marker
                id="arrow-rr"
                markerWidth="6"
                markerHeight="6"
                refX="5"
                refY="3"
                orient="auto"
              >
                <path d="M0,0 L6,3 L0,6 Z" fill="#d97a1e" />
              </marker>
              <marker
                id="arrow-mqtt"
                markerWidth="6"
                markerHeight="6"
                refX="5"
                refY="3"
                orient="auto"
              >
                <path d="M0,0 L6,3 L0,6 Z" fill="#2e3b2f" />
              </marker>
              <marker
                id="arrow-mq"
                markerWidth="6"
                markerHeight="6"
                refX="5"
                refY="3"
                orient="auto"
              >
                <path d="M0,0 L6,3 L0,6 Z" fill="#9c4f12" />
              </marker>
              <marker
                id="arrow-idle"
                markerWidth="6"
                markerHeight="6"
                refX="5"
                refY="3"
                orient="auto"
              >
                <path d="M0,0 L6,3 L0,6 Z" fill="#2a2f2a" />
              </marker>
            </defs>

            <line
              v-for="(conn, i) in visibleConnections"
              :key="i"
              :x1="conn.x1"
              :y1="conn.y1"
              :x2="conn.x2"
              :y2="conn.y2"
              :stroke="conn.color"
              :stroke-width="conn.active ? '0.4' : '0.2'"
              :stroke-dasharray="conn.active ? '2,1' : '0.5,1'"
              :marker-end="
                conn.active
                  ? 'url(#arrow-' + activeModel + ')'
                  : 'url(#arrow-idle)'
              "
              :opacity="conn.active ? 1 : 0.3"
              class="conn-line"
            />
          </svg>

          <div
            v-if="packetVisible"
            class="data-packet"
            :class="'theme-bg-' + currentModelMeta.theme"
            :style="packetStyle"
          ></div>
        </div>
      </main>

      <aside class="panel sidebar-right">
        <div class="log-header">
          <h2 class="section-title">histori log</h2>
          <button class="icon-btn" @click="history = []" title="Clear Logs">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="2"
            >
              <path
                d="M3 6h18M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"
              />
            </svg>
          </button>
        </div>
        <div class="log-container" ref="logList">
          <div v-if="history.length === 0" class="log-empty">
            <div class="empty-icon">🎪</div>
            <p>belum ngapa-ngapain, gada history</p>
          </div>
          <transition-group name="log-anim" tag="div" class="log-list">
            <div
              v-for="(e, i) in history"
              :key="e.id"
              class="log-card"
              :class="[
                'border-' + e.theme,
                e.status === 'ERROR' ? 'is-error' : '',
              ]"
            >
              <div class="log-card-header">
                <span class="log-time">{{ e.time }}</span>
                <span class="log-dur">{{ e.duration }}ms</span>
              </div>
              <div class="log-card-body">
                <span class="log-badge" :class="'theme-bg-' + e.theme">{{
                  e.model.toUpperCase()
                }}</span>
                <span class="log-msg">{{ e.msg }}</span>
              </div>
            </div>
          </transition-group>
        </div>
      </aside>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onUnmounted } from "vue";
import { invoke } from "@tauri-apps/api/core";

const clock = ref("");
const latency = ref(1000);
const isBusy = ref(false);
const activeModel = ref("idle");
const activeNodes = ref([]);
const packetVisible = ref(false);
const packetStyle = ref({});
const overlayStatus = ref("idle");
const history = ref([]);
let logIdCounter = 0;
let clockTimer;

const models = [
  {
    id: "rr",
    name: "Request-Response",
    tag: "RR",
    theme: "warm",
    fullName: "Request-Response Pattern",
  },
  {
    id: "mqtt",
    name: "MQTT Pub-Sub",
    tag: "PS",
    theme: "olive",
    fullName: "Publish-Subscribe (MQTT)",
  },
];

const currentModelMeta = computed(
  () => models.find((m) => m.id === activeModel.value) || models[0],
);

const latencyPercent = computed(() => (latency.value / 3000) * 100);
const sliderStyle = computed(() => ({
  background: `linear-gradient(90deg, var(--accent-warm) 0%, var(--accent-mustard) ${latencyPercent.value}%, var(--bg-surface) ${latencyPercent.value}%)`,
}));

const globalStatus = computed(() => (isBusy.value ? "is-busy" : "is-idle"));
const globalStatusLabel = computed(() =>
  isBusy.value ? "Lagi Jalan" : "Belum Mulai",
);

// pub sub
const topologyNodes = computed(() => {
  if (activeModel.value === "mqtt") {
    return [
      {
        id: "pub",
        label: "Publisher",
        sub: "Aktor A",
        icon: "🎩",
        type: "client",
        x: 15,
        y: 35,
      },
      {
        id: "broker",
        label: "Broker",
        sub: "Ringmaster",
        icon: "🎪",
        type: "broker",
        x: 45,
        y: 35,
      },
      {
        id: "sub1",
        label: "Subscriber 1",
        sub: "Aktor B",
        icon: "🎭",
        type: "server",
        x: 75,
        y: 15,
      },
      {
        id: "sub2",
        label: "Subscriber 2",
        sub: "Aktor C",
        icon: "🎭",
        type: "server",
        x: 75,
        y: 55,
      },
    ];
  }
  return [
    {
      id: "client",
      label: "Client",
      sub: "Sender",
      icon: "🃏",
      type: "client",
      x: 20,
      y: 35,
    },
    {
      id: "server",
      label: "Server",
      sub: "Receiver",
      icon: "👑",
      type: "server",
      x: 70,
      y: 35,
    },
  ];
});

const visibleConnections = computed(() => {
  const isActive = activeNodes.value.length > 0;
  const colorMap = {
    rr: "#d97a1e",
    mqtt: "#2e3b2f",
  };
  const c = colorMap[activeModel.value];

  if (activeModel.value === "mqtt")
    return [
      {
        x1: 15,
        y1: 35,
        x2: 45,
        y2: 35,
        color: c,
        active: isActive && activeNodes.value.includes("broker"),
      },
      {
        x1: 45,
        y1: 35,
        x2: 75,
        y2: 15,
        color: c,
        active: isActive && activeNodes.value.includes("sub1"),
      },
      {
        x1: 45,
        y1: 35,
        x2: 75,
        y2: 55,
        color: c,
        active: isActive && activeNodes.value.includes("sub2"),
      },
    ];

  return [{ x1: 20, y1: 35, x2: 70, y2: 35, color: c, active: isActive }];
});

function updateClock() {
  clock.value = new Date().toLocaleTimeString("id-ID", { hour12: false });
}

function addLog(model, msg, duration, status = "OK") {
  const t = new Date().toLocaleTimeString("id-ID", { hour12: false });
  const m = models.find((x) => x.id === model);
  history.value.unshift({
    id: logIdCounter++,
    model,
    theme: m ? m.theme : "warm",
    msg,
    duration: Math.round(duration),
    time: t,
    status,
  });
}

async function animatePacket(fromPct, toPct, topPct, duration) {
  packetVisible.value = true;
  packetStyle.value = {
    left: fromPct + "%",
    top: topPct + "%",
    transition: "none",
  };
  await new Promise((r) => setTimeout(r, 30));
  packetStyle.value = {
    left: toPct + "%",
    top: topPct + "%",
    transition: `left ${duration}ms cubic-bezier(0.4, 0.0, 0.2, 1)`,
  };
  await new Promise((r) => setTimeout(r, duration + 50));
  packetVisible.value = false;
}

// func
async function runRR() {
  activeModel.value = "rr";
  isBusy.value = true;
  overlayStatus.value = "busy";
  activeNodes.value = ["client"];
  const start = performance.now();
  animatePacket(24, 66, 35, latency.value);
  try {
    const res = await invoke("send_request_response", {
      id: "ACT-" + Date.now(),
      latency: latency.value,
    });
    activeNodes.value = ["server"];
    overlayStatus.value = "ok";
    await animatePacket(66, 24, 35, 300);
    addLog("rr", res, performance.now() - start);
  } catch (e) {
    addLog("rr", "Gaffe: " + e, 0, "ERROR");
    overlayStatus.value = "error";
  } finally {
    setTimeout(() => {
      isBusy.value = false;
      activeNodes.value = [];
      overlayStatus.value = "idle";
    }, 500);
  }
}

async function runMQTT() {
  activeModel.value = "mqtt";
  isBusy.value = true;
  overlayStatus.value = "busy";
  activeNodes.value = ["pub"];
  const start = performance.now();
  animatePacket(18, 42, 35, latency.value);
  try {
    await invoke("mqtt_publish", {
      topic: "stage/1",
      message: "cue",
      latency: latency.value,
    });
    activeNodes.value = ["broker", "sub1", "sub2"];
    overlayStatus.value = "ok";
    addLog(
      "mqtt",
      "Terkirim ke semua aktor yang dituju",
      performance.now() - start,
    );
  } catch (e) {
    addLog("mqtt", "Gaffe: " + e, 0, "ERROR");
    overlayStatus.value = "error";
  } finally {
    setTimeout(() => {
      isBusy.value = false;
      activeNodes.value = [];
    }, 600);
  }
}

function runModel(id) {
  if (id === "rr") runRR();
  else if (id === "mqtt") runMQTT();
}

onMounted(() => {
  updateClock();
  clockTimer = setInterval(updateClock, 1000);
});
onUnmounted(() => clearInterval(clockTimer));
</script>

<style scoped>
@import url("https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,500;0,700;1,500&family=JetBrains+Mono:wght@400;600&family=Inter:wght@400;500;600&display=swap");

.app-container {
  --bg-base: #0b0f0c;
  --bg-panel: #121713;
  --bg-surface: #1b211c;
  --bg-raised: #222a23;
  --accent-warm: #d97a1e;
  --accent-mustard: #e0a11b;
  --accent-deep: #9c4f12;
  --accent-olive: #2e3b2f;
  --accent-muted: #6b5a3a;
  --text-main: #e6dcc6;
  --text-dim: #b5a98a;
  --text-muted: #8a7f6a;
  --success: #6f8f5a;
  --error: #a34a2c;
  --border-light: #2a2f2a;
  --border-focus: #3d4a3e;
  --font-ui: "Inter", sans-serif;
  --font-serif: "Playfair Display", serif;
  --font-mono: "JetBrains Mono", monospace;
  --transition: all 0.25s ease;
  --radius-sm: 6px;
  --radius-md: 12px;
  --radius-lg: 18px;
  --shadow-sm: 0 2px 8px rgba(0, 0, 0, 0.4);
  --shadow-md: 0 6px 20px rgba(0, 0, 0, 0.5);
  --shadow-lg: 0 16px 40px rgba(0, 0, 0, 0.6);
}

*,
*::before,
*::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

.app-container {
  background-color: var(--bg-base);
  color: var(--text-main);
  font-family: var(--font-ui);
  height: 100vh;
  display: flex;
  flex-direction: column;
  font-size: 14px;
  -webkit-font-smoothing: antialiased;
  overflow: hidden;
}

.mono-value {
  font-family: var(--font-mono);
  font-size: 0.85em;
  font-weight: 600;
  letter-spacing: -0.5px;
}
.theme-warm {
  color: var(--accent-warm);
}
.theme-olive {
  color: var(--success);
}
.theme-deep {
  color: var(--accent-mustard);
}
.theme-bg-warm {
  background: var(--accent-warm);
  color: #0f0a04;
}
.theme-bg-olive {
  background: var(--success);
  color: #0a140a;
}
.theme-bg-deep {
  background: var(--accent-deep);
  color: var(--text-main);
}
.text-success {
  color: var(--success);
}
.text-error {
  color: var(--error);
}
.text-mustard {
  color: var(--accent-mustard);
}
.text-parchment {
  color: var(--text-main);
}
.text-muted {
  color: var(--text-muted);
}

/* topbar */
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 24px;
  height: 52px;
  background: var(--bg-panel);
  border-bottom: 1px solid var(--border-light);
  flex-shrink: 0;
  z-index: 100;
}
.topbar-left {
  display: flex;
  align-items: center;
  gap: 14px;
}
.jester-badge {
  font-family: var(--font-serif);
  font-style: italic;
  font-size: 12px;
  font-weight: 700;
  color: #0d0b04;
  background: var(--accent-mustard);
  padding: 3px 10px;
  border-radius: 4px;
  letter-spacing: 0.5px;
}
.topbar-title {
  font-family: var(--font-serif);
  font-size: 16px;
  font-weight: 500;
  color: var(--text-dim);
  letter-spacing: 0.3px;
}
.topbar-right {
  display: flex;
  align-items: center;
  gap: 20px;
}
.status-indicator {
  display: flex;
  align-items: center;
  gap: 8px;
}
.status-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  transition: var(--transition);
}
.is-idle .status-dot {
  background: var(--text-muted);
}
.is-busy .status-dot {
  background: var(--accent-mustard);
  box-shadow: 0 0 8px rgba(224, 161, 27, 0.7);
  animation: blink 1s step-end infinite;
}
@keyframes blink {
  50% {
    opacity: 0.3;
  }
}
.status-text {
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
  color: var(--text-muted);
  letter-spacing: 1.5px;
}
.clock {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-muted);
}

/* layout */
.layout-grid {
  display: grid;
  grid-template-columns: 268px 1fr 300px;
  flex: 1;
  overflow: hidden;
  min-height: 0;
}
.panel {
  background: var(--bg-panel);
  overflow-y: auto;
  overflow-x: hidden;
  padding: 20px;
  border-right: 1px solid var(--border-light);
}
.panel:last-child {
  border-right: none;
  border-left: 1px solid var(--border-light);
}

.panel::-webkit-scrollbar {
  width: 4px;
}
.panel::-webkit-scrollbar-thumb {
  background: var(--border-focus);
  border-radius: 2px;
}

/* sidebar */
.control-group {
  margin-bottom: 28px;
}
.section-title {
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 2px;
  margin-bottom: 14px;
  border-bottom: 1px solid var(--border-light);
  padding-bottom: 8px;
}

/* slider */
.slider-container {
  display: flex;
  align-items: center;
  gap: 14px;
}
.slider-container .mono-value {
  min-width: 56px;
  color: var(--accent-mustard);
  font-size: 13px;
}
.latency-label {
  font-size: 9px;
  color: var(--accent-warm);
  margin-left: 4px;
  letter-spacing: 0.5px;
}
.apple-slider {
  flex: 1;
  height: 4px;
  border-radius: 2px;
  background: var(--bg-surface);
  border: 1px solid var(--border-light);
  outline: none;
  appearance: none;
}
.apple-slider::-webkit-slider-thumb {
  appearance: none;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--accent-mustard);
  border: 2px solid var(--bg-base);
  box-shadow: 0 0 8px rgba(224, 161, 27, 0.5);
  cursor: pointer;
  transition: transform 0.15s;
}
.apple-slider::-webkit-slider-thumb:hover {
  transform: scale(1.25);
}

/* button */
.model-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.apple-btn {
  background: var(--bg-surface);
  border: 1px solid var(--border-light);
  border-radius: var(--radius-sm);
  color: var(--text-main);
  cursor: pointer;
  transition: var(--transition);
  display: flex;
  align-items: center;
}
.model-btn {
  padding: 10px 12px;
  gap: 12px;
  width: 100%;
  text-align: left;
}
.model-btn:hover:not(.disabled) {
  border-color: var(--border-focus);
  background: var(--bg-raised);
}
.model-btn.active {
  border-color: var(--accent-muted);
  background: var(--bg-raised);
  box-shadow: inset 0 0 0 1px var(--accent-muted);
}
.model-btn.disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.model-icon-wrap {
  width: 30px;
  height: 30px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-mono);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.5px;
  flex-shrink: 0;
}
.model-icon-wrap.theme-warm {
  background: rgba(217, 122, 30, 0.18);
  color: var(--accent-warm);
  border: 1px solid rgba(217, 122, 30, 0.3);
}
.model-icon-wrap.theme-olive {
  background: rgba(111, 143, 90, 0.18);
  color: var(--success);
  border: 1px solid rgba(111, 143, 90, 0.3);
}
.model-icon-wrap.theme-deep {
  background: rgba(224, 161, 27, 0.12);
  color: var(--accent-mustard);
  border: 1px solid rgba(224, 161, 27, 0.25);
}
.model-name {
  font-weight: 500;
  font-size: 13px;
  color: var(--text-dim);
}

/* stats */
.stats-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.stat-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  background: var(--bg-surface);
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-light);
}
.stat-card:hover {
  border-color: var(--border-focus);
}
.stat-label {
  font-size: 12px;
  color: var(--text-muted);
}
.stat-value {
  font-size: 15px;
  font-family: var(--font-mono);
  font-weight: 600;
}

/* tengah */
.canvas-area {
  display: flex;
  flex-direction: column;
  padding: 0;
  background: var(--bg-base);
  border-right: none;
  overflow: hidden;
}
.canvas-stage {
  flex: 1;
  position: relative;
  overflow: hidden;
  border-bottom: 1px solid var(--border-light);
  min-height: 0;
}

.main-bg {
  position: absolute;
  inset: 0;
  pointer-events: none;
  opacity: 0.6;
}

.main-bg::after {
  content: "";
  position: absolute;
  inset: 0;
  background: radial-gradient(
    ellipse 60% 50% at 50% 50%,
    rgba(156, 79, 18, 0.07) 0%,
    transparent 70%
  );
}

/* node */
.stage-node {
  position: absolute;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  z-index: 2;
  transition: var(--transition);
}
.node-graphic {
  position: relative;
  width: 58px;
  height: 58px;
}
.node-icon {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-surface);
  border: 1px solid var(--border-focus);
  border-radius: 10px;
  font-size: 22px;
  box-shadow:
    var(--shadow-sm),
    inset 0 1px 0 rgba(255, 255, 255, 0.04);
  z-index: 2;
  transition: var(--transition);
}
.node-pulse-ring {
  position: absolute;
  inset: -5px;
  border-radius: 14px;
  border: 1.5px solid transparent;
  z-index: 1;
  transition: var(--transition);
}

.stage-node.is-active .node-icon {
  border-color: var(--accent-warm);
  box-shadow:
    0 0 20px rgba(217, 122, 30, 0.35),
    var(--shadow-sm),
    inset 0 1px 0 rgba(255, 255, 255, 0.06);
  background: #1e1609;
}
.stage-node.client.is-active .node-pulse-ring {
  border-color: var(--accent-warm);
  animation: pulseOut 1.2s infinite;
}
.stage-node.server.is-active .node-pulse-ring {
  border-color: var(--success);
  animation: pulseOut 1.2s infinite;
}
.stage-node.broker.is-active .node-pulse-ring {
  border-color: var(--success);
  animation: pulseOut 1.2s infinite;
}
.stage-node.queue.is-active .node-pulse-ring {
  border-color: var(--accent-mustard);
  animation: pulseOut 1.2s infinite;
}

@keyframes pulseOut {
  0% {
    transform: scale(1);
    opacity: 0.7;
  }
  100% {
    transform: scale(1.6);
    opacity: 0;
  }
}

.node-info {
  text-align: center;
}
.node-label {
  font-family: var(--font-serif);
  font-size: 13px;
  font-weight: 700;
  color: var(--text-main);
  letter-spacing: 0.2px;
}
.node-sub {
  font-size: 10px;
  color: var(--text-muted);
  font-family: var(--font-mono);
  letter-spacing: 0.5px;
}

/* SVG Lines */
.connection-layer {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 1;
}
.conn-line {
  transition:
    opacity 0.3s,
    stroke-width 0.3s;
}

/* Packet dot */
.data-packet {
  position: absolute;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  transform: translate(-50%, -50%);
  pointer-events: none;
  z-index: 10;
}
.data-packet.theme-bg-warm {
  background: var(--accent-warm);
  box-shadow: 0 0 10px var(--accent-warm);
}
.data-packet.theme-bg-olive {
  background: var(--success);
  box-shadow: 0 0 10px var(--success);
}
.data-packet.theme-bg-deep {
  background: var(--accent-mustard);
  box-shadow: 0 0 10px var(--accent-mustard);
}

/* panel kanan*/
.sidebar-right {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.log-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
  flex-shrink: 0;
}
.icon-btn {
  background: none;
  border: 1px solid var(--border-light);
  color: var(--text-muted);
  cursor: pointer;
  padding: 5px 7px;
  border-radius: var(--radius-sm);
  transition: var(--transition);
}
.icon-btn:hover {
  background: var(--bg-surface);
  color: var(--text-main);
  border-color: var(--border-focus);
}

.log-container {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  padding-right: 2px;
}
.log-container::-webkit-scrollbar {
  width: 4px;
}
.log-container::-webkit-scrollbar-thumb {
  background: var(--border-light);
  border-radius: 2px;
}

.log-empty {
  text-align: center;
  padding: 40px 16px;
  color: var(--text-muted);
}
.empty-icon {
  font-size: 28px;
  margin-bottom: 10px;
  opacity: 0.4;
}
.log-empty p {
  font-size: 12px;
  font-style: italic;
}

.log-list {
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.log-card {
  background: var(--bg-surface);
  border-radius: var(--radius-sm);
  padding: 10px 12px;
  border-left: 2px solid transparent;
  border: 1px solid var(--border-light);
  border-left-width: 3px;
  transition: border-color 0.2s;
}
.log-card:hover {
  border-color: var(--border-focus);
  border-left-width: 3px;
}
.log-card.border-warm {
  border-left-color: var(--accent-warm);
}
.log-card.border-olive {
  border-left-color: var(--success);
}
.log-card.border-deep {
  border-left-color: var(--accent-mustard);
}
.log-card.is-error {
  background: rgba(163, 74, 44, 0.08);
  border-left-color: var(--error);
  border-color: rgba(163, 74, 44, 0.2);
}

.log-card-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 7px;
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--text-muted);
  letter-spacing: 0.3px;
}
.log-dur {
  color: var(--accent-muted);
}
.log-card-body {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.log-badge {
  font-family: var(--font-mono);
  font-size: 8px;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 3px;
  letter-spacing: 1px;
  flex-shrink: 0;
  margin-top: 1px;
}
.log-msg {
  font-size: 11px;
  color: var(--text-dim);
  line-height: 1.45;
  word-break: break-word;
}

.log-anim-enter-active {
  transition: all 0.3s ease;
}
.log-anim-enter-from {
  opacity: 0;
  transform: translateY(-8px);
}
.log-anim-leave-to {
  opacity: 0;
  transform: translateX(16px);
}
</style>
