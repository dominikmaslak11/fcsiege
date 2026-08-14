"""Testy warstwy asystenta. Uruchom: python3 tests/test_chat.py

Petla narzedziowa jest sprawdzana bez sieci - klient API jest podstawiony,
zeby dalo sie zweryfikowac przeplyw: tool_use -> wykonanie w watku interfejsu
-> tool_result -> odpowiedz koncowa.
"""

import os
import sys
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from fcsiege import aiclient  # noqa: E402
from fcsiege.aitools import TOOL_SPECS, dispatch  # noqa: E402
from fcsiege.app import MainWindow  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("  OK   " if ok else "  BLAD ") + name + (f"  {detail}" if detail else ""))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------- atrapa klienta

def block(**kw):
    return types.SimpleNamespace(**kw)


class FakeStream:
    def __init__(self, message, events):
        self._message = message
        self._events = events

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._message


class FakeMessages:
    """Zwraca zaplanowane odpowiedzi po kolei i zapamietuje parametry."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        message, events = self.turns.pop(0)
        return FakeStream(message, events)


class FakeClient:
    def __init__(self, turns):
        self.beta = types.SimpleNamespace(messages=FakeMessages(turns))


def text_event(text):
    return types.SimpleNamespace(
        type="content_block_delta",
        delta=types.SimpleNamespace(type="text_delta", text=text))


def test_tool_specs():
    print("\nSchematy narzedzi:")
    names = [t["name"] for t in TOOL_SPECS]
    check("nazwy sa unikalne", len(names) == len(set(names)), str(len(names)))
    check("kazde ma opis i schemat obiektu",
          all(len(t["description"]) > 60
              and t["input_schema"].get("type") == "object" for t in TOOL_SPECS))
    check("schematy sa domkniete (additionalProperties=false)",
          all(t["input_schema"].get("additionalProperties") is False
              for t in TOOL_SPECS))


def test_bridge(win):
    print("\nMost do interfejsu:")
    snap = dispatch(win, "pokaz_stan", {})
    check("pokaz_stan zwraca komplet pol",
          {"tryb", "zestaw_regul", "moja_jednostka", "sily_wroga"} <= set(snap))

    out = dispatch(win, "ustaw_scenariusz", {
        "tryb": "szturm", "zestaw_regul": "classic", "teren_miasta": "Hills",
        "budowle": ["City Walls"], "w_miescie": True})
    check("ustaw_scenariusz zmienia interfejs",
          out["teren_miasta"] == "Hills" and out["budowle"] == ["City Walls"])
    check("kontrolka faktycznie sie przestawila",
          win.cmb_def_terrain.currentData() == "Hills"
          and win.chk_buildings["City Walls"].isChecked())

    dispatch(win, "ustaw_sily_wroga",
             {"jednostki": [{"jednostka": "Warriors", "liczba": 5, "stopien": 0}]})
    dispatch(win, "ustaw_moja_jednostke", {"jednostka": "Catapult", "liczba": 8})
    res = dispatch(win, "policz", {})
    check("policz zwraca liczby z silnika",
          abs(res["sila_obrony"] - 9.0) < 1e-6 and res["potrzeba_90proc"] is not None,
          f"obrona={res['sila_obrony']} potrzeba={res['potrzeba_90proc']}")
    check("wynik z narzedzia zgadza sie z kartą odpowiedzi",
          res["potrzeba_90proc"] == win._last_result.attacks_for(0.90))

    bad = dispatch(win, "ustaw_scenariusz", {"teren_miasta": "Nie ma takiego"})
    check("zla nazwa daje ostrzezenie, nie wyjatek", "ostrzezenia" in bad)
    miss = dispatch(win, "dane_jednostki", {"jednostka": "Pikeman"})
    check("literowka w nazwie podpowiada poprawna",
          "Pikemen" in miss.get("podobne", []), str(miss.get("podobne")))
    check("nieznane narzedzie zwraca blad",
          "blad" in dispatch(win, "nie_ma_takiego", {}))


def test_tool_loop(app, win):
    """Pelna tura: model wola narzedzie, dostaje wynik, konczy tekstem."""
    print("\nPetla narzedziowa (klient podstawiony):")

    turn1 = (types.SimpleNamespace(
        stop_reason="tool_use",
        content=[block(type="tool_use", id="toolu_1", name="policz", input={})],
    ), [])
    turn2 = (types.SimpleNamespace(
        stop_reason="end_turn",
        content=[block(type="text", text="Potrzebujesz 13 katapult.")],
    ), [text_event("Potrzebujesz "), text_event("13 katapult.")])

    fake = FakeClient([turn1, turn2])
    original = aiclient.make_client
    aiclient.make_client = lambda creds: fake
    try:
        conv = aiclient.Conversation()
        worker = aiclient.ChatWorker(
            aiclient.Credentials("plik", "sk-test", "atrapa"), conv,
            "Ile katapult?", win.ai_context_note())
        seen = {"text": "", "tools": [], "done": False, "fail": None}
        worker.tool_requested.connect(win.ai_run_tool)
        worker.delta.connect(lambda t: seen.__setitem__("text", seen["text"] + t))
        worker.tool_started.connect(lambda n, a: seen["tools"].append(n))
        worker.finished_ok.connect(lambda: (seen.__setitem__("done", True), app.quit()))
        worker.failed.connect(lambda m: (seen.__setitem__("fail", m), app.quit()))
        QTimer.singleShot(20000, app.quit)
        worker.start()
        app.exec()
        worker.wait(3000)
    finally:
        aiclient.make_client = original

    check("tura zakonczyla sie sukcesem", seen["done"] and not seen["fail"],
          str(seen["fail"] or ""))
    check("narzedzie zostalo wywolane", seen["tools"] == ["policz"], str(seen["tools"]))
    check("tekst splynal strumieniem", seen["text"] == "Potrzebujesz 13 katapult.",
          repr(seen["text"]))

    roles = [m["role"] for m in conv.messages]
    check("historia ma poprawne role", roles == ["user", "assistant", "user", "assistant"],
          str(roles))
    tool_msg = conv.messages[2]["content"]
    check("wynik narzedzia wrocil jako tool_result z tym samym id",
          isinstance(tool_msg, list) and tool_msg[0]["type"] == "tool_result"
          and tool_msg[0]["tool_use_id"] == "toolu_1")
    check("wynik narzedzia zawiera liczby z silnika",
          "sila_obrony" in tool_msg[0]["content"])

    kwargs = fake.beta.messages.calls[0]
    check("uzyty model to claude-opus-5", kwargs["model"] == "claude-opus-5",
          kwargs["model"])
    check("wlaczone adaptacyjne myslenie",
          kwargs["thinking"]["type"] == "adaptive")
    check("wlaczony serwerowy fallback na odmowe",
          kwargs.get("fallbacks") == "default"
          and aiclient.FALLBACK_BETA in kwargs.get("betas", []))
    check("prompt systemowy ma znacznik bufora",
          kwargs["system"][0].get("cache_control", {}).get("type") == "ephemeral")
    check("narzedzia poszly w komplecie", len(kwargs["tools"]) == len(TOOL_SPECS))


def test_refusal(app, win):
    """Odmowa modelu ma trafic do uzytkownika jako komunikat, nie jako wyjatek."""
    print("\nObsluga odmowy:")
    refusal = (types.SimpleNamespace(
        stop_reason="refusal",
        stop_details=types.SimpleNamespace(category="cyber"),
        content=[],
    ), [])
    fake = FakeClient([refusal])
    original = aiclient.make_client
    aiclient.make_client = lambda creds: fake
    try:
        worker = aiclient.ChatWorker(
            aiclient.Credentials("plik", "sk-test", "atrapa"),
            aiclient.Conversation(), "test", "")
        got = {}
        worker.tool_requested.connect(win.ai_run_tool)
        worker.failed.connect(lambda m: (got.__setitem__("msg", m), app.quit()))
        worker.finished_ok.connect(app.quit)
        QTimer.singleShot(15000, app.quit)
        worker.start()
        app.exec()
        worker.wait(3000)
    finally:
        aiclient.make_client = original
    check("odmowa zglaszana jako czytelny komunikat",
          "odmówił" in got.get("msg", ""), got.get("msg", "brak"))


def test_credentials():
    print("\nPoswiadczenia:")
    creds = aiclient.detect_credentials()
    check("wykrywanie zwraca znane zrodlo",
          creds.source in ("env", "plik", "profil", "brak"), creds.source)
    check("brak poswiadczen oznacza brak polaczenia",
          creds.ok == (creds.source != "brak"))


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    app.processEvents()

    test_tool_specs()
    test_credentials()
    test_bridge(win)
    test_tool_loop(app, win)
    test_refusal(app, win)

    print("\n" + "=" * 60)
    if failures:
        print(f"NIEPOWODZENIA ({len(failures)}): " + ", ".join(failures))
        sys.exit(1)
    print("Wszystkie testy asystenta przeszly.")
