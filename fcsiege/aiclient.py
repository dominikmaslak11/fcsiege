"""Polaczenie z Claude: poswiadczenia + petla rozmowy z uzyciem narzedzi.

Petle prowadzimy recznie (a nie przez SDK-owy tool runner), bo narzedzia musza
byc wykonane w watku interfejsu Qt, a tekst ma splywac do okna token po tokenie.
Dispatch narzedzia jest wiec przekazywany sygnalem do watku glownego, ktory
odsyla wynik przez zdarzenie.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QThread, Signal

from .aitools import SYSTEM_PROMPT, TOOL_SPECS, result_to_text

MODEL = "claude-opus-5"
MAX_TOKENS = 16000
MAX_TOOL_ROUNDS = 24
FALLBACK_BETA = "server-side-fallback-2026-07-01"

CONFIG_DIR = os.path.expanduser("~/.config/fcsiege")
CRED_FILE = os.path.join(CONFIG_DIR, "credentials.json")
ANTHROPIC_PROFILE_DIR = os.path.expanduser("~/.config/anthropic/credentials")


# ------------------------------------------------------------- poswiadczenia

@dataclass
class Credentials:
    """Skad wziac klucz i czy w ogole go mamy."""
    source: str          # "env" | "plik" | "profil" | "brak"
    api_key: str | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.source != "brak"


def saved_key() -> str | None:
    try:
        with open(CRED_FILE, encoding="utf-8") as fh:
            return json.load(fh).get("api_key") or None
    except (OSError, ValueError):
        return None


def save_key(api_key: str) -> None:
    """Zapisuje klucz tylko do odczytu dla wlasciciela (0600)."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CRED_FILE, "w", encoding="utf-8") as fh:
        json.dump({"api_key": api_key}, fh)
    os.chmod(CRED_FILE, stat.S_IRUSR | stat.S_IWUSR)


def forget_key() -> None:
    try:
        os.remove(CRED_FILE)
    except OSError:
        pass


def has_anthropic_profile() -> bool:
    """Czy istnieje profil OAuth zalozony przez CLI Anthropica."""
    try:
        return any(f.endswith(".json") for f in os.listdir(ANTHROPIC_PROFILE_DIR))
    except OSError:
        return False


def detect_credentials() -> Credentials:
    """Kolejnosc jak w SDK: zmienna srodowiskowa, nasz plik, profil OAuth."""
    env = os.environ.get("ANTHROPIC_API_KEY")
    if env:
        return Credentials("env", env, "zmienna środowiskowa ANTHROPIC_API_KEY")
    key = saved_key()
    if key:
        return Credentials("plik", key, CRED_FILE)
    if has_anthropic_profile():
        return Credentials("profil", None, "profil OAuth z ~/.config/anthropic")
    return Credentials("brak")


def anthropic_cli_present() -> bool:
    """Czy w PATH jest CLI Anthropica (a nie Apache Ant o tej samej nazwie)."""
    import shutil
    import subprocess
    path = shutil.which("ant")
    if not path:
        return False
    try:
        out = subprocess.run([path, "auth", "--help"], capture_output=True,
                             text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return False
    blob = (out.stdout + out.stderr).lower()
    return "anthropic" in blob or "login" in blob and "buildfile" not in blob


def make_client(creds: Credentials):
    """Tworzy klienta SDK. Bez klucza SDK sam znajdzie profil OAuth."""
    import anthropic
    if creds.api_key:
        return anthropic.Anthropic(api_key=creds.api_key)
    return anthropic.Anthropic()


# ----------------------------------------------------------------- rozmowa

@dataclass
class Conversation:
    """Historia rozmowy w formacie API."""
    messages: list = field(default_factory=list)

    def clear(self) -> None:
        self.messages.clear()


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
    def _run_tool(self, name: str, args: dict) -> str:
        box: dict = {}
        done = threading.Event()

        def deliver(result):
            box["result"] = result
            done.set()

        self.tool_requested.emit(name, args, deliver)
        if not done.wait(timeout=120):
            return result_to_text({"blad": "przekroczono czas wykonania narzędzia"})
        return result_to_text(box.get("result"))

    def run(self) -> None:  # noqa: C901 - petla narzedziowa z natury ma galezie
        try:
            client = make_client(self._creds)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Nie udało się utworzyć klienta: {exc}")
            return

        system = [{
            "type": "text",
            "text": SYSTEM_PROMPT + (f"\n\nAktualny stan aplikacji:\n{self._context_note}"
                                     if self._context_note else ""),
            # stabilny prefiks - warto go zbuforowac miedzy turami
            "cache_control": {"type": "ephemeral"},
        }]
        self._conv.messages.append({"role": "user", "content": self._user_text})

        try:
            for _round in range(MAX_TOOL_ROUNDS):
                if self._stop:
                    self.failed.emit("Przerwano.")
                    return

                response = self._one_turn(client, system)
                if response is None:
                    return

                self._conv.messages.append(
                    {"role": "assistant", "content": response.content})

                if response.stop_reason == "refusal":
                    cat = getattr(getattr(response, "stop_details", None), "category", None)
                    self.failed.emit(
                        "Model odmówił odpowiedzi"
                        + (f" (kategoria: {cat})" if cat else "") + ".")
                    return

                if response.stop_reason == "pause_turn":
                    continue  # narzedzie serwerowe potrzebuje kolejnej rundy

                tool_uses = [b for b in response.content if b.type == "tool_use"]
                if not tool_uses:
                    self.finished_ok.emit()
                    return

                results = []
                for block in tool_uses:
                    args = block.input if isinstance(block.input, dict) else {}
                    self.tool_started.emit(block.name, json.dumps(args, ensure_ascii=False))
                    try:
                        text = self._run_tool(block.name, args)
                        is_error = False
                    except Exception as exc:  # noqa: BLE001
                        text, is_error = f"błąd narzędzia: {exc}", True
                    self.tool_finished.emit(block.name)
                    results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": text,
                        **({"is_error": True} if is_error else {}),
                    })
                # wszystkie wyniki musza wrocic w JEDNEJ wiadomosci uzytkownika
                self._conv.messages.append({"role": "user", "content": results})

            self.failed.emit("Przekroczono limit rund narzędziowych.")
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(self._explain(exc))

    def _one_turn(self, client, system):
        """Jedno wywolanie API ze strumieniowaniem."""
        import anthropic
        try:
            with client.beta.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                system=system,
                tools=TOOL_SPECS,
                thinking={"type": "adaptive", "display": "summarized"},
                messages=self._conv.messages,
            ) as stream:
                for event in stream:
                    if self._stop:
                        break
                    if event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            self.delta.emit(event.delta.text)
                        elif event.delta.type == "thinking_delta":
                            self.thinking.emit(event.delta.thinking)
                return stream.get_final_message()
        except anthropic.AuthenticationError:
            self.failed.emit("Klucz API został odrzucony. Sprawdź go w ustawieniach czatu.")
        except anthropic.PermissionDeniedError:
            self.failed.emit("Klucz nie ma uprawnień do modelu " + MODEL + ".")
        except anthropic.RateLimitError as exc:
            after = exc.response.headers.get("retry-after", "kilkadziesiąt")
            self.failed.emit(f"Limit zapytań wyczerpany. Spróbuj za {after} s.")
        except anthropic.APIConnectionError:
            self.failed.emit("Brak połączenia z api.anthropic.com.")
        except anthropic.APIStatusError as exc:
            self.failed.emit(f"Błąd API {exc.status_code}: {exc.message}")
        return None

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
