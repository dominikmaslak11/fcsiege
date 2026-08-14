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


# ----------------------------------------------------------- mapa i ruch

class TerrainMap:
    """Teren i ulepszenia z zapisu - potrzebne do liczenia przejezdnosci."""

    def __init__(self, save: "Save"):
        m = save.reg.get("map")
        sf = save.reg.get("savefile")
        self._m = m
        ti = sf.table("terrident") if sf else None
        self.ident = {str(r["identifier"]): str(r["name"]) for r in ti} if ti else {}
        self.extras = {n: i for i, n in enumerate(sf.list("extras_vector"))} if sf else {}
        self.rows = []
        y = 0
        while True:
            row = m.get(f"t{y:04d}") if m else None
            if row is None:
                break
            self.rows.append(str(row))
            y += 1
        self.height = len(self.rows)
        self.width = max((len(r) for r in self.rows), default=0)

    def terrain(self, x: int, y: int) -> str | None:
        if not (0 <= y < self.height) or x >= len(self.rows[y]) or x < 0:
            return None
        return self.ident.get(self.rows[y][x])

    def has_extra(self, name: str, x: int, y: int) -> bool:
        i = self.extras.get(name)
        if i is None:
            return False
        row = str(self._m.get(f"e{i // 4:02d}_{y:04d}") or "")
        if x >= len(row) or x < 0:
            return False
        try:
            return bool(int(row[x], 16) & (1 << (i % 4)))
        except ValueError:
            return False

    def has_road(self, x: int, y: int) -> bool:
        """Droga, kolej, maglev albo rzeka - wszystkie sa 'NativeTile'."""
        return any(self.has_extra(n, x, y)
                   for n in ("Road", "Railroad", "Maglev", "River"))


def passability(rs, tmap: TerrainMap, uclass: str):
    """Zwraca funkcje (x,y)->bool: czy jednostka tej klasy moze tam stanac.

    Kafel jest przejezdny, gdy teren jest natywny dla klasy albo lezy na nim
    ulepszenie z flaga NativeTile (droga, kolej, rzeka).
    """
    native = {t.name for t in rs.terrains.values() if uclass in t.native_to}

    def ok(x: int, y: int) -> bool:
        t = tmap.terrain(x, y)
        if t is None:
            return False
        terr = rs.terrains.get(t)
        if terr is None or not terr.is_land:
            return False
        return t in native or tmap.has_road(x, y)

    return ok


def road_link(rs, tmap: TerrainMap, passable, start: tuple[int, int],
              target_region: int, reg: dict, max_nodes: int = 200000) -> dict | None:
    """Najtansze polaczenie drogowe z kieszeni do wskazanego obszaru.

    Koszt kafla to liczba tur pracy potrzebnych, zeby zbudowac tam droge
    (road_time z regul); kafle juz przejezdne kosztuja zero.
    """
    import heapq
    road_time = {t.name: getattr(t, "road_time", 0) for t in rs.terrains.values()}
    W, H = tmap.width, tmap.height

    def cost(x: int, y: int):
        t = tmap.terrain(x, y)
        if t is None:
            return None
        terr = rs.terrains.get(t)
        if terr is None or not terr.is_land:
            return None
        if passable(x, y):
            return 0
        return road_time.get(t) or 4

    dist = {start: 0}
    prev: dict = {}
    pq = [(0, start)]
    seen = 0
    goal = None
    while pq and seen < max_nodes:
        d, (x, y) = heapq.heappop(pq)
        seen += 1
        if d > dist.get((x, y), 1 << 30):
            continue
        if reg.get((x, y)) == target_region:
            goal = (x, y)
            break
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                if dx == dy == 0:
                    continue
                nx, ny = (x + dx) % W, y + dy
                if not (0 <= ny < H):
                    continue
                c = cost(nx, ny)
                if c is None:
                    continue
                nd = d + c
                if nd < dist.get((nx, ny), 1 << 30):
                    dist[(nx, ny)] = nd
                    prev[(nx, ny)] = (x, y)
                    heapq.heappush(pq, (nd, (nx, ny)))
    if goal is None:
        return None
    path = [goal]
    while path[-1] in prev:
        path.append(prev[path[-1]])
    path.reverse()
    todo = [{"x": x, "y": y, "teren": tmap.terrain(x, y),
             "tur_pracy": road_time.get(tmap.terrain(x, y)) or 4}
            for x, y in path if not passable(x, y)]
    total = sum(t["tur_pracy"] for t in todo)
    return {"kafli_do_zbudowania": len(todo), "lacznie_tur_pracy": total,
            "kafle": todo,
            "robotnikow_na_jedna_ture": total,
            "przy_8_robotnikach_tur": max(1, -(-total // 8)) if total else 0}


def regions(tmap: TerrainMap, passable) -> dict[tuple[int, int], int]:
    """Spojne obszary przejezdne (8-kierunkowo, mapa zawinieta w poziomie)."""
    seen: dict[tuple[int, int], int] = {}
    cid = 0
    W, H = tmap.width, tmap.height
    for y in range(H):
        for x in range(len(tmap.rows[y])):
            if (x, y) in seen or not passable(x, y):
                continue
            cid += 1
            stack = [(x, y)]
            seen[(x, y)] = cid
            while stack:
                cx, cy = stack.pop()
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        if dx == dy == 0:
                            continue
                        nx, ny = (cx + dx) % W, cy + dy
                        if 0 <= ny < H and (nx, ny) not in seen and passable(nx, ny):
                            seen[(nx, ny)] = cid
                            stack.append((nx, ny))
    return seen


def _upkeep_profile(rs, gov: str) -> tuple[str, int, int]:
    """(typ kosztu, darmowe na miasto, mnoznik) dla danego ustroju."""
    all_govs = set(rs.governments)
    out_type, free, factor = "Gold", 0, 0
    for eff in rs.effects_by_type.get("Unit_Upkeep_Free_Per_City", []):
        govs = [r.name for r in eff.reqs if r.type == "Gov" and r.present]
        types = [r.name for r in eff.reqs if r.type == "OutputType"]
        if gov in govs and types:
            out_type, free = types[0], eff.value
    for eff in rs.effects_by_type.get("Upkeep_Factor", []):
        pos = [r.name for r in eff.reqs if r.type == "Gov" and r.present]
        neg = [r.name for r in eff.reqs if r.type == "Gov" and not r.present]
        types = [r.name for r in eff.reqs if r.type == "OutputType"]
        if types and types[0] != out_type:
            continue
        if pos and gov not in pos:
            continue
        if neg and gov in neg:
            continue
        if not pos and not neg:
            continue
        if neg and not pos and not (all_govs - set(neg)):
            continue
        factor += eff.value
    return out_type, free, max(1, factor)


def _known_techs(save: "Save") -> set[str]:
    research = save.reg.get("research")
    sf = save.reg.get("savefile")
    if not research or not sf:
        return set()
    tbl = research.table("r")
    vector = sf.list("technology_vector")
    if not tbl or not len(tbl):
        return set()
    done = str(list(tbl.dicts())[0].get("done") or "")
    return {vector[i] for i, ch in enumerate(done) if ch == "1" and i < len(vector)}


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

    # ---------------------------------------------------------- handel

    def _continents(self) -> dict[tuple[int, int], int]:
        tmap = TerrainMap(self.save)
        ocean = {"Ocean", "Deep Ocean", "Lake", "Inaccessible"}

        def land(x, y):
            t = tmap.terrain(x, y)
            return t is not None and t not in ocean

        return regions(tmap, land)

    def trade_routes(self, rs, limit: int = 15, full: bool = False,
                     only_overseas: bool = False) -> dict:
        """Proponuje szlaki handlowe wg regul danego zestawu.

        Typ trasy (krajowa / zagraniczna / miedzykontynentalna) decyduje
        o procencie wartosci - w wielu zestawach trasy miedzy wlasnymi
        miastami daja 0%, a miedzykontynentalne podwojna stawke.
        """
        import os
        import collections
        from .registry import parse_file
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}

        # zasady handlu z regul
        pct: dict[str, int] = {}
        bonus: dict[str, str] = {}
        path = os.path.join(rs.path, "game.ruleset")
        if os.path.exists(path):
            g = parse_file(path, base_dir=os.path.dirname(rs.path))
            sec = g.get("trade")
            tbl = sec.table("settings") if sec else None
            for r in (tbl.dicts() if tbl else []):
                pct[str(r.get("type"))] = int(r.get("pct") or 0)
                bonus[str(r.get("type"))] = str(r.get("bonus") or "")

        max_routes = 0
        for eff in rs.effects_by_type.get("Max_Trade_Routes", []):
            if not eff.reqs:
                max_routes = max(max_routes, eff.value)

        st = s.reg.get("settings")
        stbl = st.table("set") if st else None
        settings = {str(r.get("name")): r.get("value")
                    for r in (stbl.dicts() if stbl else [])}
        mindist = int(settings.get("trademindist") or 9)

        cont = self._continents()
        my = s.cities_of(s.me.slot)
        foreign = s.known_cities()
        if full:
            seen = {(c.x, c.y) for c in foreign}
            for p in s.players.values():
                if p.slot == s.me.slot:
                    continue
                for c in s.cities_of(p.slot):
                    if (c.x, c.y) not in seen:
                        foreign.append(c)

        nations = {p.slot: p.nation for p in s.players.values()}
        dipl = s.me.diplomacy

        # ile slotow juz zajete
        sec_me = s._sections[s.me.slot]
        ctbl = sec_me.table("c")
        used = collections.Counter()
        for r in (ctbl.dicts() if ctbl else []):
            n = 0
            for i in range(8):
                v = r.get(f"traderoute{i}")
                if v not in (None, 0, "0"):
                    n += 1
            used[str(r.get("name"))] = n

        def route_type(fc) -> str:
            same_cont = cont.get((fc.x, fc.y)) == cont.get(("__", "__"))
            return ""

        def dist(a, b) -> int:
            return max(abs(a[0] - b[0]), abs(a[1] - b[1]))

        cands = []
        for mc in my:
            mc_cont = cont.get((mc.x, mc.y))
            for fc in foreign:
                ic = cont.get((fc.x, fc.y)) != mc_cont
                rel = dipl.get(fc.owner, "?")
                if rel in ("Alliance",):
                    kind = "AllyIC" if ic else "Ally"
                elif rel in ("War",):
                    kind = "EnemyIC" if ic else "Enemy"
                elif rel == "Team":
                    kind = "TeamIC" if ic else "Team"
                else:
                    kind = "INIC" if ic else "IN"
                value_pct = pct.get(kind, 0)
                if value_pct <= 0:
                    continue
                if only_overseas and not ic:
                    continue
                d = dist((mc.x, mc.y), (fc.x, fc.y))
                if d < mindist:
                    continue
                # przyblizenie klasycznego wzoru: (dystans+10) * (handel obu
                # miast) / 24; handlu miast nie ma w zapisie, wiec bierzemy
                # rozmiar jako przyblizenie
                score = (d + 10) * (mc.size + fc.size) * value_pct // 2400
                cands.append({
                    "moje_miasto": mc.name, "rozmiar": mc.size,
                    "partner": fc.name, "nacja": nations.get(fc.owner, "?"),
                    "rozmiar_partnera": fc.size,
                    "stan_dyplomatyczny": rel,
                    "typ_trasy": kind, "procent_wartosci": value_pct,
                    "miedzykontynentalna": ic,
                    "dystans": d, "ocena": score,
                })
        cands.sort(key=lambda c: -c["ocena"])

        # przydzial zachlanny: limit tras na miasto
        picked = []
        per_city = collections.Counter()
        taken = set()
        for c in cands:
            if max_routes and used[c["moje_miasto"]] + per_city[c["moje_miasto"]] >= max_routes:
                continue
            key = (c["moje_miasto"], c["partner"])
            if key in taken:
                continue
            taken.add(key)
            per_city[c["moje_miasto"]] += 1
            picked.append(c)
            if len(picked) >= limit:
                break

        martwe = sorted({k for k, v in pct.items() if v <= 0})
        return {
            "tryb_wywiadu": "pełny wgląd (świadome chity)" if full
            else "tylko moja wiedza (mgła wojny)",
            "zasady": {k: {"procent": v, "premia_jednorazowa": bonus.get(k, "")}
                       for k, v in pct.items()},
            "bez_wartosci": martwe,
            "min_dystans": mindist,
            "max_tras_na_miasto": max_routes,
            "wolnych_slotow": sum(max(0, max_routes - used[c.name]) for c in my),
            "propozycje": picked,
            "kandydatow_lacznie": len(cands),
            "uwaga": ("Ocena jest przybliżona: zapis nie zawiera handlu miasta, "
                      "więc zamiast niego bierzemy rozmiar. Kolejność jest "
                      "wiarygodna, wartości bezwzględne nie."),
        }

    # ------------------------------------------------------ rozwiazywanie

    def disband_plan(self, rs, unit_types: list[str] | None = None,
                     full: bool = False) -> dict:
        """Co da rozwiazanie jednostek: tarcze, utrzymanie, zywnosc, gdzie.

        Procent zwrotu czytamy z regul (efekt Unit_Shield_Value_Pct przy akcji
        "Disband Unit Recover"), tak samo utrzymanie wg ustroju. Kandydatow
        dobieramy sami: jednostki odciete od wszystkich celow oraz bezczynne
        jednostki cywilne.
        """
        import collections
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}

        # ile procent kosztu wraca
        pct = 100
        for eff in rs.effects_by_type.get("Unit_Shield_Value_Pct", []):
            acts = [r.name for r in eff.reqs if r.type == "Action" and r.present]
            if "Disband Unit Recover" in acts:
                pct += eff.value
        pct = max(0, pct)

        gov = s.me.government
        out_type, free_per_city, factor = _upkeep_profile(rs, gov)

        units = s.units_of(s.me.slot)
        sec = s._sections[s.me.slot]
        ctbl = sec.table("c")
        crows = list(ctbl.dicts()) if ctbl else []
        city_by_id = {int(r["id"]): r for r in crows}
        by_home = collections.Counter(u.homecity for u in units)

        # cele: obszary, do ktorych warto docierac
        tmap = TerrainMap(s)
        enemy_spots = []
        for p in s.players.values():
            if p.slot == s.me.slot:
                continue
            cs = s.cities_of(p.slot) if full else [
                c for c in s.known_cities() if c.owner == p.slot]
            enemy_spots.extend((c.x, c.y) for c in cs)

        candidates: list[dict] = []
        chosen: list = []
        wanted = {str(t) for t in (unit_types or [])}

        # 1) jednostki odciete od wszystkich celow (klasa nie dojdzie)
        by_class: dict[str, list] = collections.defaultdict(list)
        for u in units:
            ut = rs.units.get(u.type)
            if ut:
                by_class[rs.uclass_of(ut).name].append(u)
        for cls, group in by_class.items():
            ok = passability(rs, tmap, cls)
            reg = regions(tmap, ok)
            good = {reg.get(spot, 0) for spot in enemy_spots if reg.get(spot)}
            cut = [u for u in group if reg.get((u.x, u.y), 0) not in good]
            if not cut:
                continue
            for tname, n in collections.Counter(u.type for u in cut).items():
                if wanted and tname not in wanted:
                    continue
                ut = rs.units[tname]
                if ut.attack <= 0 and ut.defense <= 0:
                    continue                      # cywilne omijamy tutaj
                candidates.append({
                    "jednostka": tname, "klasa": cls, "sztuk": n,
                    "powod": "odcięta od wszystkich celów — nie dojdzie do walki",
                    "koszt_budowy": ut.build_cost,
                    "zwrot_tarcz": ut.build_cost * pct // 100 * n,
                    "zywnosc": getattr(ut, "uk_food", 0) * n,
                })
                chosen.extend([u for u in cut if u.type == tname])

        # 2) bezczynne jednostki cywilne
        idle = [u for u in units
                if u.activity == "Idle" and rs.units.get(u.type)
                and rs.units[u.type].attack == 0 and rs.units[u.type].defense <= 1]
        for tname, n in collections.Counter(u.type for u in idle).items():
            if wanted and tname not in wanted:
                continue
            ut = rs.units[tname]
            keep = 0 if wanted else min(n, 30)   # zapas na roboty
            drop = n - keep
            if drop <= 0:
                continue
            candidates.append({
                "jednostka": tname, "klasa": rs.uclass_of(ut).name, "sztuk": drop,
                "powod": f"bezczynne ({n} sztuk stoi, zostawiam {keep} w rezerwie)",
                "koszt_budowy": ut.build_cost,
                "zwrot_tarcz": ut.build_cost * pct // 100 * drop,
                "zywnosc": getattr(ut, "uk_food", 0) * drop,
            })
            chosen.extend([u for u in idle if u.type == tname][:drop])

        # oszczednosc na utrzymaniu: przed i po
        def upkeep(counter) -> int:
            return sum(max(0, counter.get(cid, 0) - free_per_city) * max(1, factor)
                       for cid in city_by_id)
        after = by_home.copy()
        for u in chosen:
            after[u.homecity] -= 1
        before_cost, after_cost = upkeep(by_home), upkeep(after)

        # gdzie rozwiazac: miasta bez kluczowych budynkow
        KEY = ["Library", "Temple", "Marketplace", "Granary", "Harbour",
               "Colosseum", "Aqueduct", "University", "Bank"]
        spots = []
        for r in crows:
            blds = set(s._bits(r.get("improvements")))
            missing = [k for k in KEY
                       if k in rs.buildings and k not in blds
                       and all(t in _known_techs(s) for t in rs.buildings[k].req_techs())]
            if not missing:
                continue
            cheapest = min(missing, key=lambda k: rs.buildings[k].build_cost)
            here = sum(1 for u in chosen
                       if (u.x, u.y) == (int(r["x"]), int(r["y"])))
            spots.append({
                "miasto": str(r.get("name")), "rozmiar": int(r.get("size") or 0),
                "brakuje": missing,
                "najtanszy_brak": cheapest,
                "koszt": rs.buildings[cheapest].build_cost,
                "buduje_teraz": str(r.get("currently_building_name") or "") or None,
                "jednostek_do_rozwiazania_na_miejscu": here,
            })
        spots.sort(key=lambda x: (-x["jednostek_do_rozwiazania_na_miejscu"],
                                  -x["rozmiar"]))

        total = sum(c["zwrot_tarcz"] for c in candidates)
        buys = []
        for k in KEY:
            b = rs.buildings.get(k)
            if b and b.build_cost and all(t in _known_techs(s) for t in b.req_techs()):
                brakuje = sum(1 for x in spots if k in x["brakuje"])
                if brakuje:
                    buys.append({"budynek": k, "koszt": b.build_cost,
                                 "brakuje_w_miastach": brakuje,
                                 "ile_za_zwrot": total // b.build_cost})
        buys.sort(key=lambda x: -x["brakuje_w_miastach"])

        return {
            "zwrot_procent": pct,
            "ustroj": gov,
            "utrzymanie_placone_w": {"Gold": "złocie", "Shield": "tarczach"}.get(
                out_type, out_type),
            "kandydaci": sorted(candidates, key=lambda c: -c["zwrot_tarcz"]),
            "razem_tarcz": total,
            "utrzymanie_teraz": before_cost,
            "utrzymanie_po": after_cost,
            "oszczednosc_na_ture": before_cost - after_cost,
            "uwolniona_zywnosc": sum(c["zywnosc"] for c in candidates),
            "gdzie_rozwiazac": spots[:12],
            "co_za_to_kupisz": buys[:6],
            "uwaga": ("Tarcze trafiają do miasta, w którym rozwiązujesz jednostkę, "
                      "i idą w bieżącą produkcję — ustaw docelowy budynek ZANIM "
                      "rozwiążesz."),
        }

    # -------------------------------------------------------------- miasta

    def cities_audit(self, rs) -> dict:
        """Wielkosc miast, limity wzrostu i ile jednostek jeszcze wyzywia.

        W wielu zestawach regul darmowe utrzymanie ZYWNOSCIOWE rosnie razem
        z miastem (efekt Unit_Upkeep_Free_Per_City dla OutputType:Food
        z warunkami MinSize), a limit wielkosci podnosza akwedukt i kanalizacja.
        Wszystko czytamy z regul - nic nie jest tu zaszyte.
        """
        import collections
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}

        # ile jednostek na zywnosci miasto utrzyma za darmo, wg wielkosci
        base = 0
        steps: list[int] = []
        for eff in rs.effects_by_type.get("Unit_Upkeep_Free_Per_City", []):
            types = [r.name for r in eff.reqs if r.type == "OutputType"]
            if types != ["Food"]:
                continue
            sizes = [int(r.name) for r in eff.reqs
                     if r.type == "MinSize" and str(r.name).isdigit()]
            govs = [r.name for r in eff.reqs if r.type == "Gov"]
            if govs:
                continue
            if sizes:
                steps.extend([sizes[0]] * eff.value)
            else:
                base += eff.value
        steps.sort()

        def free_food(size: int) -> int:
            return base + sum(1 for m in steps if size >= m)

        # limit wielkosci: Size_Adj sumuje sie, Size_Unlimit znosi limit
        def size_cap(buildings: set[str]) -> tuple[int, bool]:
            cap = 0
            for eff in rs.effects_by_type.get("Size_Adj", []):
                need = [r for r in eff.reqs if r.type == "Building"]
                if all((r.name in buildings) == r.present for r in need):
                    cap += eff.value
            unlimited = False
            for eff in rs.effects_by_type.get("Size_Unlimit", []):
                need = [r for r in eff.reqs if r.type == "Building"]
                if need and all((r.name in buildings) == r.present for r in need):
                    unlimited = True
            return cap, unlimited

        food_cost = {}
        for name, ut in rs.units.items():
            food_cost[name] = getattr(ut, "uk_food", 0)

        sec = s._sections[s.me.slot]
        tbl = sec.table("c")
        by_home = collections.defaultdict(list)
        for u in s.units_of(s.me.slot):
            by_home[u.homecity].append(u)

        rows = []
        for r in (tbl.dicts() if tbl else []):
            cid = int(r.get("id") or 0)
            size = int(r.get("size") or 0)
            blds = set(s._bits(r.get("improvements")))
            here = by_home.get(cid, [])
            eaters = sum(1 for u in here if food_cost.get(u.type, 0) > 0)
            ff = free_food(size)
            cap, unlimited = size_cap(blds)
            rows.append({
                "miasto": str(r.get("name") or "?"),
                "rozmiar": size,
                "jednostek": len(here),
                "jednostek_na_zywnosci": eaters,
                "darmowe_utrzymanie_zywnosci": ff,
                "zapas_do_limitu": ff - eaters,
                "deficyt_zywnosci": max(0, eaters - ff),
                "limit_wielkosci": "bez limitu" if unlimited else cap,
                "zapas_zywnosci": int(r.get("food_stock") or 0),
                "buduje": str(r.get("currently_building_name") or "") or None,
            })
        rows.sort(key=lambda x: (-x["deficyt_zywnosci"], x["zapas_do_limitu"]))

        # ktore jednostki w ogole jedza
        eat = sorted(n for n, c in food_cost.items() if c > 0)
        free_eat = sorted(n for n, c in food_cost.items()
                          if c == 0 and rs.units[n].build_cost > 0)
        return {
            "zasada_darmowego_utrzymania": (
                f"{base} jednostek na żywności za darmo, +1 za każdy rozmiar "
                f"od {steps[0] if steps else '-'} do {steps[-1] if steps else '-'}"),
            "przyklad": {f"rozmiar {n}": free_food(n) for n in (4, 8, 12, 16, 20, 24)},
            "jednostki_jedzace": eat[:40],
            "jednostki_bez_zywnosci": free_eat[:20],
            "miasta": rows,
            "miast_z_deficytem": sum(1 for x in rows if x["deficyt_zywnosci"]),
            "miast_na_granicy": sum(1 for x in rows if x["zapas_do_limitu"] == 0),
        }

    # ------------------------------------------------------------ przejezdnosc

    def reachability(self, rs, unit_types: list[str], full: bool) -> dict:
        """Czy jednostki danych typow w ogole dojda do celow.

        Klasy takie jak "Big Land" (katapulty, dziala) nie wchodza na bagna,
        dzungle i gory bez drogi - to czesto wazniejsze niz sama sila ataku.
        """
        import collections
        s = self.save
        if s.me is None:
            return {"blad": "brak gracza ludzkiego"}
        tmap = TerrainMap(s)
        my_units = s.units_of(s.me.slot)

        types = unit_types or sorted({u.type for u in my_units})
        out: dict = {"tryb_wywiadu": "pełny wgląd (świadome chity)" if full
                     else "tylko moja wiedza (mgła wojny)", "jednostki": {}}

        targets = []
        for p in s.players.values():
            if s.me and p.slot == s.me.slot:
                continue
            cs = s.cities_of(p.slot) if full else [
                c for c in s.known_cities() if c.owner == p.slot]
            for c in cs:
                targets.append((p.nation, c.name, c.x, c.y))

        for tname in types:
            ut = rs.units.get(tname)
            if ut is None:
                continue
            uclass = rs.uclass_of(ut).name
            ok = passability(rs, tmap, uclass)
            reg = regions(tmap, ok)
            mine = [u for u in my_units if u.type == tname]
            where = collections.Counter(reg.get((u.x, u.y), 0) for u in mine)
            entry = {
                "klasa": uclass,
                "sztuk": len(mine),
                "wchodzi_na": sorted(t.name for t in rs.terrains.values()
                                     if uclass in t.native_to and t.is_land),
                "nie_wchodzi_bez_drogi": sorted(
                    t.name for t in rs.terrains.values()
                    if t.is_land and uclass not in t.native_to),
                "moje_sztuki_wg_obszaru": [
                    {"obszar": k, "sztuk": v} for k, v in where.most_common()],
                "cele": [],
            }
            for nation, cname, x, y in targets:
                z = reg.get((x, y), 0)
                entry["cele"].append({
                    "nacja": nation, "miasto": cname,
                    "teren": tmap.terrain(x, y),
                    "obszar": z,
                    "moich_sztuk_w_tym_obszarze": where.get(z, 0),
                    "dojda": where.get(z, 0) > 0,
                })
            # dla odcietych grup policz, ile pracy kosztuje polaczenie ich drogą
            target_regions = {c["obszar"] for c in entry["cele"] if c["obszar"]}
            main = max(target_regions, key=lambda z: sum(
                1 for c in entry["cele"] if c["obszar"] == z), default=0)
            links = []
            for z, n in where.items():
                if z == main or z == 0 or n == 0:
                    continue
                spot = next(((u.x, u.y) for u in mine
                             if reg.get((u.x, u.y), 0) == z), None)
                if spot is None:
                    continue
                link = road_link(rs, tmap, ok, spot, main, reg)
                if link:
                    links.append({"obszar": z, "odcietych_sztuk": n, **link})
            if links:
                entry["polaczenia_drogowe"] = sorted(
                    links, key=lambda l: l["lacznie_tur_pracy"])
                entry["glowny_obszar"] = main

            entry["odcietych_sztuk"] = sum(
                v for k, v in where.items()
                if not any(c["obszar"] == k for c in entry["cele"]))
            out["jednostki"][tname] = entry
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

    def ai_trade(self, args: dict) -> dict:
        full = bool(args.get("pelny_wglad", self._intel_full))
        return self._need_intel().trade_routes(
            self._intel_ruleset(), int(args.get("limit") or 15), full,
            bool(args.get("tylko_miedzykontynentalne")))

    def ai_disband(self, args: dict) -> dict:
        full = bool(args.get("pelny_wglad", self._intel_full))
        types = args.get("jednostki") or None
        return self._need_intel().disband_plan(
            self._intel_ruleset(),
            [str(t) for t in types] if types else None, full)

    def ai_cities(self, args: dict) -> dict:
        return self._need_intel().cities_audit(self._intel_ruleset())

    def ai_reach(self, args: dict) -> dict:
        full = bool(args.get("pelny_wglad", self._intel_full))
        types = args.get("jednostki") or []
        return self._need_intel().reachability(
            self._intel_ruleset(), [str(t) for t in types], full)

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
