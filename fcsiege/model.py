"""Model danych zestawu regul Freeciva.

Wszystko jest czytane z prawdziwych plikow .ruleset, nic nie jest zaszyte
w kodzie. Dzieki temu ten sam kod liczy walke dla classic, sandbox, civ2civ3,
alien itd.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from .registry import Registry, Section, Table, parse_ruleset_dir

POWER_FACTOR = 10  # Freeciv trzyma sile w dziesiatych czesciach punktu

RULESET_FILES = ["units", "terrain", "effects", "game",
                 "buildings", "techs", "cities", "governments"]

_CTX_PREFIX = re.compile(r"^\?[^:]*:")


def clean_name(raw: str) -> str:
    """Usuwa kontekst tlumaczenia, np. '?unit:Workers' -> 'Workers'."""
    return _CTX_PREFIX.sub("", str(raw)).strip()


@dataclass(frozen=True)
class Req:
    """Pojedyncze wymaganie efektu."""
    type: str
    name: str
    range: str = "Local"
    present: bool = True

    @staticmethod
    def from_row(row: dict) -> "Req":
        present = row.get("present")
        if present is None:
            present = True
        return Req(
            type=str(row.get("type", "")),
            name=str(row.get("name", "")),
            range=str(row.get("range", "Local")),
            present=bool(present),
        )


@dataclass
class Effect:
    type: str
    value: int
    reqs: list[Req] = field(default_factory=list)
    nreqs: list[Req] = field(default_factory=list)


@dataclass
class Bonus:
    """Wpis z 'bonuses' typu jednostki (np. Pikinierzy kontra konnica)."""
    flag: str
    type: str
    value: int


@dataclass
class UnitClass:
    id: str
    name: str
    flags: set[str] = field(default_factory=set)
    min_speed: int = 0

    @property
    def terrain_defense(self) -> bool:
        return "TerrainDefense" in self.flags

    @property
    def can_fortify(self) -> bool:
        return "CanFortify" in self.flags

    @property
    def can_occupy_city(self) -> bool:
        return "CanOccupyCity" in self.flags


@dataclass
class VetLevel:
    name: str
    power_fact: int
    raise_chance: int = 0


@dataclass
class UnitType:
    id: str
    name: str
    uclass_id: str
    attack: int
    defense: int
    hitpoints: int
    firepower: int
    build_cost: int
    pop_cost: int
    move_rate: int
    uk_shield: int = 0
    flags: set[str] = field(default_factory=set)
    roles: set[str] = field(default_factory=set)
    reqs: list[Req] = field(default_factory=list)
    bonuses: list[Bonus] = field(default_factory=list)
    obsolete_by: str = ""
    vet_levels: list[VetLevel] = field(default_factory=list)
    helptext: str = ""

    @property
    def is_military(self) -> bool:
        return (self.attack > 0 or self.defense > 0) and "NonMil" not in self.flags

    def req_techs(self) -> list[str]:
        return [r.name for r in self.reqs if r.type == "Tech" and r.present]

    def bonus_value(self, btype: str, other_flags: set[str]) -> int:
        """Sumaryczna wartosc bonusu danego typu wobec przeciwnika o tych flagach."""
        total = 0
        for b in self.bonuses:
            if b.type == btype and b.flag in other_flags:
                total += b.value
        return total

    def has_bonus(self, btype: str, other_flags: set[str]) -> bool:
        return any(b.type == btype and b.flag in other_flags for b in self.bonuses)


@dataclass
class Terrain:
    id: str
    name: str
    defense_bonus: int
    movement_cost: int
    tclass: str = "Land"
    flags: set[str] = field(default_factory=set)
    native_to: set[str] = field(default_factory=set)
    road_time: int = 0

    @property
    def is_land(self) -> bool:
        return self.tclass.lower() == "land"


@dataclass
class Extra:
    id: str
    name: str
    defense_bonus: int = 0
    flags: set[str] = field(default_factory=set)
    causes: set[str] = field(default_factory=set)
    reqs: list[Req] = field(default_factory=list)

    @property
    def natural_defense(self) -> bool:
        return "NaturalDefense" in self.flags


@dataclass
class Building:
    id: str
    name: str
    build_cost: int = 0
    upkeep: int = 0
    genus: str = "Improvement"
    reqs: list[Req] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)

    @property
    def is_wonder(self) -> bool:
        return "Wonder" in self.genus or self.genus in ("GreatWonder", "SmallWonder")

    def req_techs(self) -> list[str]:
        return [r.name for r in self.reqs if r.type == "Tech" and r.present]


@dataclass
class Tech:
    id: str
    name: str
    req1: str = "None"
    req2: str = "None"
    root_req: str = "None"


@dataclass
class CombatRules:
    tired_attack: bool = False
    low_firepower_badwallattacker: int = 1
    low_firepower_pearl_harbour: int = 1
    low_firepower_combat_bonus: int = 1
    low_firepower_nonnat_bombard: int = 1
    killstack: bool = True


class Ruleset:
    """Kompletny zestaw regul gotowy do obliczen."""

    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.units: dict[str, UnitType] = {}
        self.unit_classes: dict[str, UnitClass] = {}
        self.terrains: dict[str, Terrain] = {}
        self.extras: dict[str, Extra] = {}
        self.buildings: dict[str, Building] = {}
        self.techs: dict[str, Tech] = {}
        self.governments: list[str] = []
        self.effects: list[Effect] = []
        self.effects_by_type: dict[str, list[Effect]] = {}
        self.combat: CombatRules = CombatRules()
        self.move_fragments: int = 3
        self.default_vet: list[VetLevel] = []
        self._tech_depth: dict[str, int] | None = None

    # ---------------------------------------------------------------- ladowanie

    @classmethod
    def load(cls, dir_path: str) -> "Ruleset":
        name = os.path.basename(dir_path.rstrip("/"))
        regs = parse_ruleset_dir(dir_path, RULESET_FILES)
        rs = cls(name, dir_path)
        rs._load_units(regs.get("units"))
        rs._load_terrain(regs.get("terrain"))
        rs._load_buildings(regs.get("buildings"))
        rs._load_techs(regs.get("techs"))
        rs._load_effects(regs.get("effects"))
        rs._load_game(regs.get("game"))
        rs._load_governments(regs.get("governments"))
        return rs

    def _load_governments(self, reg: Registry | None) -> None:
        if reg is None:
            return
        for sec in reg.prefixed("government_"):
            name = clean_name(sec.str("name"))
            if name:
                self.governments.append(name)

    @staticmethod
    def _reqs_from(sec: Section, key: str = "reqs") -> list[Req]:
        tbl = sec.table(key)
        if not tbl:
            return []
        return [Req.from_row(row) for row in tbl.dicts()]

    def _vet_from(self, sec: Section, fallback: list[VetLevel]) -> list[VetLevel]:
        facts = sec.list("veteran_power_fact")
        if not facts:
            return fallback
        names = [clean_name(n) for n in sec.list("veteran_names")]
        chances = sec.list("veteran_base_raise_chance")
        out = []
        for i, f in enumerate(facts):
            out.append(VetLevel(
                name=names[i] if i < len(names) else f"poziom {i}",
                power_fact=int(f),
                raise_chance=int(chances[i]) if i < len(chances) else 0,
            ))
        return out

    def _load_units(self, reg: Registry | None) -> None:
        if reg is None:
            return
        vs = reg.get("veteran_system")
        self.default_vet = self._vet_from(vs, [VetLevel("green", 100)]) if vs \
            else [VetLevel("green", 100)]

        for sec in reg.prefixed("unitclass_"):
            uc = UnitClass(
                id=sec.name,
                name=clean_name(sec.str("name")),
                flags=set(str(f) for f in sec.list("flags")),
                min_speed=sec.int("min_speed"),
            )
            self.unit_classes[uc.name] = uc

        for sec in reg.prefixed("unit_"):
            bonuses = []
            tbl = sec.table("bonuses")
            if tbl:
                for row in tbl.dicts():
                    bonuses.append(Bonus(
                        flag=str(row.get("flag", "")),
                        type=str(row.get("type", "")),
                        value=int(row.get("value") or 0),
                    ))
            ut = UnitType(
                id=sec.name,
                name=clean_name(sec.str("name")),
                uclass_id=clean_name(sec.str("class")),
                attack=sec.int("attack"),
                defense=sec.int("defense"),
                hitpoints=max(1, sec.int("hitpoints", 10)),
                firepower=max(0, sec.int("firepower", 1)),
                build_cost=sec.int("build_cost"),
                pop_cost=sec.int("pop_cost"),
                move_rate=sec.int("move_rate", 1),
                uk_shield=sec.int("uk_shield"),
                flags=set(str(f) for f in sec.list("flags")),
                roles=set(str(f) for f in sec.list("roles")),
                reqs=self._reqs_from(sec),
                bonuses=bonuses,
                obsolete_by=clean_name(sec.str("obsolete_by")),
                vet_levels=self._vet_from(sec, self.default_vet),
                helptext=sec.str("helptext"),
            )
            self.units[ut.name] = ut

    def _load_terrain(self, reg: Registry | None) -> None:
        if reg is None:
            return
        params = reg.get("parameters")
        if params:
            self.move_fragments = max(1, params.int("move_fragments", 3))

        for sec in reg.prefixed("terrain_"):
            t = Terrain(
                id=sec.name,
                name=clean_name(sec.str("name")),
                defense_bonus=sec.int("defense_bonus"),
                movement_cost=sec.int("movement_cost", 1),
                tclass=clean_name(sec.str("class", "Land")),
                flags=set(str(f) for f in sec.list("flags")),
                native_to=set(clean_name(str(c)) for c in sec.list("native_to")),
                road_time=sec.int("road_time"),
            )
            self.terrains[t.name] = t

        for sec in reg.prefixed("extra_"):
            e = Extra(
                id=sec.name,
                name=clean_name(sec.str("name")),
                defense_bonus=sec.int("defense_bonus"),
                flags=set(str(f) for f in sec.list("flags")),
                causes=set(str(f) for f in sec.list("causes")),
                reqs=self._reqs_from(sec),
            )
            self.extras[e.name] = e

    def _load_buildings(self, reg: Registry | None) -> None:
        if reg is None:
            return
        for sec in reg.prefixed("building_"):
            b = Building(
                id=sec.name,
                name=clean_name(sec.str("name")),
                build_cost=sec.int("build_cost"),
                upkeep=sec.int("upkeep"),
                genus=clean_name(sec.str("genus", "Improvement")),
                reqs=self._reqs_from(sec),
                flags=set(str(f) for f in sec.list("flags")),
            )
            self.buildings[b.name] = b

    def _load_techs(self, reg: Registry | None) -> None:
        if reg is None:
            return
        for sec in reg.prefixed("advance_"):
            t = Tech(
                id=sec.name,
                name=clean_name(sec.str("name")),
                req1=clean_name(sec.str("req1", "None")),
                req2=clean_name(sec.str("req2", "None")),
                root_req=clean_name(sec.str("root_req", "None")),
            )
            self.techs[t.name] = t

    def _load_effects(self, reg: Registry | None) -> None:
        if reg is None:
            return
        for sec in reg.sections:
            etype = sec.str("type")
            if not etype or "value" not in sec:
                continue
            eff = Effect(
                type=etype,
                value=sec.int("value"),
                reqs=self._reqs_from(sec, "reqs"),
                nreqs=self._reqs_from(sec, "nreqs"),
            )
            self.effects.append(eff)
            self.effects_by_type.setdefault(etype, []).append(eff)

    def _load_game(self, reg: Registry | None) -> None:
        if reg is None:
            return
        cr = reg.get("combat_rules")
        if cr:
            self.combat = CombatRules(
                tired_attack=cr.bool("tired_attack"),
                low_firepower_badwallattacker=cr.int("low_firepower_badwallattacker", 1),
                low_firepower_pearl_harbour=cr.int("low_firepower_pearl_harbour", 1),
                low_firepower_combat_bonus=cr.int("low_firepower_combat_bonus", 1),
                low_firepower_nonnat_bombard=cr.int("low_firepower_nonnat_bombard", 1),
            )

    # ------------------------------------------------------------- pomocnicze

    def uclass_of(self, ut: UnitType) -> UnitClass:
        return self.unit_classes.get(ut.uclass_id, UnitClass(ut.uclass_id, ut.uclass_id))

    def tech_depth(self, tech_name: str) -> int:
        """Ile technologii lacznie trzeba zbadac, by miec dana technologie."""
        if self._tech_depth is None:
            self._tech_depth = {}
            for name in self.techs:
                self._depth_of(name, set())
        return self._tech_depth.get(tech_name, 0)

    def _depth_of(self, name: str, stack: set[str]) -> int:
        if name in ("None", "", "Never"):
            return 0
        assert self._tech_depth is not None
        if name in self._tech_depth:
            return self._tech_depth[name]
        if name in stack or name not in self.techs:
            return 0
        stack.add(name)
        t = self.techs[name]
        deps: set[str] = set()
        for parent in (t.req1, t.req2, t.root_req):
            if parent in ("None", "", "Never") or parent not in self.techs:
                continue
            deps |= self._closure(parent, stack)
        stack.discard(name)
        depth = len(deps) + 1
        self._tech_depth[name] = depth
        return depth

    def _closure(self, name: str, stack: set[str]) -> set[str]:
        """Zbior technologii potrzebnych do zdobycia 'name' (wlacznie z nia)."""
        if name in ("None", "", "Never") or name not in self.techs:
            return set()
        out = {name}
        t = self.techs[name]
        for parent in (t.req1, t.req2, t.root_req):
            if parent in ("None", "", "Never") or parent in out or parent in stack:
                continue
            out |= self._closure(parent, stack | out)
        return out

    def techs_up_to(self, depth: int) -> set[str]:
        """Zbior technologii osiagalnych przy danym poziomie rozwoju."""
        return {n for n in self.techs if self.tech_depth(n) <= depth}

    def max_tech_depth(self) -> int:
        return max((self.tech_depth(n) for n in self.techs), default=0)

    def unit_tech_depth(self, ut: UnitType) -> int:
        techs = ut.req_techs()
        if not techs:
            return 0
        return max(self.tech_depth(t) for t in techs)

    def units_available(self, known_techs: set[str] | None) -> list[UnitType]:
        """Jednostki mozliwe do zbudowania przy danym stanie wiedzy."""
        out = []
        for ut in self.units.values():
            if known_techs is None:
                out.append(ut)
                continue
            if all(t in known_techs for t in ut.req_techs()):
                out.append(ut)
        return out

    def defensive_buildings(self) -> list[Building]:
        """Budynki i cuda wystepujace w efektach obronnych tego zestawu regul."""
        names: list[str] = []
        interesting = ("Defend_Bonus", "Fortify_Defense_Bonus", "Unit_No_Lose_Pop",
                       "HP_Regen", "HP_Regen_2", "Veteran_Build", "Min_HP_Pct")
        for etype in interesting:
            for eff in self.effects_by_type.get(etype, []):
                for r in eff.reqs:
                    if not r.present:
                        continue
                    if r.type in ("Building", "Wonder"):
                        if r.name not in names:
                            names.append(r.name)
                    elif r.type == "BuildingFlag":
                        # np. efekt koszar wskazuje flage "Barracks",
                        # a nie konkretna budowle
                        for b in self.buildings.values():
                            if r.name in b.flags and b.name not in names:
                                names.append(b.name)
        return [self.buildings[n] for n in names if n in self.buildings]

    def defensive_extras(self) -> list[Extra]:
        """Ulepszenia terenu wplywajace na obrone (rzeka, fortyfikacja, ...)."""
        names: list[str] = []
        for e in self.extras.values():
            if e.defense_bonus:
                names.append(e.name)
        for etype in ("Defend_Bonus", "Fortify_Defense_Bonus", "HP_Regen"):
            for eff in self.effects_by_type.get(etype, []):
                for r in eff.reqs:
                    if r.type == "Extra" and r.present and r.name in self.extras \
                            and r.name not in names:
                        names.append(r.name)
        return [self.extras[n] for n in names]

    def land_terrains(self) -> list[Terrain]:
        return [t for t in self.terrains.values() if t.is_land]


def discover_rulesets(root: str) -> list[str]:
    """Zwraca katalogi zestawow regul (te, ktore maja units.ruleset)."""
    out = []
    if not os.path.isdir(root):
        return out
    for entry in sorted(os.listdir(root)):
        d = os.path.join(root, entry)
        if os.path.isdir(d) and os.path.exists(os.path.join(d, "units.ruleset")):
            out.append(d)
    return out


def default_ruleset_roots() -> list[str]:
    """Miejsca, w ktorych szukamy zestawow regul."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots = [
        os.path.join(here, "data", "rulesets"),
        os.path.expanduser("~/.local/share/freeciv"),
        "/usr/share/freeciv",
        "/usr/local/share/freeciv",
    ]
    env = os.environ.get("FREECIV_DATA_PATH")
    if env:
        roots = env.split(":") + roots
    return [r for r in roots if os.path.isdir(r)]
