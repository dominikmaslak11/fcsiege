"""Testy dwujezycznosci: okno, narzedzia, API.

Najwazniejszy jest test kompletnosci katalogu - pilnuje, zeby kazdy napis
owiniety w `_()` mial tlumaczenie. Bez tego angielski interfejs cichcem
gubilby pojedyncze polskie zdania.
"""

from __future__ import annotations

import ast
import json
import os
import sys
import threading
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fcsiege import i18n                                       # noqa: E402
from fcsiege.aitools import TOOL_SPECS, dispatch, localized_specs   # noqa: E402
from fcsiege.headless import HeadlessBridge                    # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK  ' if ok else 'BLAD'} {name}{'  ' + str(detail) if detail else ''}")
    if not ok:
        failures.append(name)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_catalog_complete() -> None:
    print("\nKompletnosc katalogu:")
    missing: list[str] = []
    wrapped = 0
    for name in ("app.py", "chatpanel.py"):
        path = os.path.join(ROOT, "fcsiege", name)
        for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        wrapped += 1
                        if arg.value not in i18n.UI:
                            missing.append(arg.value)
    check("każdy napis owinięty w _() ma tłumaczenie",
          not missing, f"{wrapped} napisów; brakuje: {missing[:5]}")

    empty = [k for k, v in i18n.UI.items() if not v.strip()]
    check("żadne tłumaczenie nie jest puste", not empty, empty[:5])

    same = [k for k, v in i18n.UI.items()
            if v == k and any(c in k for c in "ąćęłńóśźż")]
    check("nic z polskimi znakami nie zostało nieprzetłumaczone", not same, same[:5])


def test_tool_layer() -> None:
    print("\nWarstwa narzedzi:")
    names = {s["name"] for s in TOOL_SPECS}
    check("każde narzędzie ma nazwę angielską",
          all(n in i18n.TOOL_NAMES for n in names),
          sorted(n for n in names if n not in i18n.TOOL_NAMES)[:5])
    check("każde narzędzie ma opis angielski",
          all(n in i18n.TOOL_DESC for n in names),
          sorted(n for n in names if n not in i18n.TOOL_DESC)[:5])
    check("nazwy angielskie są unikalne",
          len(set(i18n.TOOL_NAMES.values())) == len(i18n.TOOL_NAMES))

    en_names = {s["name"] for s in localized_specs("en")}
    check("alias wraca do nazwy kanonicznej",
          all(i18n.canonical_tool(n) in names for n in en_names))

    bridge = HeadlessBridge("sandbox")
    i18n.set_language("pl")
    pl = dispatch(bridge, "pokaz_stan", {})
    i18n.set_language("en")
    en = dispatch(bridge, "show_state", {})
    i18n.set_language("pl")
    check("ten sam wynik, inne klucze",
          set(pl) != set(en) and len(pl) == len(en),
          f"{len(pl)} pól")
    check("klucze angielskie bez polskich znaków",
          not any(c in k for k in en for c in "ąćęłńóśźż"))
    check("nazwy z zestawu reguł nietknięte",
          en.get("ruleset") == pl.get("zestaw_regul") == "sandbox")

    i18n.set_language("en")
    out = dispatch(bridge, "set_my_unit", {"unit": "Warriors", "veterancy": 1})
    i18n.set_language("pl")
    check("argumenty angielskie działają",
          isinstance(out, dict) and "error" not in out, list(out)[:4])


def test_values_and_roundtrip() -> None:
    print("\nWartosci i droga powrotna:")
    i18n.set_language("en")
    check("tryb tłumaczony jako wartość", i18n.value("szturm") == "assault")
    back = i18n.untranslate_args({"mode": "assault", "my_unit": {"unit": "X"}})
    check("argumenty wracają do postaci kanonicznej",
          back == {"tryb": "szturm", "moja_jednostka": {"jednostka": "X"}}, back)
    check("klucz dynamiczny (rozmiar N)", i18n.key("rozmiar 12") == "size 12")
    check("nieznany klucz przechodzi bez zmian",
          i18n.key("Output_Bonus") == "Output_Bonus")
    i18n.set_language("pl")
    check("po polsku translate nic nie robi",
          i18n.translate({"miasto": "X"}) == {"miasto": "X"})


def test_http() -> None:
    print("\nAPI HTTP:")
    from http.server import ThreadingHTTPServer

    from fcsiege.http_api import Engine, Handler

    handler = type("H", (Handler,),
                   {"engine": Engine("sandbox", "nigdy"), "token": None})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def get(path, **headers):
        req = urllib.request.Request(base + path, headers=headers)
        return json.loads(urllib.request.urlopen(req, timeout=30).read())

    try:
        pl = get("/stan")
        en_q = get("/state?lang=en")
        en_h = get("/stan", **{"Accept-Language": "en-GB,en;q=0.9"})
        check("?lang=en przełącza język", "ruleset" in en_q and "zestaw_regul" in pl)
        check("Accept-Language przełącza język", set(en_h) == set(en_q))
        check("ścieżki angielskie działają", "ruleset" in en_q)
        tools = get("/tools?lang=en")["narzedzia"]
        check("spis narzędzi po angielsku",
              {"compute", "rank", "waste"} <= {t["name"] for t in tools})
        def post(path, payload, **headers):
            req = urllib.request.Request(
                base + path, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json", **headers})
            return json.loads(urllib.request.urlopen(req, timeout=60).read())

        try:
            post("/tool/nie-ma?lang=en", {})
            check("nieznane narzędzie daje 404", False)
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read())
            check("nieznane narzędzie daje 404 z listą po angielsku",
                  exc.code == 404 and "compute" in body.get("dostepne", []),
                  body.get("error"))
        try:
            get("/nie-ma-takiej?lang=en")
            check("nieznana ścieżka daje 404", False)
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read())
            check("komunikat o ścieżce po angielsku",
                  "no such path" in str(body.get("error", "")), body.get("error"))
    finally:
        srv.shutdown()


def test_window() -> None:
    print("\nOkno w obu jezykach:")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from fcsiege import theme
    from fcsiege.app import MainWindow

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(theme.stylesheet())
    seen = {}
    for lang in ("pl", "en"):
        i18n.set_language(lang)
        win = MainWindow()
        tabs = [win.tabs.tabText(i) for i in range(win.tabs.count())]
        win.btn_defense.click()
        tabs += [win.tabs.tabText(i) for i in range(win.tabs.count())]
        seen[lang] = (win.windowTitle(), tabs)
    i18n.set_language("pl")

    check("tytuł okna się różni", seen["pl"][0] != seen["en"][0], seen["en"][0])
    check("zakładki się różnią", seen["pl"][1] != seen["en"][1])
    check("angielskie zakładki bez polskich znaków",
          not any(c in t for t in seen["en"][1] for c in "ąćęłńóśźż"),
          [t for t in seen["en"][1] if any(c in t for c in "ąćęłńóśźż")])
    check("tyle samo zakładek", len(seen["pl"][1]) == len(seen["en"][1]))


if __name__ == "__main__":
    test_catalog_complete()
    test_tool_layer()
    test_values_and_roundtrip()
    test_http()
    test_window()

    print("\n" + "=" * 60)
    if failures:
        print(f"NIEPOWODZENIA ({len(failures)}): " + ", ".join(failures))
        sys.exit(1)
    print("Wszystkie testy dwujezycznosci przeszly.")
