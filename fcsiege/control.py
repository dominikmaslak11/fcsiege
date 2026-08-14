"""Lokalne gniazdo sterujace uruchomionym oknem aplikacji.

Serwer MCP i API HTTP moga dzieki temu przestawiac kontrolki w oknie, ktore
uzytkownik ma otwarte, zamiast liczyc we wlasnym, osobnym stanie. Protokol to
JSON po jednej linii na wiadomosc:

    -> {"tool": "policz", "args": {}}
    <- {"ok": true, "result": {...}}

Gniazdo jest lokalne (AF_UNIX) i tylko dla wlasciciela - nie sluchamy na sieci.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import threading

DEFAULT_SOCKET = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR") or "/tmp",
    f"fcsiege-{os.getuid()}.sock" if hasattr(os, "getuid") else "fcsiege.sock")


def socket_path() -> str:
    return os.environ.get("FCSIEGE_SOCKET", DEFAULT_SOCKET)


# ------------------------------------------------------------ serwer (w oknie)

class ControlServer:
    """Nasluchuje na gniezdzie w osobnym watku i wykonuje narzedzia w watku Qt.

    Swiadomie nie uzywamy QtNetwork - nie jest czescia podstawowej instalacji
    PySide6 w wielu dystrybucjach, a zwykle gniazdo uniksowe wystarcza. Kazde
    zadanie jest przekazywane sygnalem do watku interfejsu, wiec kontrolki
    zmieniaja sie bezpiecznie.
    """

    def __init__(self, bridge, path: str | None = None):
        self.bridge = bridge
        self.path = path or socket_path()
        self._marshaller = _Marshaller(bridge)

        try:
            os.unlink(self.path)
        except OSError:
            pass
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(self.path)
        os.chmod(self.path, stat.S_IRUSR | stat.S_IWUSR)
        self._sock.listen(8)
        self._sock.settimeout(0.5)

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True,
                                        name="fcsiege-control")
        self._thread.start()

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=self._serve_client, args=(conn,),
                             daemon=True).start()

    def _serve_client(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(300)
            buffer = bytearray()
            while not self._stop.is_set():
                try:
                    part = conn.recv(65536)
                except (socket.timeout, OSError):
                    return
                if not part:
                    return
                buffer.extend(part)
                while b"\n" in buffer:
                    line, _, rest = bytes(buffer).partition(b"\n")
                    buffer.clear()
                    buffer.extend(rest)
                    if line.strip():
                        try:
                            conn.sendall(self._handle(line) + b"\n")
                        except OSError:
                            return

    def _handle(self, line: bytes) -> bytes:
        try:
            req = json.loads(line.decode("utf-8"))
            name = str(req.get("tool", ""))
            args = req.get("args") or {}
            result = self._marshaller.run(name, args)
            payload = {"ok": True, "result": result}
        except Exception as exc:  # noqa: BLE001
            payload = {"ok": False, "blad": f"{type(exc).__name__}: {exc}"}
        return json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        try:
            os.unlink(self.path)
        except OSError:
            pass


class _Marshaller:
    """Przenosi wykonanie narzedzia z watku gniazda do watku interfejsu."""

    def __init__(self, bridge):
        from PySide6.QtCore import QObject, Signal

        class _Relay(QObject):
            job = Signal(object, object, object)

            def __init__(self, outer):
                super().__init__()
                self._bridge = outer
                self.job.connect(self._execute)

            def _execute(self, name, args, deliver):
                from .aitools import dispatch
                try:
                    deliver(("ok", dispatch(self._bridge, name, args)))
                except Exception as exc:  # noqa: BLE001
                    deliver(("blad", f"{type(exc).__name__}: {exc}"))

        self._relay = _Relay(bridge)

    def run(self, name: str, args: dict, timeout: float = 180.0):
        box: dict = {}
        done = threading.Event()

        def deliver(payload):
            box["payload"] = payload
            done.set()

        self._relay.job.emit(name, args, deliver)
        if not done.wait(timeout):
            raise TimeoutError("okno aplikacji nie odpowiedziało w czasie")
        kind, value = box["payload"]
        if kind == "blad":
            raise RuntimeError(value)
        return value


# ------------------------------------------------------------- klient (czysty)

class ControlClient:
    """Prosty klient gniazda - bez Qt, uzywany przez MCP i HTTP."""

    def __init__(self, path: str | None = None, timeout: float = 120.0):
        self.path = path or socket_path()
        self.timeout = timeout

    def available(self) -> bool:
        if not os.path.exists(self.path):
            return False
        try:
            with self._connect():
                return True
        except OSError:
            return False

    def _connect(self) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self.path)
        return sock

    def call(self, tool: str, args: dict | None = None) -> dict:
        request = json.dumps({"tool": tool, "args": args or {}},
                             ensure_ascii=False).encode("utf-8") + b"\n"
        with self._connect() as sock:
            sock.sendall(request)
            chunks = bytearray()
            while b"\n" not in chunks:
                part = sock.recv(65536)
                if not part:
                    break
                chunks.extend(part)
        line = bytes(chunks).split(b"\n", 1)[0]
        if not line:
            raise OSError("okno aplikacji nie odpowiedziało")
        payload = json.loads(line.decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError(payload.get("blad", "nieznany błąd okna"))
        return payload["result"]
