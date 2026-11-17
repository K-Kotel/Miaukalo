"""Приём звука вкладки: WebSocket-рукопожатие, проверка Origin, кадры с маской, раздача источнику, переподключение."""
import base64
import json
import os
import socket
import struct
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.audio.sources import TabSource  # noqa: E402
from app.audio.tabaudio import TabHub  # noqa: E402
import app.audio.tabaudio as ta  # noqa: E402

PORT = 47911
hub = TabHub(PORT)
ta.HUB = hub
assert hub.start(), hub.error


def ws_connect(origin="chrome-extension://abcdef"):
    s = socket.create_connection(("127.0.0.1", PORT))
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET /tab HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nOrigin: {origin}\r\n\r\n").encode())
    resp = s.recv(1024).decode()
    return s, resp


def send(s, op, payload):
    mask = os.urandom(4)
    n = len(payload)
    hdr = bytes([0x80 | op])
    if n < 126:
        hdr += bytes([0x80 | n])
    elif n < 65536:
        hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    s.sendall(hdr + mask + masked)


# обычный сайт подключиться не может
s, resp = ws_connect("https://evil.example")
assert "403" in resp, resp
s.close()

s, resp = ws_connect()
assert "101" in resp, resp
send(s, 1, json.dumps({"type": "hello", "tab": 42, "title": "Lo-fi beats", "url": "https://www.youtube.com/watch?v=1",
                       "rate": 48000, "channels": 2}).encode())
time.sleep(0.2)
snap = hub.snapshot()
assert snap and snap[0]["device"] == "tab:42" and snap[0]["process"] == "youtube.com", snap

src = TabSource(48000, "tab:42", "youtube.com")
assert src.start()
t = np.arange(960) / 48000
block = np.stack([np.sin(2 * np.pi * 440 * t), np.sin(2 * np.pi * 440 * t)], 1).astype("<f4") * 0.5
for _ in range(10):
    send(s, 2, block.tobytes())
time.sleep(0.3)
assert src.ring.available() == 9600, src.ring.available()
out = np.zeros((960, 2), np.float32)
src.ring.read_into(out)
assert abs(float(out.max()) - 0.5) < 0.01

# вкладку закрыли и открыли снова (новый id, тот же сайт) — источник подхватывает её сам
send(s, 8, b"")
s.close()
time.sleep(0.3)
assert src.attached is None
s2, resp = ws_connect()
send(s2, 1, json.dumps({"type": "hello", "tab": 77, "title": "Другое видео", "url": "https://youtube.com/watch?v=2"}).encode())
time.sleep(0.2)
assert src.attached is not None and src.tab_id == "77", src.tab_id
send(s2, 2, block.tobytes())
time.sleep(0.2)
assert src.ring.available() == 960
s2.close()
hub.stop()
print("tab audio ok")
