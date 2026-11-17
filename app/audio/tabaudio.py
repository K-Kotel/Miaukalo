"""Звук отдельных вкладок браузера от расширения «Мяукало — звук вкладки».

Расширение захватывает звук вкладки (chrome.tabCapture) и шлёт его сюда по WebSocket на 127.0.0.1:47800:
первое сообщение — JSON {"type":"hello","tab":id,"title":..,"url":..,"rate":48000,"channels":2},
дальше — двоичные блоки float32, стерео вперемешку. Сервер написан на стандартной библиотеке.
"""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import threading
import time
from urllib.parse import urlparse

import numpy as np

from .ring import Ring

PORT = 47800
GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def host_of(url: str) -> str:
    try:
        h = urlparse(url).hostname or ""
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


class TabStream:
    def __init__(self, tab_id, title, url, rate):
        self.tab_id = str(tab_id)
        self.title = title or ""
        self.url = url or ""
        self.rate = int(rate or 48000)
        self.level = 0.0
        self.connected = True
        self.last = time.time()
        self.subscribers: list = []      # TabSource, которым раздаём звук

    @property
    def host(self):
        return host_of(self.url)

    def info(self) -> dict:
        return {"type": "tab", "device": f"tab:{self.tab_id}", "process": self.host, "label": self.title or self.host,
                "sub": self.host or self.url, "level": self.level, "active": self.level > 0.002}


class TabHub:
    """Принимает подключения расширения и раздаёт звук вкладок подписчикам (живым источникам)."""

    def __init__(self, port=PORT):
        self.port = port
        self.streams: dict[str, TabStream] = {}
        self.subs: list = []
        self.lock = threading.Lock()
        self.error = ""
        self.running = False
        self._sock = None

    # ------------------------------------------------------------ сервер
    def start(self):
        if self.running:
            return True
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", self.port))     # только этот компьютер
            s.listen(8)
            s.settimeout(0.5)
        except OSError as e:
            self.error = f"порт {self.port} занят: {e}"
            return False
        self._sock = s
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True, name="tab-hub").start()
        return True

    def stop(self):
        self.running = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass

    def _accept_loop(self):
        while self.running:
            try:
                conn, _addr = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._client, args=(conn,), daemon=True, name="tab-client").start()

    def _client(self, conn: socket.socket):
        stream = None
        try:
            conn.settimeout(10)
            if not self._handshake(conn):
                return
            conn.settimeout(30)
            for opcode, payload in self._frames(conn):
                if opcode == 1:                                  # текст
                    msg = json.loads(payload.decode("utf-8"))
                    if msg.get("type") == "hello":
                        stream = TabStream(msg.get("tab"), msg.get("title"), msg.get("url"), msg.get("rate"))
                        self._register(stream)
                    elif msg.get("type") == "meta" and stream:
                        stream.title = msg.get("title") or stream.title
                        stream.url = msg.get("url") or stream.url
                elif opcode == 2 and stream is not None:          # звук
                    self._audio(stream, payload)
                elif opcode == 8:                                 # закрытие
                    break
                elif opcode == 9:                                 # ping → pong
                    self._send(conn, 0xA, payload)
        except Exception:
            pass
        finally:
            if stream is not None:
                self._unregister(stream)
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def _handshake(conn) -> bool:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return False
            data += chunk
            if len(data) > 16384:
                return False
        head = data.split(b"\r\n\r\n", 1)[0].decode("latin-1")
        headers = {}
        for line in head.split("\r\n")[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        origin = headers.get("origin", "")
        # принимаем только расширение браузера (обычные сайты сюда подключиться не могут)
        if origin and not origin.startswith(("chrome-extension://", "moz-extension://", "extension://")):
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            return False
        key = headers.get("sec-websocket-key")
        if not key:
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return False
        acc = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {acc}\r\n\r\n").encode())
        return True

    @staticmethod
    def _recv_exact(conn, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return bytes(buf)

    def _frames(self, conn):
        """Генератор (opcode, payload) с поддержкой фрагментированных сообщений."""
        frag_op, frag = None, bytearray()
        while self.running:
            b1, b2 = self._recv_exact(conn, 2)
            fin, op = b1 & 0x80, b1 & 0x0F
            masked, ln = b2 & 0x80, b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._recv_exact(conn, 2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._recv_exact(conn, 8))[0]
            if ln > 8 * 1024 * 1024:
                raise ConnectionError("слишком большой кадр")
            mask = self._recv_exact(conn, 4) if masked else None
            payload = self._recv_exact(conn, ln) if ln else b""
            if mask:
                arr = np.frombuffer(payload, np.uint8)
                m = np.frombuffer((mask * (ln // 4 + 1))[:ln], np.uint8)
                payload = (arr ^ m).tobytes()
            if op == 0:
                frag += payload
                if fin:
                    yield frag_op, bytes(frag)
                    frag_op, frag = None, bytearray()
                continue
            if not fin and op in (1, 2):
                frag_op, frag = op, bytearray(payload)
                continue
            yield op, payload

    @staticmethod
    def _send(conn, op, payload=b""):
        hdr = bytes([0x80 | op])
        n = len(payload)
        hdr += bytes([n]) if n < 126 else (bytes([126]) + struct.pack(">H", n))
        try:
            conn.sendall(hdr + payload)
        except Exception:
            pass

    # ------------------------------------------------------------ вкладки и подписчики
    def _register(self, st: TabStream):
        with self.lock:
            old = self.streams.get(st.tab_id)
            if old:
                old.connected = False
            self.streams[st.tab_id] = st
            for sub in self.subs:
                if sub.wants(st):
                    sub.attach(st)

    def _unregister(self, st: TabStream):
        with self.lock:
            st.connected = False
            if self.streams.get(st.tab_id) is st:
                del self.streams[st.tab_id]
            for sub in list(st.subscribers):
                sub.detach(st)

    def _audio(self, st: TabStream, payload: bytes):
        if len(payload) % 8:
            payload = payload[: len(payload) - len(payload) % 8]
        block = np.frombuffer(payload, dtype="<f4").reshape(-1, 2)
        if st.rate != 48000 and len(block):
            import soxr
            block = soxr.resample(block, st.rate, 48000).astype(np.float32)
        st.level = max(float(np.abs(block).max()) if len(block) else 0.0, st.level * 0.7)
        st.last = time.time()
        for sub in list(st.subscribers):
            sub.ring.write(block)

    def subscribe(self, sub):
        with self.lock:
            self.subs.append(sub)
            for st in self.streams.values():
                if sub.wants(st):
                    sub.attach(st)
                    break

    def unsubscribe(self, sub):
        with self.lock:
            if sub in self.subs:
                self.subs.remove(sub)
            for st in self.streams.values():
                if sub in st.subscribers:
                    st.subscribers.remove(sub)

    def snapshot(self) -> list:
        with self.lock:
            now = time.time()
            out = []
            for st in self.streams.values():
                if now - st.last > 0.5:
                    st.level *= 0.5
                out.append(st.info())
            return out


HUB = TabHub()
