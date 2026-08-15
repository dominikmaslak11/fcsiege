"""Wpisywanie kluczy API z terminala: `fcsiege.py klucz`.

Klucz czytamy przez getpass, wiec nie trafia ani na ekran, ani do historii
powloki. Zapis idzie do pliku z prawami 0600.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from . import providers


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="fcsiege.py klucz",
        description="Zapisuje klucz API wybranego dostawcy.")
    ap.add_argument("dostawca", nargs="?", choices=sorted(providers.PROVIDERS),
                    help="pominięty = pokaż stan wszystkich")
    ap.add_argument("--model", default="", help="model domyślny dla dostawcy")
    ap.add_argument("--usun", action="store_true", help="zapomnij klucz")
    ap.add_argument("--aktywny", action="store_true",
                    help="ustaw tego dostawcę jako domyślnego")
    args = ap.parse_args(argv)

    if not args.dostawca:
        st = providers.status()
        print(f"Aktywny: {st['aktywny']}   (plik: {st['plik']})\n")
        for d in st["dostawcy"]:
            znak = "✓" if d["ma_klucz"] else "·"
            skad = {"env": f"ze zmiennej {d['zmienne_srodowiskowe'][0]}",
                    "plik": "z pliku", "brak": "brak klucza"}[d["skad_klucz"]]
            print(f" {znak} {d['dostawca']:<10}{d['nazwa']:<24}{skad:<26}"
                  f"model: {d['model']}")
        print("\nAby zapisać klucz:  fcsiege.py klucz <dostawca>")
        return 0

    p = providers.PROVIDERS[args.dostawca]
    if args.usun:
        providers.forget_key(args.dostawca)
        print(f"Klucz {p.label} usunięty.")
        return 0

    print(f"{p.label}   (format: {p.key_hint or '—'})")
    print(f"Klucz weźmiesz stąd: {p.console}")
    try:
        klucz = getpass.getpass("Klucz (nie pojawi się na ekranie): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nPrzerwano.", file=sys.stderr)
        return 1
    if not klucz:
        print("Nic nie wpisano — nic nie zmieniam.", file=sys.stderr)
        return 1
    providers.save_key(args.dostawca, klucz, args.model)
    if args.aktywny:
        providers.set_active(args.dostawca)
    print(f"Zapisano w {providers.CRED_FILE} (prawa 0600)."
          + (f" Ustawiono jako aktywnego." if args.aktywny else ""))
    return 0
