"""Czytanie zapisow gry Freeciva.

Zapis (.sav, .sav.gz, .sav.xz, .sav.bz2, .sav.zst) to ten sam format secfile,
co pliki regul - uzywamy wiec tego samego parsera.

MGLA WOJNY. Zapis zawiera stan WSZYSTKICH graczy, takze to, czego twoja
cywilizacja nie widzi. Domyslnie pokazujemy wylacznie twoja wiedze:
 * twoje miasta i jednostki,
 * cudze miasta, ktore masz odkryte (tabela "dc" - dosl. discovered cities),
 * stosunki dyplomatyczne.
Pelny wglad (cudze jednostki, niewidziane miasta) trzeba wlaczyc swiadomie
parametrem pelny_wglad=True. Kazda odpowiedz mowi, w ktorym trybie powstala.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field

from .registry import parse_file

DEFAULT_SAVE_DIRS = [
    os.path.expanduser("~/.freeciv/saves"),
    os.path.expanduser("~/.local/share/freeciv/saves"),
]


# ------------------------------------------------------------- dekompresja

def _read_text(path: str) -> str:
    """Rozpakowuje zapis do tekstu, niezaleznie od uzytej kompresji."""
    with open(path, "rb") as fh:
        head = fh.read(6)
        fh.seek(0)
        blob = fh.read()

    if head[:4] == b"\x28\xb5\x2f\xfd":                       # zstd
        try:
            import zstandard
        except ImportError as exc:                            # pragma: no cover
            raise RuntimeError(
                "zapis jest spakowany zstd — zainstaluj moduł 'zstandard'") from exc
        blob = zstandard.ZstdDecompressor().decompress(
            blob, max_output_size=512 * 1024 * 1024)
    elif head[:2] == b"\x1f\x8b":                             # gzip
        import gzip
        blob = gzip.decompress(blob)
    elif head[:6] == b"\xfd7zXZ\x00":                         # xz
        import lzma
        blob = lzma.decompress(blob)
    elif head[:3] == b"BZh":                                  # bzip2
        import bz2
        blob = bz2.decompress(blob)
    return blob.decode("utf-8", errors="replace")


def find_saves(directories: list[str] | None = None) -> list[str]:
    """Zapisy posortowane od najnowszego."""
    out: list[str] = []
    for d in (directories or DEFAULT_SAVE_DIRS):
        if os.path.isdir(d):
            out.extend(glob.glob(os.path.join(d, "*.sav*")))
    return sorted(out, key=os.path.getmtime, reverse=True)


# ---------------------------------------------------------------- struktury

@dataclass
class Player:
    slot: int
    name: str
    nation: str
    human: bool
    alive: bool
    gold: int
    government: str
    ncities: int
    nunits: int
    diplomacy: dict[int, str] = field(default_factory=dict)


@dataclass
class City:
    name: str
    owner: int
    x: int
    y: int
    size: int
    mine: bool
    walls: bool = False
    occupied: bool | None = None
    capital: bool = False
    buildings: list[str] = field(default_factory=list)
    building_now: str | None = None
    shield_stock: int = 0


@dataclass
class Unit:
    type: str
    owner: int
    x: int
    y: int
    veteran: int
    hp: int
    homecity: int = 0
    activity: str = ""


class Save:
    """Wczytany zapis gry."""

    def __init__(self, path: str):
        self.path = path
        text = _read_text(path)
        tmp = None
        try:
            import tempfile
            with tempfile.NamedTemporaryFile("w", suffix=".sav", delete=False,
                                             encoding="utf-8") as fh:
                fh.write(text)
                tmp = fh.name
            self.reg = parse_file(tmp)
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

        sf = self.reg.get("savefile")
        game = self.reg.get("game")
        self.ruleset = sf.str("rulesetdir") if sf else "classic"
        self.version = sf.str("revision") if sf else ""
        self.turn = game.int("turn") if game else 0
        self.year = game.int("year") if game else 0
        self._improvements = sf.list("improvement_vector") if sf else []
        self._activities = sf.list("activities_vector") if sf else []

        self.players: dict[int, Player] = {}
        self._sections: dict[int, object] = {}
        for sec in self.reg.sections:
            if not sec.name.startswith("player") or not sec.name[6:].isdigit():
                continue
            slot = int(sec.name[6:])
            flags = sec.list("flags")
            self._sections[slot] = sec
            self.players[slot] = Player(
                slot=slot,
                name=sec.str("name"),
                nation=sec.str("nation"),
                human="ai" not in [str(f) for f in flags],
                alive=sec.bool("is_alive", True),
                gold=sec.int("gold"),
                government=sec.str("government_name"),
                ncities=sec.int("ncities"),
                nunits=sec.int("nunits"),
            )

        for slot, sec in self._sections.items():
            tbl = sec.table("diplstate")
            if tbl:
                self.players[slot].diplomacy = {
                    i: str(row.get("current")) for i, row in enumerate(tbl.dicts())}

        humans = [p for p in self.players.values() if p.human and p.alive]
        self.me: Player | None = humans[0] if humans else None

    # ------------------------------------------------------------ pomocnicze

    def _bits(self, blob) -> list[str]:
        s = str(blob or "")
        return [name for i, name in enumerate(self._improvements)
                if i < len(s) and s[i] == "1"]

    def _activity(self, code) -> str:
        try:
            return self._activities[int(code)]
        except (ValueError, TypeError, IndexError):
            return str(code)

    def nation_slot(self, nation: str) -> int | None:
        """Znajduje gracza po nazwie nacji albo przywodcy (bez wielkosci liter)."""
        needle = nation.strip().lower()
        for p in self.players.values():
            if needle in (p.nation.lower(), p.name.lower()):
                return p.slot
        for p in self.players.values():
            if needle and (needle in p.nation.lower() or needle in p.name.lower()):
                return p.slot
        return None

    # ----------------------------------------------------------------- dane

    def cities_of(self, slot: int) -> list[City]:
        """Miasta z sekcji gracza - pelne dane (dla obcych to pelny wglad)."""
        sec = self._sections.get(slot)
        tbl = sec.table("c") if sec else None
        if not tbl:
            return []
        out = []
        for row in tbl.dicts():
            out.append(City(
                name=str(row.get("name") or "?"),
                owner=slot, x=int(row.get("x") or 0), y=int(row.get("y") or 0),
                size=int(row.get("size") or 0),
                mine=(self.me is not None and slot == self.me.slot),
                buildings=self._bits(row.get("improvements")),
                building_now=str(row.get("currently_building_name") or "") or None,
                shield_stock=int(row.get("shield_stock") or 0),
            ))
        for c in out:
            c.walls = any(b.startswith("City Walls") or b == "Force Walls"
                          for b in c.buildings)
        return out

    def known_cities(self) -> list[City]:
        """Cudze miasta, ktore gracz ma odkryte - to jest wiedza z mgly wojny."""
        if self.me is None:
            return []
        sec = self._sections.get(self.me.slot)
        tbl = sec.table("dc") if sec else None
        if not tbl:
            return []
        out = []
        for row in tbl.dicts():
            out.append(City(
                name=str(row.get("name") or "?"),
                owner=int(row.get("owner") or -1),
                x=int(row.get("x") or 0), y=int(row.get("y") or 0),
                size=int(row.get("size") or 0),
                mine=False,
                walls=bool(row.get("walls")),
                occupied=bool(row.get("occupied")),
                capital=str(row.get("capital") or "Not") != "Not",
                buildings=self._bits(row.get("improvements")),
            ))
        return out

    def units_of(self, slot: int) -> list[Unit]:
        sec = self._sections.get(slot)
        tbl = sec.table("u") if sec else None
        if not tbl:
            return []
        return [Unit(
            type=str(row.get("type_by_name") or "?"),
            owner=slot, x=int(row.get("x") or 0), y=int(row.get("y") or 0),
            veteran=int(row.get("veteran") or 0), hp=int(row.get("hp") or 0),
            homecity=int(row.get("homecity") or 0),
            activity=self._activity(row.get("activity")),
        ) for row in tbl.dicts()]


# ------------------------------------------------------------------ wywiad

def _distance(a: tuple[int, int], b: tuple[int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


class Intel:
    """Odpowiada na pytania o partie, respektujac (albo nie) mgle wojny."""

    def __init__(self, save: Save):
        self.save = save

    # --------------------------------------------------------------- ogolne

    def summary(self, full: bool) -> dict:
        s = self.save
        me = s.me
        out = {
            "plik": os.path.basename(s.path),
            "tura": s.turn, "rok": s.year,
            "zestaw_regul": s.ruleset,
            "wersja_gry": s.version,
            "tryb_wywiadu": "pełny wgląd (świadome chity)" if full
            else "tylko moja wiedza (mgła wojny)",
        }
        if me is None:
            out["blad"] = "nie znalazłem gracza ludzkiego w tym zapisie"
            return out

        my_cities = s.cities_of(me.slot)
        my_units = s.units_of(me.slot)
        out["ja"] = {
            "przywodca": me.name, "nacja": me.nation,
            "ustroj": me.government, "zloto": me.gold,
            "miast": len(my_cities), "jednostek": len(my_units),
        }
        out["dyplomacja"] = [
            {"nacja": p.nation, "przywodca": p.name, "stan": me.diplomacy.get(p.slot, "?"),
             "zywy": p.alive,
             "znane_miasta": sum(1 for c in s.known_cities() if c.owner == p.slot)}
            for p in sorted(s.players.values(), key=lambda x: x.slot)
            if p.slot != me.slot and p.alive
        ]
        if full:
            for row in out["dyplomacja"]:
                slot = s.nation_slot(row["nacja"])
                if slot is not None:
                    row["miast_naprawde"] = s.players[slot].ncities
                    row["jednostek_naprawde"] = s.players[slot].nunits
                    row["zloto"] = s.players[slot].gold
        return out

    # ----------------------------------------------------------------- armia

    def my_army(self) -> dict:
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        units = s.units_of(s.me.slot)
        by_type: dict[str, dict] = {}
        for u in units:
            e = by_type.setdefault(u.type, {"jednostka": u.type, "sztuk": 0,
                                            "stopnie": {}, "ranne": 0})
            e["sztuk"] += 1
            e["stopnie"][u.veteran] = e["stopnie"].get(u.veteran, 0) + 1
            if u.hp < 10:
                e["ranne"] += 1
        cities = s.cities_of(s.me.slot)
        building: dict[str, int] = {}
        for c in cities:
            if c.building_now:
                building[c.building_now] = building.get(c.building_now, 0) + 1
        return {
            "razem_jednostek": len(units),
            "wg_typu": sorted(by_type.values(), key=lambda e: -e["sztuk"]),
            "miast": len(cities),
            "co_buduja_miasta": sorted(
                ({"co": k, "w_ilu_miastach": v} for k, v in building.items()),
                key=lambda e: -e["w_ilu_miastach"]),
        }

    # ------------------------------------------------------------ przeciwnik

    def nation(self, name: str, full: bool) -> dict:
        s = self.save
        slot = s.nation_slot(name)
        if slot is None:
            return {"blad": f"nie ma nacji {name} w tym zapisie",
                    "dostepne": sorted(p.nation for p in s.players.values() if p.alive)}
        p = s.players[slot]
        me = s.me
        known = [c for c in s.known_cities() if c.owner == slot]
        out = {
            "nacja": p.nation, "przywodca": p.name, "zywy": p.alive,
            "stan_dyplomatyczny": me.diplomacy.get(slot, "?") if me else "?",
            "tryb_wywiadu": "pełny wgląd (świadome chity)" if full
            else "tylko moja wiedza (mgła wojny)",
            "znane_miasta": [{
                "nazwa": c.name, "x": c.x, "y": c.y, "rozmiar": c.size,
                "mury": c.walls, "obsadzone": c.occupied, "stolica": c.capital,
                "budowle": c.buildings,
            } for c in sorted(known, key=lambda c: -c.size)],
        }
        if not full:
            out["czego_nie_wiem"] = (
                "liczebność i rozmieszczenie ich wojsk, miasta jeszcze nieodkryte, "
                "ich złoto i technologie")
            return out

        out["zloto"] = p.gold
        out["ustroj"] = p.government
        cities = s.cities_of(slot)
        units = s.units_of(slot)
        out["wszystkie_miasta"] = [{
            "nazwa": c.name, "x": c.x, "y": c.y, "rozmiar": c.size,
            "mury": c.walls, "buduje": c.building_now, "budowle": c.buildings,
        } for c in sorted(cities, key=lambda c: -c.size)]
        by_type: dict[str, int] = {}
        for u in units:
            by_type[u.type] = by_type.get(u.type, 0) + 1
        out["wszystkie_wojska"] = sorted(
            ({"jednostka": k, "sztuk": v} for k, v in by_type.items()),
            key=lambda e: -e["sztuk"])
        # co stoi w ktorym miescie
        garrisons: dict[str, list[str]] = {}
        for c in cities:
            here = [u for u in units if (u.x, u.y) == (c.x, c.y)]
            if here:
                garrisons[c.name] = sorted(f"{u.type} (wet {u.veteran}, {u.hp} hp)"
                                           for u in here)
        out["garnizony"] = garrisons
        return out

    # --------------------------------------------------------------- dystans

    def front(self, target: str, full: bool, max_distance: int = 12) -> dict:
        """Ktore moje miasta i jednostki sa najblizej celu."""
        s = self.save
        slot = s.nation_slot(target)
        if slot is None or s.me is None:
            return {"blad": f"nie ma nacji {target}"}
        targets = ([(c.name, c.x, c.y, c.size, c.walls)
                    for c in s.cities_of(slot)] if full else
                   [(c.name, c.x, c.y, c.size, c.walls)
                    for c in s.known_cities() if c.owner == slot])
        if not targets:
            return {"blad": f"nie znam żadnego miasta nacji {target}"}

        my_cities = s.cities_of(s.me.slot)
        my_units = s.units_of(s.me.slot)
        rows = []
        for name, x, y, size, walls in targets:
            near_c = sorted(((_distance((x, y), (c.x, c.y)), c) for c in my_cities),
                            key=lambda t: t[0])[:3]
            near_u = [u for u in my_units if _distance((x, y), (u.x, u.y)) <= max_distance]
            by_type: dict[str, int] = {}
            for u in near_u:
                by_type[u.type] = by_type.get(u.type, 0) + 1
            rows.append({
                "miasto_celu": name, "x": x, "y": y, "rozmiar": size, "mury": walls,
                "moje_najblizsze_miasta": [
                    {"nazwa": c.name, "dystans": d, "x": c.x, "y": c.y}
                    for d, c in near_c],
                "moje_jednostki_w_zasiegu": sorted(
                    ({"jednostka": k, "sztuk": v} for k, v in by_type.items()),
                    key=lambda e: -e["sztuk"]),
                "jednostek_w_zasiegu": len(near_u),
            })
        return {"cel": s.players[slot].nation,
                "tryb_wywiadu": "pełny wgląd (świadome chity)" if full
                else "tylko moja wiedza (mgła wojny)",
                "promien_zasiegu": max_distance,
                "fronty": sorted(rows, key=lambda r: -r["rozmiar"])}


# --------------------------------------------------------- wspolne narzedzia

class IntelMixin:
    """Narzedzia wywiadu wspolne dla okna aplikacji i trybu bez Qt.

    Klasa uzywajaca musi udostepnic metode `_intel_apply_ruleset(nazwa)`.
    """

    _intel: Intel | None = None
    _intel_full: bool = False

    def _load_save(self, path: str | None) -> Intel:
        if path:
            target = os.path.expanduser(path)
        else:
            found = find_saves()
            if not found:
                raise FileNotFoundError(
                    "nie znalazłem żadnego zapisu w ~/.freeciv/saves")
            target = found[0]
        self._intel = Intel(Save(target))
        return self._intel

    def _need_intel(self) -> Intel:
        if self._intel is None:
            return self._load_save(None)
        return self._intel

    def ai_savegame(self, args: dict) -> dict:
        full = bool(args.get("pelny_wglad"))
        intel = self._load_save(args.get("sciezka"))
        self._intel_full = full
        out = intel.summary(full)
        ruleset = intel.save.ruleset
        try:
            applied = self._intel_apply_ruleset(ruleset)
            out["zestaw_regul_ustawiony"] = applied
        except Exception as exc:  # noqa: BLE001
            out["zestaw_regul_ustawiony"] = f"nie udało się: {exc}"
        return out

    def ai_army(self, args: dict) -> dict:
        return self._need_intel().my_army()

    def ai_nation(self, args: dict) -> dict:
        full = bool(args.get("pelny_wglad", self._intel_full))
        return self._need_intel().nation(str(args.get("nacja", "")), full)

    def ai_governments(self, args: dict) -> dict:
        rs = self._intel_ruleset()
        govs = args.get("ustroje") or None
        if govs is not None:
            govs = [str(g) for g in govs]
        return government_comparison(rs, self._intel, govs)

    def ai_front(self, args: dict) -> dict:
        full = bool(args.get("pelny_wglad", self._intel_full))
        return self._need_intel().front(
            str(args.get("nacja", "")), full,
            int(args.get("promien") or 12))


# ------------------------------------------------------------------ ustroje

# Efekty, ktore realnie decyduja o oplacalnosci ustroju. Wartosci czytamy
# z regul - nic nie jest tu zaszyte na sztywno poza doborem, co pokazac.
GOV_EFFECTS = {
    "Max_Rates": ("maks. suwak podatków/nauki (%)", "wyżej lepiej"),
    "Empire_Size_Base": ("próg wielkości imperium", "wyżej lepiej"),
    "Empire_Size_Step": ("co ile miast kolejna kara", "wyżej lepiej"),
    "Martial_Law_Each": ("stan wojenny: ilu niezadowolonych uspokaja jednostka",
                         "wyżej lepiej"),
    "Martial_Law_Max": ("stan wojenny: maks. jednostek", "wyżej lepiej"),
    "Make_Content_Mil": ("darmowe jednostki w polu (bez niezadowolenia)",
                         "wyżej lepiej"),
    "Revolution_Unhappiness": ("niezadowolenie za każdą jednostkę w polu",
                               "niżej lepiej"),
    "Unit_Upkeep_Free_Per_City": ("darmowe utrzymanie jednostek na miasto",
                                  "wyżej lepiej"),
    "Upkeep_Factor": ("mnożnik utrzymania jednostki", "niżej lepiej"),
    "Output_Waste": ("marnotrawstwo produkcji/handlu (%)", "niżej lepiej"),
    "Output_Bonus": ("premia do wytwarzania (%)", "wyżej lepiej"),
    "Output_Inc_Tile": ("dodatkowy surowiec z kafla", "wyżej lepiej"),
    "Civil_War_Chance": ("ryzyko wojny domowej (%)", "niżej lepiej"),
    "Happiness_To_Gold": ("budynki szczęścia dają złoto zamiast zadowolenia", ""),
    "Fanatics": ("dostęp do fanatyków (bez utrzymania)", ""),
}


def _gov_effect_values(rs, gov: str) -> dict[str, list[dict]]:
    """Wartosci efektow obowiazujace pod danym ustrojem.

    Uwzglednia zarowno efekty wymagajace tego ustroju, jak i te, ktore
    wykluczaja inne ustroje (czyli obowiazuja rowniez ten).
    """
    all_govs = set(rs.governments)
    out: dict[str, list[dict]] = {}
    for eff in rs.effects:
        if eff.type not in GOV_EFFECTS:
            continue
        pos = [r.name for r in eff.reqs if r.type == "Gov" and r.present]
        neg = [r.name for r in eff.reqs if r.type == "Gov" and not r.present]
        if pos and gov not in pos:
            continue
        if neg and gov in neg:
            continue
        if not pos and not neg:
            continue                      # efekt globalny, nie rozroznia ustrojow
        if neg and not pos and not (all_govs - set(neg)):
            continue
        extra = [f"{r.type}:{r.name}" for r in eff.reqs
                 if r.type not in ("Gov",)]
        out.setdefault(eff.type, []).append(
            {"wartosc": eff.value, "warunki": extra})
    return out


def government_comparison(rs, intel: "Intel | None", govs: list[str] | None = None
                          ) -> dict:
    """Porownuje ustroje wg regul, a jesli jest wczytany zapis - takze wg
    konkretnej sytuacji gracza (liczba miast, jednostek, znane technologie)."""
    import collections

    names = govs or list(rs.governments)
    unknown = [g for g in names if g not in rs.governments]
    names = [g for g in names if g in rs.governments]
    out: dict = {"zestaw_regul": rs.name, "ustroje": {}}
    if unknown:
        out["nieznane_ustroje"] = unknown

    # jakiej technologii wymaga ustroj
    reqs: dict[str, list[str]] = {}
    from .registry import parse_file
    import os
    path = os.path.join(rs.path, "governments.ruleset")
    if os.path.exists(path):
        reg = parse_file(path, base_dir=os.path.dirname(rs.path))
        for sec in reg.prefixed("government_"):
            from .model import clean_name
            gname = clean_name(sec.str("name"))
            tbl = sec.table("reqs")
            reqs[gname] = [str(r.get("name")) for r in tbl.dicts()
                           if str(r.get("type", "")).lower() == "tech"] if tbl else []

    for g in names:
        entry: dict = {"wymaga_technologii": reqs.get(g, []),
                       "efekty": {}}
        for etype, rows in sorted(_gov_effect_values(rs, g).items()):
            opis, kierunek = GOV_EFFECTS[etype]
            entry["efekty"][etype] = {"opis": opis, "kierunek": kierunek,
                                      "wartosci": rows}
        out["ustroje"][g] = entry

    if intel is None or intel.save.me is None:
        out["uwaga"] = ("brak wczytanego zapisu — porównanie jest ogólne, "
                        "bez liczb z twojej partii")
        return out

    save = intel.save
    me = save.me
    units = save.units_of(me.slot)
    sec = save._sections[me.slot]
    tbl = sec.table("c")
    city_ids = {int(r["id"]) for r in tbl.dicts()} if tbl else set()
    by_home = collections.Counter(u.homecity for u in units)

    known = set()
    research = save.reg.get("research")
    sf = save.reg.get("savefile")
    if research and sf:
        rtbl = research.table("r")
        vector = sf.list("technology_vector")
        if rtbl and len(rtbl):
            done = str(list(rtbl.dicts())[0].get("done") or "")
            known = {vector[i] for i, ch in enumerate(done)
                     if ch == "1" and i < len(vector)}

    out["moja_sytuacja"] = {
        "tura": save.turn, "obecny_ustroj": me.government,
        "miast": len(city_ids), "jednostek": len(units),
        "zloto": me.gold,
    }

    for g, entry in out["ustroje"].items():
        need = entry["wymaga_technologii"]
        entry["dostepny_teraz"] = all(t in known for t in need) if known else None
        entry["brakujace_technologie"] = [t for t in need if t not in known] \
            if known else []

        eff = entry["efekty"]

        def first(etype: str, default: int = 0) -> int:
            rows = eff.get(etype, {}).get("wartosci", [])
            plain = [r["wartosc"] for r in rows if not r["warunki"]]
            return plain[0] if plain else default

        free = 0
        factor = 0
        koszt_typ = "—"
        for row in eff.get("Unit_Upkeep_Free_Per_City", {}).get("wartosci", []):
            for w in row["warunki"]:
                if w.startswith("OutputType:"):
                    free = row["wartosc"]
                    koszt_typ = w.split(":", 1)[1]
        for row in eff.get("Upkeep_Factor", {}).get("wartosci", []):
            for w in row["warunki"]:
                if w == f"OutputType:{koszt_typ}":
                    factor += row["wartosc"]
        if koszt_typ != "—" and city_ids:
            koszt = sum(max(0, by_home.get(c, 0) - free) * max(1, factor)
                        for c in city_ids)
            entry["utrzymanie_wojsk"] = {
                "darmowych_na_miasto": free,
                "mnoznik": max(1, factor),
                "placisz_w": {"Gold": "złocie", "Shield": "tarczach"}.get(
                    koszt_typ, koszt_typ),
                "koszt_na_ture": koszt,
            }

        base = first("Empire_Size_Base")
        step = first("Empire_Size_Step", base)
        if base:
            n = len(city_ids)
            kary = 0 if n <= base else 1 + (n - base - 1) // max(1, step)
            entry["kara_za_wielkosc"] = {
                "prog": base, "krok": step,
                "poziomow_kary_przy_twoich_miastach": kary,
                "miast_do_kolejnej_kary": max(0, base + kary * step - n + 1)
                if n > base else base - n + 1,
            }
    return out
