"""Tryb nasluchu: aplikacja sama czyta nowe zapisy i doradza.

Uruchamiany przez `fcsiege.py watch`. Nie wymaga Qt ani przegladarki - nadaje
sie do puszczenia w drugim oknie terminala obok gry.
"""

from __future__ import annotations

import argparse
import sys
import threading

from . import i18n
from .headless import HeadlessBridge
from .watcher import SaveWatcher, newest_save, summarize

WAGI = {"krytyczne": "\033[31m", "pilne": "\033[33m", "warte uwagi": "\033[36m"}
RESET = "\033[0m"


def _print(payload: dict, kolor: bool) -> None:
    if "blad" in payload:
        print(f"  ! {payload['blad']}", file=sys.stderr)
        return
    stan = payload.get("stan", {})
    ja = payload.get("ja", {})
    head = (f"\n=== {payload.get('plik', '?')} · tura "
            f"{stan.get('tura', stan.get('turn', '?'))} · "
            f"złoto {ja.get('zloto', ja.get('gold', '?'))} · "
            f"miast {ja.get('miast', ja.get('cities', '?'))} ===")
    print(head)
    alerty = payload.get("alerty", [])
    if not alerty:
        print("  nic pilnego")
        return
    for a in alerty:
        waga = a.get("waga", a.get("severity", ""))
        tur = a.get("tur_do_szkody", a.get("turns_to_harm"))
        pref = WAGI.get(waga, "") if kolor else ""
        suf = RESET if kolor and pref else ""
        czas = f" ({tur} tur)" if tur is not None else ""
        print(f"  {pref}[{waga}]{suf} {a.get('miasto', a.get('city', ''))}"
              f"{czas}: {a.get('rodzaj', a.get('kind', ''))}")
        print(f"      → {a.get('rada', a.get('advice', ''))}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="fcsiege.py watch",
        description="Czyta nowe zapisy gry i od razu podaje ostrzeżenia.")
    ap.add_argument("--ruleset", default="sandbox")
    ap.add_argument("--lang", "--jezyk", choices=list(i18n.LANGS), default="pl")
    ap.add_argument("--interval", type=float, default=3.0,
                    help="co ile sekund sprawdzać katalog")
    ap.add_argument("--raz", action="store_true",
                    help="przelicz najnowszy zapis i zakończ")
    ap.add_argument("--bez-koloru", dest="kolor", action="store_false")
    args = ap.parse_args(argv)
    i18n.set_language(i18n.normalize(args.lang))

    bridge = HeadlessBridge(args.ruleset)
    latest = newest_save()
    if latest is None:
        print("Nie znalazłem żadnego zapisu w ~/.freeciv/saves", file=sys.stderr)
        return 2

    _print(summarize(bridge, latest), args.kolor)
    if args.raz:
        return 0

    print(f"\nNasłuchuję nowych zapisów (co {args.interval:.0f} s). Ctrl+C kończy.")
    done = threading.Event()
    watcher = SaveWatcher(
        lambda path: _print(summarize(bridge, path), args.kolor),
        interval=args.interval)
    try:
        done.wait()
    except KeyboardInterrupt:
        pass
    finally:
        watcher.stop()
    return 0
