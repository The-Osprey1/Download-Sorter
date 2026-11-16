const NATIVE_HOST = "com.devynfidel.download_sorter";
const MAX_RECENT = 8;

async function isRoutingEnabled() {
  try {
    const data = await chrome.storage.local.get({ enabled: true });
    return data.enabled !== false;
  } catch {
    return true;
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
    const enabled = await isRoutingEnabled();
    if (!enabled) {
      finish(null);
      return;
    }

    const baseName = cleanBaseName(item.filename);
    if (!baseName) {
      finish(null);
      return;
    }

    const resp = await sendNative({
      action: "route",
      filename: baseName,
      finalUrl: item.finalUrl || item.url || "",
      url: item.url || "",
      referrer: item.referrer || "",
      mime: item.mime || "",
      fileSize: item.fileSize || 0,
      incognito: Boolean(item.incognito)
    });

    if (resp && resp.ok && resp.decision === "suggest" && resp.relative_dest) {
      const targetRel = String(resp.relative_dest).replace(/\\/g, "/").replace(/^\/+/, "");
      if (targetRel && !targetRel.includes("..")) {
        await savePendingRoute(item.id, {
          filename: baseName,
          relative_dest: targetRel,
          folder_display: resp.folder_display || targetRel.split("/").slice(0, -1).join("/"),
          matched_rule: resp.matched_rule || "",
          match_type: resp.match_type || "chrome",
          reason: resp.reason || "Routed directly by Chrome Extension",
          source_url: item.finalUrl || item.url || item.referrer || ""
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
        filename: finalName,
        folder: meta.folder_display,
        at: Date.now()
      });
    });
  })();
});
