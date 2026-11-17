// Захватывает звук вкладки, оставляет его слышимым и шлёт PCM (float32, стерео, 48 кГц) в Мяукало по WebSocket.
const PORT = 47800;
const sessions = {};   // tabId -> { stream, ctx, node, ws, meta, closed }

function report(tabId, state, error) {
  chrome.runtime.sendMessage({ target: "background", type: "status", tabId, state, error });
}

function connect(s, tabId) {
  if (s.closed) return;
  const ws = new WebSocket(`ws://127.0.0.1:${PORT}/tab`);
  ws.binaryType = "arraybuffer";
  s.ws = ws;
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "hello", tab: tabId, rate: s.ctx.sampleRate, channels: 2, ...s.meta }));
    report(tabId, "live");
  };
  ws.onclose = () => {
    if (s.closed) return;
    report(tabId, "waiting");
    setTimeout(() => connect(s, tabId), 2000);     // программа закрыта — ждём, пока её запустят
  };
  ws.onerror = () => {};
}

async function start(msg) {
  const tabId = msg.tabId;
  if (sessions[tabId]) stop(tabId);
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: msg.streamId } },
      video: false,
    });
    const ctx = new AudioContext({ sampleRate: 48000 });
    const src = ctx.createMediaStreamSource(stream);
    src.connect(ctx.destination);                     // вкладка продолжает звучать как обычно
    await ctx.audioWorklet.addModule("worklet.js");
    const node = new AudioWorkletNode(ctx, "pcm-tap", {
      numberOfInputs: 1, numberOfOutputs: 0, channelCount: 2, channelCountMode: "explicit",
    });
    src.connect(node);
    const s = { stream, ctx, node, ws: null, meta: { title: msg.title, url: msg.url }, closed: false };
    sessions[tabId] = s;
    node.port.onmessage = (e) => {
      if (s.ws && s.ws.readyState === 1 && s.ws.bufferedAmount < 192000) s.ws.send(e.data);
    };
    stream.getAudioTracks()[0].addEventListener("ended", () => stop(tabId));
    connect(s, tabId);
  } catch (e) {
    report(tabId, "error", e.message);
  }
}

function stop(tabId) {
  const s = sessions[tabId];
  if (!s) return;
  s.closed = true;
  try { s.ws && s.ws.close(); } catch (e) {}
  try { s.stream.getTracks().forEach((t) => t.stop()); } catch (e) {}
  try { s.ctx.close(); } catch (e) {}
  delete sessions[tabId];
  report(tabId, "ended");
}

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.target !== "offscreen") return;
  if (msg.type === "start") start(msg);
  else if (msg.type === "stop") stop(msg.tabId);
  else if (msg.type === "meta") {
    const s = sessions[msg.tabId];
    if (s) {
      s.meta = { title: msg.title, url: msg.url };
      if (s.ws && s.ws.readyState === 1) s.ws.send(JSON.stringify({ type: "meta", ...s.meta }));
    }
  }
});
