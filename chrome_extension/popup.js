const NATIVE_HOST = "com.devynfidel.download_sorter";

const appRoot = document.getElementById("appRoot");
const statusText = document.getElementById("status-text");
const detail = document.getElementById("status-detail");
const routingToggle = document.getElementById("routing-toggle");
const btnOpen = document.getElementById("btn-open");
const btnPause = document.getElementById("btn-pause");
const pauseLabel = document.getElementById("pause-label");
const btnUndo = document.getElementById("btn-undo");
const undoLabel = document.getElementById("undo-label");
const btnClear = document.getElementById("btn-clear");
const btnTestSite = document.getElementById("btn-test-site");
const siteTestHost = document.getElementById("site-test-host");
const siteTestResult = document.getElementById("site-test-result");
const recentList = document.getElementById("recent-list");
const recentCount = document.getElementById("recent-count");
const recentEmpty = document.getElementById("recent-empty");

let cachedFolders = [];

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

function getExt(filename) {
  const idx = String(filename || "").lastIndexOf(".");
  if (idx <= 0 || idx === filename.length - 1) return "file";
  return filename.slice(idx + 1, idx + 6).toLowerCase();
}

async function refreshStatus() {
  const { enabled = true } = await chrome.storage.local.get({ enabled: true });
  routingToggle.checked = enabled;

  const resp = await sendNative({ action: "status" });
  if (!resp || !resp.ok) {
    appRoot.dataset.routing = "error";
    statusText.textContent = "Disconnected";
    btnPause.disabled = true;
    btnUndo.disabled = true;
    detail.textContent = "Open Settings in Download Sorter and click 'Install / Repair Chrome Host'.";
    return;
  }

  btnPause.disabled = false;
  btnUndo.disabled = false;
  btnPause.setAttribute("aria-pressed", String(Boolean(resp.paused)));
  pauseLabel.textContent = resp.paused ? "Resume" : "Pause App";

  if (!enabled || resp.paused) {
    appRoot.dataset.routing = "paused";
    statusText.textContent = resp.paused ? "App Paused" : "Routing Off";
    detail.textContent = resp.paused
      ? `v${resp.version} connected, Auto-Sort paused`
      : `v${resp.version} connected — Chrome routing off`;
    return;
  }

  appRoot.dataset.routing = "active";
  statusText.textContent = "Active";
  const holdText = resp.hold_3mf_enabled ? ` • .3MF ${resp.hold_3mf_days}d` : "";
  detail.textContent = `v${resp.version} • ${resp.rules_count} rule(s)${holdText}`;
}

async function refreshRecent() {
  const { recentRoutes = [] } = await chrome.storage.local.get({ recentRoutes: [] });
  recentList.innerHTML = "";
  const list = Array.isArray(recentRoutes) ? recentRoutes.slice(0, 6) : [];
  recentCount.textContent = String(list.length);

  if (list.length === 0) {
    recentList.style.display = "none";
    recentEmpty.style.display = "block";
    btnClear.style.display = "none";
    return;
  }

  recentList.style.display = "flex";
  recentEmpty.style.display = "none";
  btnClear.style.display = "inline";

  list.forEach((item, index) => {
    const li = document.createElement("li");
    li.className = "routed-item";
    li.setAttribute("role", "listitem");
    li.style.setProperty("--i", String(index));

    const top = document.createElement("div");
    top.className = "item-top";

    const fileWrap = document.createElement("div");
    fileWrap.className = "item-file";
    fileWrap.title = item.filename || "";

    const extBadge = document.createElement("span");
    extBadge.className = "ext-badge";
    extBadge.textContent = getExt(item.filename);

    const name = document.createElement("span");
    name.className = "filename";
    name.textContent = item.filename || "";

    fileWrap.appendChild(extBadge);
    fileWrap.appendChild(name);

    const dest = document.createElement("span");
    dest.className = "dest-badge";
    dest.textContent = item.folder || "Downloads";
    dest.title = `Routed to ${item.folder || "Downloads"}`;

    top.appendChild(fileWrap);
    if (item.is_duplicate) {
      const dupTag = document.createElement("span");
      dupTag.className = "dup-tag";
      dupTag.textContent = "DUP";
      dupTag.title = "Previously downloaded";
      top.appendChild(dupTag);
    }
    top.appendChild(dest);
    li.appendChild(top);

    if (typeof item.downloadId === "number" || (item.source_host && item.folder) || item.full_path || item.relative_dest) {
      const actions = document.createElement("div");
      actions.className = "item-actions";

      if (typeof item.downloadId === "number") {
        const btnReveal = document.createElement("button");
        btnReveal.type = "button";
        btnReveal.className = "btn-item";
        btnReveal.textContent = "Reveal";
        btnReveal.addEventListener("click", () => {
          chrome.downloads.show(item.downloadId);
        });
        actions.appendChild(btnReveal);
      }

      if ((item.full_path || item.relative_dest) && cachedFolders.length > 0) {
        const moveSelect = document.createElement("select");
        moveSelect.className = "move-select";
        moveSelect.title = "Re-route file to another folder";
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Move to...";
        moveSelect.appendChild(placeholder);
        for (const f of cachedFolders) {
          if (f === item.folder) continue;
          const opt = document.createElement("option");
          opt.value = f;
          opt.textContent = f;
          moveSelect.appendChild(opt);
        }
        moveSelect.addEventListener("change", async () => {
          const target = moveSelect.value;
          if (!target) return;
          moveSelect.disabled = true;
          const res = await sendNative({
            action: "reroute_file",
            full_path: item.full_path || "",
            relative_dest: item.relative_dest || "",
            new_folder: target,
            source_url: item.source_url || ""
          });
          if (res && res.ok) {
            item.folder = res.folder_display;
            item.relative_dest = res.relative_dest;
            item.full_path = res.dest_path;
            const { recentRoutes = [] } = await chrome.storage.local.get({ recentRoutes: [] });
            const updated = recentRoutes.map((r) => (r.at === item.at ? item : r));
            await chrome.storage.local.set({ recentRoutes: updated });
            await refreshRecent();
          } else {
            moveSelect.value = "";
            moveSelect.disabled = false;
          }
        });
        actions.appendChild(moveSelect);
      }

      if (item.source_host && item.folder) {
        const btnPin = document.createElement("button");
        btnPin.type = "button";
        btnPin.className = "btn-item btn-always";
        btnPin.title = `Always route ${item.source_host} to ${item.folder}`;

        const pinLabel = document.createElement("span");
        pinLabel.className = "btn-always-label";
        pinLabel.textContent = `Always ${item.source_host}`;
        btnPin.appendChild(pinLabel);

        btnPin.addEventListener("click", async () => {
          btnPin.disabled = true;
          pinLabel.textContent = "Saving...";
          const res = await sendNative({
            action: "create_rule",
            domain: item.source_host,
            destination: item.folder
          });
          if (res && res.ok) {
            btnPin.classList.add("is-pinned");
            pinLabel.textContent = `✓ Always ${item.source_host}`;
            chrome.runtime.sendMessage({ type: "refresh_menus" });
            await refreshStatus();
          } else {
            pinLabel.textContent = "Failed";
            btnPin.disabled = false;
          }
        });
        actions.appendChild(btnPin);
      }

      li.appendChild(actions);
    }

    recentList.appendChild(li);
  });
}

routingToggle.addEventListener("change", async () => {
  await chrome.storage.local.set({ enabled: Boolean(routingToggle.checked) });
  await refreshStatus();
});

btnOpen.addEventListener("click", async () => {
  await sendNative({ action: "open_app" });
  window.close();
});

btnPause.addEventListener("click", async () => {
  btnPause.disabled = true;
  await sendNative({ action: "toggle_pause" });
  await refreshStatus();
});

btnUndo.addEventListener("click", async () => {
  btnUndo.disabled = true;
  undoLabel.textContent = "Undoing...";
  const res = await sendNative({ action: "undo_last" });
  undoLabel.textContent = res && res.ok ? "Undone ✓" : "Undo Last";
  setTimeout(() => {
    undoLabel.textContent = "Undo Last";
    btnUndo.disabled = false;
  }, 1200);
});

btnClear.addEventListener("click", async () => {
  await chrome.storage.local.set({ recentRoutes: [] });
  await refreshRecent();
});

async function runSiteTest() {
  if (!chrome.tabs || !chrome.tabs.query) return;
  try {
    const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    const tab = tabs && tabs[0] ? tabs[0] : null;
    if (!tab || !tab.url) {
      siteTestHost.textContent = "Current Tab";
      siteTestResult.textContent = "No active webpage URL";
      return;
    }
    const resp = await sendNative({
      action: "test_tab",
      tabUrl: tab.url,
      tabTitle: tab.title || ""
    });
    if (resp && resp.ok) {
      siteTestHost.textContent = resp.host || "Current Tab";
      siteTestResult.textContent = `${resp.matched_rule} → ${resp.folder}`;
    } else {
      siteTestResult.textContent = "Host disconnected";
    }
  } catch {
    siteTestResult.textContent = "Unable to inspect tab";
  }
}

btnTestSite.addEventListener("click", runSiteTest);

async function initPopup() {
  const folderResp = await sendNative({ action: "list_folders" });
  if (folderResp && folderResp.ok && Array.isArray(folderResp.folders)) {
    cachedFolders = folderResp.folders;
  }
  await refreshStatus();
  await refreshRecent();
  await runSiteTest();
}

initPopup();
