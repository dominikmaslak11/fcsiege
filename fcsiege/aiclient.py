"""Polaczenie z Claude dla okna: poswiadczenia + adapter petli na sygnaly Qt.

Sama petla rozmowy siedzi w `chat.py` i nie zna Qt - dzieki temu okno i webowe
UI korzystaja z tej samej implementacji. Tutaj zostaje to, co jest specyficzne
dla okna: skad wziac klucz oraz przelozenie zdarzen petli na sygnaly, bo
narzedzia musza wykonac sie w watku interfejsu.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QThread, Signal

from . import i18n
# poswiadczenia zyja osobno, zeby serwer HTTP nie ciagnal Qt
from .aicreds import (ANTHROPIC_PROFILE_DIR, CONFIG_DIR,  # noqa: F401
                      CRED_FILE, Credentials, anthropic_cli_present,
                      detect_credentials, forget_key, has_anthropic_profile,
                      make_client, save_key, saved_key)
# reeksport: chatpanel i testy siegaja po nie przez ten modul
from .chat import (FALLBACK_BETA, MAX_TOKENS, MAX_TOOL_ROUNDS,  # noqa: F401
                   MODEL, Conversation, stream_reply)

class ChatWorker(QThread):
    """Jedna tura rozmowy: strumieniuje tekst i wykonuje narzedzia."""

    delta = Signal(str)                 # kolejny kawalek odpowiedzi
    thinking = Signal(str)              # podsumowanie rozumowania
    tool_started = Signal(str, str)     # nazwa, argumenty (do podgladu)
    tool_finished = Signal(str)         # nazwa
    finished_ok = Signal()
    failed = Signal(str)
    # prosba o wykonanie narzedzia w watku interfejsu
    tool_requested = Signal(str, object, object)   # nazwa, argumenty, uchwyt

    def __init__(self, creds: Credentials, conversation: Conversation,
                 user_text: str, context_note: str = "", parent=None):
        super().__init__(parent)
        self._creds = creds
        self._conv = conversation
        self._user_text = user_text
        self._context_note = context_note
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    # narzedzia wykonuje watek interfejsu; tu tylko czekamy na wynik
    def _run_tool_obj(self, name: str, args: dict):
        box: dict = {}
        done = threading.Event()

        def deliver(result):
            box["result"] = result
            done.set()

        self.tool_requested.emit(name, args, deliver)
        if not done.wait(timeout=120):
            return {"blad": "przekroczono czas wykonania narzędzia"}
        return box.get("result")

    def run(self) -> None:
        """Adapter: zamienia zdarzenia wspolnej petli na sygnaly Qt."""
        try:
            client = make_client(self._creds)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Nie udało się utworzyć klienta: {exc}")
            return

        for event in stream_reply(client, self._conv, self._user_text,
                                  self._run_tool_obj, self._context_note,
                                  lambda: self._stop, i18n.language()):
            kind = event[0]
            if kind == "delta":
                self.delta.emit(event[1])
            elif kind == "thinking":
                self.thinking.emit(event[1])
            elif kind == "tool_start":
                self.tool_started.emit(event[1], event[2])
            elif kind == "tool_end":
                self.tool_finished.emit(event[1])
            elif kind == "done":
                self.finished_ok.emit()
                return
            elif kind == "error":
                self.failed.emit(event[1])
                return

    @staticmethod
    def _explain(exc: Exception) -> str:
        return f"{type(exc).__name__}: {exc}"


class ToolBridgeRunner(QObject):
    """Wykonuje narzedzia w watku interfejsu i odsyla wynik do workera."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge

    def handle(self, name: str, args: dict, deliver) -> None:
        from .aitools import dispatch
        try:
            result = dispatch(self.bridge, name, args or {})
        except Exception as exc:  # noqa: BLE001
            result = {"blad": f"{type(exc).__name__}: {exc}"}
        deliver(result)
