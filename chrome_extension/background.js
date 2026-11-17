const NATIVE_HOST = "com.devynfidel.download_sorter";
const MAX_RECENT = 8;
const MENU_PARENT_ID = "ds_save_sort_parent";
const MENU_FOLDER_PREFIX = "ds_folder::";
const DEFAULT_MENU_FOLDERS = ["Documents", "Images", "3D Models", "Archives", "Other"];

async function isRoutingEnabled() {
  try {
    const data = await chrome.storage.local.get({ enabled: true });
    return data.enabled !== false;
  } catch {
    return true;
  }
}

async function getActiveTabContext() {
  try {
    const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    const tab = tabs && tabs[0] ? tabs[0] : null;
    if (!tab) return { tabUrl: "", tabTitle: "" };
    return {
      tabUrl: typeof tab.url === "string" ? tab.url : "",
      tabTitle: typeof tab.title === "string" ? tab.title : ""
    };
  } catch {
    return { tabUrl: "", tabTitle: "" };
  }
}

async function saveOverrideRoute(downloadId, folder) {
  try {
    await chrome.storage.session.set({ [`override_${downloadId}`]: folder });
  } catch {
    // Session storage best-effort
  }
}

async function takeOverrideRoute(downloadId) {
  const key = `override_${downloadId}`;
  try {
    const res = await chrome.storage.session.get(key);
    const folder = res[key] || null;
    if (folder) {
      await chrome.storage.session.remove(key);
    }
    return folder;
  } catch {
    return null;
  }
}

async function savePendingRoute(downloadId, meta) {
  try {
    await chrome.storage.session.set({ [`pending_${downloadId}`]: meta });
  } catch {
    // Session storage best-effort
  }
}

async function takePendingRoute(downloadId) {
  const key = `pending_${downloadId}`;
  try {
    const res = await chrome.storage.session.get(key);
    const meta = res[key] || null;
    if (meta) {
      await chrome.storage.session.remove(key);
    }
    return meta;
  } catch {
    return null;
  }
}

async function appendRecentRoute(entry) {
  try {
    const data = await chrome.storage.local.get({ recentRoutes: [] });
    const list = Array.isArray(data.recentRoutes) ? data.recentRoutes : [];
    list.unshift(entry);
    await chrome.storage.local.set({ recentRoutes: list.slice(0, MAX_RECENT) });
  } catch {
    // Ignore storage errors
  }
}

function flashBadge(text, colorHex, clearAfterMs = 3500) {
  if (!chrome.action || !chrome.action.setBadgeText) return;
  try {
    chrome.action.setBadgeBackgroundColor({ color: colorHex });
    chrome.action.setBadgeText({ text });
    if (clearAfterMs > 0) {
      setTimeout(() => {
        chrome.action.setBadgeText({ text: "" });
      }, clearAfterMs);
    }
  } catch {
    // Ignore badge errors
  }
}

function cleanBaseName(rawPath) {
  if (!rawPath) return "";
  const normalized = String(rawPath).replace(/\\/g, "/");
  const idx = normalized.lastIndexOf("/");
  return idx >= 0 ? normalized.slice(idx + 1) : normalized;
}

function sendNative(message) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendNativeMessage(NATIVE_HOST, message, (response) => {
        if (chrome.runtime.lastError || !response) {
          resolve({ ok: false, error: chrome.runtime.lastError?.message || "No response" });
          return;
        }
        resolve(response);
      });
    } catch (err) {
      resolve({ ok: false, error: String(err) });
    }
  });
}

chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
  let settled = false;
  const finish = (suggestion) => {
    if (settled) return;
    settled = true;
    clearTimeout(watchdog);
    try {
      if (suggestion && suggestion.filename) {
        suggest(suggestion);
      } else {
        suggest();
      }
    } catch {
      try {
        suggest();
      } catch {
        // Already handled by Chrome
      }
    }
  };

  // Safety timeout: never stall a Chrome download if native messaging is slow or absent.
  const watchdog = setTimeout(() => finish(null), 1500);

  (async () => {
    const baseName = cleanBaseName(item.filename);
    if (!baseName) {
      finish(null);
      return;
    }

    if (item.byExtensionId === chrome.runtime.id) {
      const overrideFolder = await takeOverrideRoute(item.id);
      if (overrideFolder) {
        const cleanFolder = String(overrideFolder).replace(/\\/g, "/").replace(/^\/+|\/+$/g, "");
        if (cleanFolder && !cleanFolder.includes("..")) {
          const targetRel = `${cleanFolder}/${baseName}`;
          await savePendingRoute(item.id, {
            filename: baseName,
            relative_dest: targetRel,
            folder_display: cleanFolder,
            matched_rule: "Context Menu",
            match_type: "context_menu",
            reason: `Saved via Chrome context menu to '${cleanFolder}'`,
            source_url: item.finalUrl || item.url || item.referrer || "",
            source_host: ""
          });
          finish({ filename: targetRel, conflictAction: "uniquify" });
          return;
        }
      }
    }

    const enabled = await isRoutingEnabled();
    if (!enabled) {
      finish(null);
      return;
    }

    const tabCtx = await getActiveTabContext();
    const resp = await sendNative({
      action: "route",
      filename: baseName,
      finalUrl: item.finalUrl || item.url || "",
      url: item.url || "",
      referrer: item.referrer || "",
      tabUrl: tabCtx.tabUrl,
      tabTitle: tabCtx.tabTitle,
      mime: item.mime || "",
      fileSize: item.fileSize || 0,
      incognito: Boolean(item.incognito)
    });

    if (resp && resp.ok && resp.decision === "suggest" && resp.relative_dest) {
      const targetRel = String(resp.relative_dest).replace(/\\/g, "/").replace(/^\/+/, "");
      if (targetRel && !targetRel.includes("..")) {
        const finalFileName = resp.repaired_filename || baseName;
        // Best-effort duplicate guard check (non-blocking if fast)
        const dupResp = await sendNative({
          action: "check_duplicate",
          filename: finalFileName,
          finalUrl: item.finalUrl || item.url || "",
          fileSize: item.fileSize || 0
        });
        const isDuplicate = Boolean(dupResp && dupResp.ok && dupResp.duplicate);

        await savePendingRoute(item.id, {
          filename: finalFileName,
          relative_dest: targetRel,
          folder_display: resp.folder_display || targetRel.split("/").slice(0, -1).join("/"),
          matched_rule: resp.matched_rule || "",
          match_type: resp.match_type || "chrome",
          reason: isDuplicate ? `${resp.reason} (${dupResp.reason})` : (resp.reason || "Routed directly by Chrome Extension"),
          source_url: item.finalUrl || item.url || item.referrer || tabCtx.tabUrl || "",
          source_host: resp.source_host || "",
          is_duplicate: isDuplicate
        });
        finish({
          filename: targetRel,
          conflictAction: resp.conflict_action || "uniquify"
        });
        return;
      }
    }

    finish(null);
  })();

  return true;
});

chrome.downloads.onChanged.addListener((delta) => {
  if (!delta || !delta.state || delta.state.current !== "complete") {
    return;
  }
  const downloadId = delta.id;
  (async () => {
    const meta = await takePendingRoute(downloadId);
    if (!meta) return;

    chrome.downloads.search({ id: downloadId }, async (items) => {
      const dl = items && items[0] ? items[0] : null;
      const fullPath = dl ? dl.filename : "";
      const finalName = fullPath ? cleanBaseName(fullPath) : meta.filename;
      await sendNative({
        action: "completed",
        filename: finalName,
        full_path: fullPath,
        relative_dest: meta.relative_dest,
        matched_rule: meta.matched_rule,
        match_type: meta.match_type,
        reason: meta.reason,
        source_url: meta.source_url,
        file_size: dl && typeof dl.fileSize === "number" ? dl.fileSize : 0
      });
      await appendRecentRoute({
        downloadId,
        filename: finalName,
        folder: meta.folder_display,
        relative_dest: meta.relative_dest,
        full_path: fullPath,
        source_url: meta.source_url || "",
        source_host: meta.source_host || "",
        is_duplicate: Boolean(meta.is_duplicate),
        at: Date.now()
      });
      if (meta.is_duplicate) {
        flashBadge("DUP", "#FF9F0A", 4500);
      } else {
        flashBadge("✓", "#30D158", 3000);
      }
    });
  })();
});

async function rebuildContextMenus() {
  if (!chrome.contextMenus) return;
  let folders = DEFAULT_MENU_FOLDERS;
  try {
    const resp = await sendNative({ action: "list_folders" });
    if (resp && resp.ok && Array.isArray(resp.folders) && resp.folders.length > 0) {
      folders = resp.folders.slice(0, 12);
    }
  } catch {
    // Keep defaults when host is unreachable
  }

  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: MENU_PARENT_ID,
      title: "Download Sorter: Save & Sort to",
      contexts: ["link", "image", "video", "audio"]
    });
    for (const folder of folders) {
      chrome.contextMenus.create({
        id: `${MENU_FOLDER_PREFIX}${folder}`,
        parentId: MENU_PARENT_ID,
        title: folder,
        contexts: ["link", "image", "video", "audio"]
      });
    }
  });
}

chrome.runtime.onInstalled.addListener(() => {
  rebuildContextMenus();
});

chrome.runtime.onStartup.addListener(() => {
  rebuildContextMenus();
});

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "refresh_menus") {
    rebuildContextMenus().then(() => sendResponse({ ok: true }));
    return true;
  }
  return false;
});

if (chrome.contextMenus) {
  chrome.contextMenus.onClicked.addListener((info) => {
    const menuId = String(info.menuItemId || "");
    if (!menuId.startsWith(MENU_FOLDER_PREFIX)) return;
    const folder = menuId.slice(MENU_FOLDER_PREFIX.length);
    const targetUrl = info.linkUrl || info.srcUrl || "";
    if (!targetUrl || !folder) return;

    chrome.downloads.download({ url: targetUrl }, (downloadId) => {
      if (chrome.runtime.lastError || typeof downloadId !== "number") return;
      saveOverrideRoute(downloadId, folder);
    });
  });
}
