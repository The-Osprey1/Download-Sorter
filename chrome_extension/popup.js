const NATIVE_HOST = "com.devynfidel.download_sorter";

const pill = document.getElementById("status-pill");
const detail = document.getElementById("status-detail");
const btnToggle = document.getElementById("btn-toggle");
const btnOpen = document.getElementById("btn-open");
const btnClear = document.getElementById("btn-clear");
const recentList = document.getElementById("recent-list");
const recentEmpty = document.getElementById("recent-empty");

function sendNative(msg) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendNativeMessage(NATIVE_HOST, msg, (resp) => {
        if (chrome.runtime.lastError || !resp) {
          resolve({ ok: false, error: chrome.runtime.lastError?.message || "Host unreachable" });
          return;
        }
        resolve(resp);
      });
    } catch (err) {
      resolve({ ok: false, error: String(err) });
    }
  });
}

async function refreshStatus() {
  const { enabled = true } = await chrome.storage.local.get({ enabled: true });
  btnToggle.textContent = enabled ? "Chrome Routing: ON" : "Chrome Routing: OFF";

  const resp = await sendNative({ action: "status" });
  if (!resp || !resp.ok) {
    pill.className = "pill err";
    pill.textContent = "Disconnected";
    detail.textContent =
      "Open Download Sorter Settings and click 'Install / Repair Chrome Host' to connect.";
    return;
  }

  if (!enabled || resp.paused) {
    pill.className = "pill warn";
    pill.textContent = resp.paused ? "App Paused" : "Routing Off";
    detail.textContent = resp.paused
      ? `v${resp.version} connected, but Auto-Sort is paused in the desktop app.`
      : `v${resp.version} connected — Chrome direct routing is turned off here.`;
    return;
  }

  pill.className = "pill ok";
  pill.textContent = "Connected";
  const holdText = resp.hold_3mf_enabled ? ` • .3MF hold (${resp.hold_3mf_days}d)` : "";
  detail.textContent = `v${resp.version} active • ${resp.rules_count} custom rule(s)${holdText}`;
}

async function refreshRecent() {
  const { recentRoutes = [] } = await chrome.storage.local.get({ recentRoutes: [] });
  recentList.innerHTML = "";
  if (!Array.isArray(recentRoutes) || recentRoutes.length === 0) {
    recentEmpty.style.display = "block";
    btnClear.style.display = "none";
    return;
  }
  recentEmpty.style.display = "none";
  btnClear.style.display = "inline";
  for (const item of recentRoutes.slice(0, 6)) {
    const li = document.createElement("li");
    li.className = "item";

    const name = document.createElement("div");
    name.className = "item-name";
    name.textContent = item.filename || "";
    name.title = item.filename || "";

    const dest = document.createElement("div");
    dest.className = "item-dest";
    dest.textContent = `→ ${item.folder || "Downloads"}`;

    li.appendChild(name);
    li.appendChild(dest);
    recentList.appendChild(li);
  }
}

btnToggle.addEventListener("click", async () => {
  const { enabled = true } = await chrome.storage.local.get({ enabled: true });
  await chrome.storage.local.set({ enabled: !enabled });
  await refreshStatus();
});

btnOpen.addEventListener("click", async () => {
  await sendNative({ action: "open_app" });
  window.close();
});

btnClear.addEventListener("click", async () => {
  await chrome.storage.local.set({ recentRoutes: [] });
  await refreshRecent();
});

refreshStatus();
refreshRecent();
