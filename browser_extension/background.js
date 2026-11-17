// Мяукало — звук вкладки. Кнопка расширения на вкладке: начать / остановить передачу её звука в программу.

async function ensureOffscreen() {
  const ctx = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  if (!ctx.length) {
    await chrome.offscreen.createDocument({
      url: "offscreen.html",
      reasons: ["USER_MEDIA"],
      justification: "Передать звук выбранной вкладки в программу Мяукало на этом компьютере",
    });
  }
}

async function getActive() {
  const st = await chrome.storage.session.get("active");
  return st.active || {};
}

async function setActive(active) {
  await chrome.storage.session.set({ active });
}

function badge(tabId, text, color, title) {
  chrome.action.setBadgeText({ tabId, text });
  if (color) chrome.action.setBadgeBackgroundColor({ tabId, color });
  chrome.action.setTitle({ tabId, title: title || "Мяукало: отправить звук этой вкладки" });
}

chrome.action.onClicked.addListener(async (tab) => {
  await ensureOffscreen();
  const active = await getActive();
  if (active[tab.id]) {
    chrome.runtime.sendMessage({ target: "offscreen", type: "stop", tabId: tab.id });
    delete active[tab.id];
    await setActive(active);
    badge(tab.id, "", null);
    return;
  }
  try {
    const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
    chrome.runtime.sendMessage({
      target: "offscreen", type: "start", tabId: tab.id, streamId,
      title: tab.title || "", url: tab.url || "",
    });
    active[tab.id] = true;
    await setActive(active);
    badge(tab.id, "…", "#b5803a", "Мяукало: подключаюсь к программе…");
  } catch (e) {
    badge(tab.id, "ERR", "#ff4b2b", "Мяукало: " + e.message);
  }
});

chrome.runtime.onMessage.addListener(async (msg) => {
  if (msg.target !== "background") return;
  if (msg.type === "status") {
    if (msg.state === "live") badge(msg.tabId, "ON", "#ff9a1f", "Мяукало: звук вкладки передаётся. Нажмите, чтобы остановить");
    else if (msg.state === "waiting") badge(msg.tabId, "…", "#b5803a", "Мяукало не запущено — жду программу…");
    else if (msg.state === "ended") {
      badge(msg.tabId, "", null);
      const active = await getActive();
      delete active[msg.tabId];
      await setActive(active);
    } else if (msg.state === "error") badge(msg.tabId, "ERR", "#ff4b2b", "Мяукало: " + (msg.error || "ошибка"));
  }
});

chrome.tabs.onUpdated.addListener(async (tabId, info, tab) => {
  if (!info.title && !info.url) return;
  const active = await getActive();
  if (active[tabId]) {
    chrome.runtime.sendMessage({ target: "offscreen", type: "meta", tabId, title: tab.title || "", url: tab.url || "" });
  }
});

chrome.tabs.onRemoved.addListener(async (tabId) => {
  const active = await getActive();
  if (active[tabId]) {
    chrome.runtime.sendMessage({ target: "offscreen", type: "stop", tabId });
    delete active[tabId];
    await setActive(active);
  }
});
