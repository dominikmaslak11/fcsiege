"""Obserwator katalogu z zapisami gry.

Freeciv zapisuje partie automatycznie co ture, wiec pojawienie sie nowego pliku
jest dobrym sygnalem "gracz skonczyl ture". Pilnujemy katalogu i po kazdej
zmianie przeliczamy ostrzezenia, zeby aplikacja odzywala sie sama, zamiast
czekac, az ktos ja o cos zapyta.

Swiadomie odpytujemy katalog zamiast uzywac inotify: zaleznosci maja byc zerowe,
a raz na kilka sekund to i tak czesciej, niz trwa tura. Czekamy tez, az plik
przestanie rosnac - inaczej trafiliby smy na zapis w polowie zapisywania.
"""

from __future__ import annotations

import glob
import os
import threading
import time

DEFAULT_DIRS = ("~/.freeciv/saves",)
PATTERNS = ("*.sav", "*.sav.gz", "*.sav.bz2", "*.sav.xz", "*.sav.zst")


def save_dirs() -> list[str]:
    env = os.environ.get("FCSIEGE_SAVES")
    dirs = ([env] if env else []) + [os.path.expanduser(d) for d in DEFAULT_DIRS]
    return [d for d in dirs if os.path.isdir(d)]


def newest_save(dirs: list[str] | None = None) -> str | None:
    """Najswiezszy zapis wg czasu modyfikacji, nie wg nazwy."""
    found: list[str] = []
    for d in dirs or save_dirs():
        for pat in PATTERNS:
            found.extend(glob.glob(os.path.join(d, pat)))
    if not found:
        return None
    return max(found, key=os.path.getmtime)


def _settled(path: str, tries: int = 6, pause: float = 0.25) -> bool:
    """Czy plik przestal rosnac - zapis moze byc jeszcze w toku."""
    last = -1
    for _ in range(tries):
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        if size == last and size > 0:
            return True
        last = size
        time.sleep(pause)
    return last > 0


class SaveWatcher:
    """Wola `on_change(sciezka)` przy kazdym nowym zapisie.

    Startowego pliku nie zglasza - dopiero zmiane, zeby uruchomienie aplikacji
    nie wygladalo jak koniec tury.
    """

    def __init__(self, on_change, interval: float = 3.0,
                 dirs: list[str] | None = None, announce_first: bool = False):
        self._on_change = on_change
        self._interval = max(0.5, interval)
        self._dirs = dirs
        self._stop = threading.Event()
        self._seen: tuple[str, float] | None = None
        if not announce_first:
            path = newest_save(self._dirs)
            if path:
                self._seen = (path, os.path.getmtime(path))
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="fcsiege-watch")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                path = newest_save(self._dirs)
                if path is None:
                    continue
                stamp = os.path.getmtime(path)
                if self._seen is not None and (path, stamp) == self._seen:
                    continue
                if not _settled(path):
                    continue
                self._seen = (path, os.path.getmtime(path))
                self._on_change(path)
            except Exception:  # noqa: BLE001 - watek ma przezyc kazdy blad
                continue

    def stop(self) -> None:
        self._stop.set()


def summarize(bridge, path: str, lang: str = "pl") -> dict:
    """Wczytuje zapis i zwraca to, co warto pokazac bez pytania.

    Bierzemy stan partii i ostrzezenia - reszte gracz doczyta sam, jesli
    zechce. Celowo nie wolamy tu ciezkich narzedzi (mobilnosc, szlaki), zeby
    zmiana tury nie kosztowala sekund.
    """
    from .aitools import dispatch

    out: dict = {"plik": os.path.basename(path)}
    try:
        stan = dispatch(bridge, "wczytaj_zapis", {"sciezka": path})
        out["stan"] = {k: stan.get(k) for k in
                       ("tura", "rok", "zestaw_regul", "turn", "year", "ruleset")
                       if stan.get(k) is not None}
        me = stan.get("ja") or stan.get("me") or {}
        out["ja"] = {k: me.get(k) for k in
                     ("zloto", "miast", "jednostek", "gold", "cities", "units")
                     if me.get(k) is not None}
    except Exception as exc:  # noqa: BLE001
        return {**out, "blad": f"{type(exc).__name__}: {exc}"}
    try:
        alerty = dispatch(bridge, "alerty", {})
        out["alerty"] = alerty.get("alerty", alerty.get("alerts", []))[:40]
        out["podsumowanie"] = {k: alerty.get(k) for k in
                               ("alertow", "krytycznych", "pilnych",
                                "alerts_count", "critical", "urgent")
                               if alerty.get(k) is not None}
    except Exception as exc:  # noqa: BLE001
        out["alerty_blad"] = f"{type(exc).__name__}: {exc}"
    return out
