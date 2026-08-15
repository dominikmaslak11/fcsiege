"""Kalkulator bez interfejsu graficznego.

Ten sam zestaw narzedzi co w oknie aplikacji, ale operujacy na zwyklym obiekcie
stanu zamiast na kontrolkach Qt. Uzywaja go serwer MCP i API HTTP, dzieki czemu
wszystkie trzy powierzchnie licza dokladnie tym samym silnikiem.
"""

from __future__ import annotations

import difflib
import os
from dataclasses import dataclass, field

import numpy as np

from .advisor import (max_wave_stopped, min_defenders, rank_defenders,
                      rank_units)
from .combat import (Side, Situation, defense_stand, duel, siege,
                     veteran_build_level)
from .model import Ruleset, default_ruleset_roots, discover_rulesets
from .savegame import IntelMixin

MODE_ATTACK = "szturm"
MODE_DEFENSE = "obrona"


def find_rulesets() -> dict[str, str]:
    """Nazwa zestawu regul -> katalog. Pomija zestawy pomocnicze."""
    out: dict[str, str] = {}
    for root in default_ruleset_roots():
        for d in discover_rulesets(root):
            name = os.path.basename(d)
            if name not in ("stub", "ruledit", "override"):
                out.setdefault(name, d)
    return out


@dataclass
class ScenarioState:
    """Pelny opis scenariusza - odpowiednik ustawien w oknie."""
    ruleset: str = "classic"
    mode: str = MODE_ATTACK
    terrain: str = "Hills"
    attacker_terrain: str = "Forest"
    in_city: bool = True
    city_size: int = 8
    fortified: bool = True
    buildings: list[str] = field(default_factory=lambda: ["City Walls"])
    extras: list[str] = field(default_factory=list)
    gov: str = "Despotism"
    tech_depth: int | None = None            # None = pelne drzewo
    from_barracks: bool = True
    promotions: bool = True
    my_unit: str | None = None
    my_vet: int = 0
    planned: int = 8
    enemy: list[dict] = field(default_factory=list)


class HeadlessBridge(IntelMixin):
    """Realizuje ten sam protokol narzedziowy, co okno aplikacji."""

    def __init__(self, ruleset: str = "classic"):
        self._dirs = find_rulesets()
        self.state = ScenarioState()
        self._rs: Ruleset | None = None
        self.load_ruleset(ruleset if ruleset in self._dirs else
                          next(iter(self._dirs), "classic"))

    # ------------------------------------------------------------ zaladowanie

    def load_ruleset(self, name: str) -> None:
        path = self._dirs.get(name)
        if path is None:
            raise KeyError(name)
        self._rs = Ruleset.load(path)
        self.state.ruleset = name
        self._fit_state_to_ruleset()

    @property
    def rs(self) -> Ruleset:
        assert self._rs is not None
        return self._rs

    def _fit_state_to_ruleset(self) -> None:
        """Po zmianie zestawu regul podmienia nazwy, ktorych w nim nie ma."""
        rs, st = self.rs, self.state
        lands = [t.name for t in rs.land_terrains()]
        if st.terrain not in lands:
            st.terrain = "Hills" if "Hills" in lands else lands[0]
        if st.attacker_terrain not in lands:
            st.attacker_terrain = "Forest" if "Forest" in lands else lands[0]
        blds = {b.name for b in rs.defensive_buildings()}
        st.buildings = [b for b in st.buildings if b in blds]
        exts = {e.name for e in rs.defensive_extras()}
        st.extras = [e for e in st.extras if e in exts]
        if st.gov not in rs.governments:
            st.gov = rs.governments[0] if rs.governments else "Despotism"
        st.enemy = [e for e in st.enemy if e.get("jednostka") in rs.units]
        if st.my_unit not in rs.units:
            st.my_unit = None
        self._ensure_defaults()

    def _ensure_defaults(self) -> None:
        """Uzupelnia brakujace jednostki sensownym wyborem z zestawu regul."""
        rs, st = self.rs, self.state
        known = self.known_techs()
        avail = rs.units_available(known)
        if st.mode == MODE_ATTACK:
            pool = [u for u in avail if u.attack > 0 and "NonMil" not in u.flags]
            enemy_pool = [u for u in avail if u.defense > 0]
            prefer = ["Catapult", "Legion", "Archers"]
            enemy_prefer = ["Warriors", "Militia", "Phalanx"]
        else:
            pool = [u for u in avail if u.defense > 0]
            enemy_pool = [u for u in avail if u.attack > 0 and "NonMil" not in u.flags]
            prefer = ["Phalanx", "Pikemen", "Warriors"]
            enemy_prefer = ["Warriors", "Militia", "Legion"]

        names = {u.name for u in pool}
        if st.my_unit not in names:
            st.my_unit = next((p for p in prefer if p in names),
                              sorted(names)[0] if names else None)
        enemy_names = {u.name for u in enemy_pool}
        st.enemy = [e for e in st.enemy if e["jednostka"] in enemy_names]
        if not st.enemy and enemy_names:
            pick = next((p for p in enemy_prefer if p in enemy_names),
                        sorted(enemy_names)[0])
            st.enemy = [{"jednostka": pick, "liczba": 5, "stopien": 0}]

    def known_techs(self) -> set[str] | None:
        st = self.state
        over = getattr(self, "_tech_override", None)
        if over:
            return set(over)
        if st.tech_depth is None or st.tech_depth >= self.rs.max_tech_depth():
            return None
        return self.rs.techs_up_to(st.tech_depth)

    # ------------------------------------------------------- budowa obiektow

    def situation(self) -> Situation:
        rs, st = self.rs, self.state
        buildings, wonders = set(), set()
        for name in st.buildings:
            b = rs.buildings.get(name)
            if b and b.is_wonder:
                wonders.add(name)
            elif b:
                buildings.add(name)
        return Situation(
            terrain=rs.terrains[st.terrain],
            extras=set(st.extras),
            in_city=st.in_city,
            city_size=st.city_size,
            buildings=buildings,
            player_buildings=wonders,
            fortified=st.fortified,
            gov=st.gov,
            techs=self.known_techs() or set(rs.techs),
            units_on_tile=max(1, sum(e.get("liczba", 0) for e in st.enemy)),
            attacker_terrain=rs.terrains.get(st.attacker_terrain),
        )

    def enemy_sides(self) -> list[Side]:
        rs = self.rs
        out = []
        for e in self.state.enemy:
            ut = rs.units.get(e.get("jednostka"))
            if ut and int(e.get("liczba", 0)) > 0:
                out.append(Side(ut, int(e.get("stopien") or 0), int(e["liczba"])))
        return out

    def my_side(self) -> Side | None:
        ut = self.rs.units.get(self.state.my_unit)
        if ut is None:
            return None
        moves = self.rs.move_fragments if self.rs.combat.tired_attack else None
        return Side(ut, self.state.my_vet, 1, moves)

    # -------------------------------------------------------------- narzedzia

    def ai_snapshot(self) -> dict:
        st = self.state
        return {
            "tryb": st.mode,
            "zestaw_regul": st.ruleset,
            "teren_miasta": st.terrain,
            "teren_atakujacego": st.attacker_terrain,
            "w_miescie": st.in_city,
            "wielkosc_miasta": st.city_size,
            "okopani": st.fortified,
            "budowle": sorted(st.buildings),
            "ulepszenia_kafla": sorted(st.extras),
            "ustroj": st.gov,
            "poziom_technologiczny": (st.tech_depth if st.tech_depth is not None
                                      else self.rs.max_tech_depth()),
            "maks_poziom_technologiczny": self.rs.max_tech_depth(),
            "z_koszar": st.from_barracks,
            "awanse_obroncow": st.promotions,
            "moja_jednostka": {"jednostka": st.my_unit, "stopien": st.my_vet,
                               "liczba": st.planned},
            "sily_wroga": [dict(e) for e in st.enemy],
        }

    def ai_apply(self, patch: dict) -> dict:  # noqa: C901 - duzo prostych pol
        rs, st = self.rs, self.state
        warn: list[str] = []

        if "zestaw_regul" in patch:
            name = str(patch["zestaw_regul"])
            if name not in self._dirs:
                warn.append(f"nie znam zestawu reguł {name}")
            else:
                self.load_ruleset(name)
                rs = self.rs

        if "tryb" in patch:
            want = MODE_ATTACK if str(patch["tryb"]).startswith("szturm") else MODE_DEFENSE
            if want != st.mode:
                st.mode = want
                st.my_unit = None
                st.enemy = []
                self._ensure_defaults()

        if "poziom_technologiczny" in patch:
            depth = int(patch["poziom_technologiczny"])
            st.tech_depth = None if depth >= rs.max_tech_depth() else max(0, depth)
            self._ensure_defaults()

        lands = {t.name for t in rs.land_terrains()}
        for key, attr in (("teren_miasta", "terrain"),
                          ("teren_atakujacego", "attacker_terrain")):
            if key in patch:
                val = str(patch[key])
                if val not in lands:
                    warn.append(f"nie znam terenu {val}"
                                + self._hint(val, sorted(lands)))
                else:
                    setattr(st, attr, val)

        if "w_miescie" in patch:
            st.in_city = bool(patch["w_miescie"])
        if "wielkosc_miasta" in patch:
            st.city_size = max(1, min(40, int(patch["wielkosc_miasta"])))
        if "okopani" in patch:
            st.fortified = bool(patch["okopani"])
        if "z_koszar" in patch:
            st.from_barracks = bool(patch["z_koszar"])
        if "awanse_obroncow" in patch:
            st.promotions = bool(patch["awanse_obroncow"])
        if "ustroj" in patch:
            val = str(patch["ustroj"])
            if val not in rs.governments:
                warn.append(f"nie znam ustroju {val}")
            else:
                st.gov = val

        if "budowle" in patch:
            allowed = {b.name for b in rs.defensive_buildings()}
            want = [str(x) for x in (patch["budowle"] or [])]
            for missing in [w for w in want if w not in allowed]:
                warn.append(f"nie ma budowli {missing} w tym zestawie reguł"
                            + self._hint(missing, sorted(allowed)))
            st.buildings = [w for w in want if w in allowed]
        if "ulepszenia_kafla" in patch:
            allowed = {e.name for e in rs.defensive_extras()}
            want = [str(x) for x in (patch["ulepszenia_kafla"] or [])]
            for missing in [w for w in want if w not in allowed]:
                warn.append(f"nie ma ulepszenia {missing} w tym zestawie reguł")
            st.extras = [w for w in want if w in allowed]

        if "moja_jednostka" in patch:
            spec = patch["moja_jednostka"] or {}
            if spec.get("jednostka"):
                name = str(spec["jednostka"])
                ok = self._unit_allowed(name, mine=True)
                if not ok:
                    warn.append(f"jednostka {name} jest niedostępna po twojej stronie "
                                f"w trybie {st.mode} lub na tym poziomie technologicznym"
                                + self._hint(name, list(rs.units)))
                else:
                    st.my_unit = name
            if spec.get("stopien") is not None:
                ut = rs.units.get(st.my_unit)
                top = len(ut.vet_levels) - 1 if ut and ut.vet_levels else 0
                st.my_vet = max(0, min(int(spec["stopien"]), top))
            if spec.get("liczba") is not None:
                st.planned = max(1, min(200, int(spec["liczba"])))

        if "sily_wroga" in patch:
            entries = list(patch["sily_wroga"] or [])[:3]
            new = []
            for spec in entries:
                name = str(spec.get("jednostka", ""))
                if not self._unit_allowed(name, mine=False):
                    warn.append(f"jednostka {name} jest niedostępna po stronie wroga "
                                f"w trybie {st.mode}" + self._hint(name, list(rs.units)))
                    continue
                ut = rs.units[name]
                top = len(ut.vet_levels) - 1 if ut.vet_levels else 0
                new.append({
                    "jednostka": name,
                    "liczba": max(0, min(24, int(spec.get("liczba") or 1))),
                    "stopien": max(0, min(int(spec.get("stopien") or 0), top)),
                })
            st.enemy = [e for e in new if e["liczba"] > 0]

        out = self.ai_snapshot()
        if warn:
            out["ostrzezenia"] = warn
        return out

    def _unit_allowed(self, name: str, mine: bool) -> bool:
        rs, st = self.rs, self.state
        ut = rs.units.get(name)
        if ut is None:
            return False
        attacker_side_check = (st.mode == MODE_ATTACK) == mine
        # niebojowe jednostki nie atakują, ale bronią miasta
        if attacker_side_check and "NonMil" in ut.flags:
            return False
        if self.known_techs() is not None:
            if not all(t in self.known_techs() for t in ut.req_techs()):
                return False
        attacker_side = (st.mode == MODE_ATTACK) == mine
        return ut.attack > 0 if attacker_side else ut.defense > 0

    @staticmethod
    def _hint(value: str, pool: list[str]) -> str:
        close = difflib.get_close_matches(value, pool, n=3, cutoff=0.6)
        return f" (może chodziło o: {', '.join(close)})" if close else ""

    def ai_compute(self) -> dict:
        rs, st = self.rs, self.state
        sit = self.situation()
        mine = self.my_side()
        enemy = self.enemy_sides()
        if mine is None:
            return {"blad": "nie wybrano jednostki gracza"}
        if not enemy:
            return {"blad": "nie ustawiono sił przeciwnika"}

        if st.mode == MODE_ATTACK:
            res = siege(rs, mine, enemy, sit, promotions=st.promotions, trials=30000)
            d = res.duel
            return {
                "tryb": "szturm", "zestaw_regul": rs.name,
                "atakujacy": mine.utype.name,
                "obroncy": [f"{e.count}x {e.utype.name}" for e in enemy],
                "teren": sit.terrain.name,
                "budowle": sorted(sit.buildings | sit.player_buildings),
                "sila_ataku": round(d.attack_power / 10, 2),
                "sila_obrony": round(d.defense_power / 10, 2),
                "mnozniki_obrony": [
                    {"opis": m.label, "mnoznik": round(m.factor, 3),
                     "skladniki": m.details} for m in d.defense_bd.modifiers],
                "sila_ognia": {"atakujacy": d.attacker_fp, "obronca": d.defender_fp},
                "zycie": {"atakujacy": d.attacker_hp, "obronca": d.defender_hp},
                "szansa_pojedynku_proc": round(d.p_win * 100, 2),
                "srednio_atakow": round(res.mean_attacks, 2),
                "srednie_straty": round(res.mean_losses, 2),
                "koszt_strat_tarcze": round(res.mean_shields_lost),
                "potrzeba_50proc": res.attacks_for(0.5),
                "potrzeba_90proc": res.attacks_for(0.9),
                "potrzeba_99proc": res.attacks_for(0.99),
                "plan_jednostek": st.planned,
                "szansa_przy_planie_proc": round(res.p_with(st.planned) * 100, 2),
                "zajmie_miasto": rs.uclass_of(mine.utype).can_occupy_city,
                "uwagi": res.notes,
            }

        ut = mine.utype
        vet = veteran_build_level(rs, sit, ut) if st.from_barracks else st.my_vet
        res = defense_stand(rs, enemy, [Side(ut, vet, st.planned)], sit,
                            promotions=st.promotions, trials=30000)
        rng = np.random.default_rng(2024)
        mc, p_at = min_defenders(rs, enemy, Side(ut, vet), sit, 0.95,
                                 st.promotions, 12000, rng)
        main = max(enemy, key=lambda a: a.utype.attack * a.vet_fact())
        stops = max_wave_stopped(rs, main, [Side(ut, vet, st.planned)], sit,
                                 0.95, st.promotions, 8000, rng)
        d = res.duels[0][2] if res.duels else None
        return {
            "tryb": "obrona", "zestaw_regul": rs.name,
            "obronca": ut.name,
            "stopien_obroncy": ut.vet_levels[min(vet, len(ut.vet_levels) - 1)].name
            if ut.vet_levels else "green",
            "sily_wroga": [f"{a.count}x {a.utype.name}" for a in enemy],
            "teren": sit.terrain.name,
            "budowle": sorted(sit.buildings | sit.player_buildings),
            "sila_ataku_wroga": round(d.attack_power / 10, 2) if d else None,
            "sila_mojej_obrony": round(d.defense_power / 10, 2) if d else None,
            "mnozniki_obrony": [
                {"opis": m.label, "mnoznik": round(m.factor, 3), "skladniki": m.details}
                for m in d.defense_bd.modifiers] if d else [],
            "szansa_wroga_w_pojedynku_proc": round(d.p_win * 100, 2) if d else None,
            "minimum_obroncow_na_95proc": mc,
            "utrzymanie_przy_minimum_proc": round(p_at * 100, 2),
            "plan_obroncow": st.planned,
            "utrzymanie_przy_planie_proc": round(res.p_hold * 100, 2),
            "srednie_straty_wroga": round(res.mean_att_losses, 2),
            "srednie_straty_moje": round(res.mean_def_losses, 2),
            "garnizon_zatrzyma_napastnikow": stops,
            "uwagi": res.notes,
        }

    def ai_ranking(self, limit: int = 8) -> dict:
        rs, st = self.rs, self.state
        sit = self.situation()
        enemy = self.enemy_sides()
        if not enemy:
            return {"blad": "nie ustawiono sił przeciwnika"}

        if st.mode == MODE_ATTACK:
            opts = rank_units(rs, enemy, sit, self.known_techs(),
                              attacker_vet=st.my_vet, promotions=st.promotions,
                              trials=4000, occupiers_only=True)
            return {"tryb": "szturm",
                    "opis": "ile jednostek trzeba i ile z nich zginie",
                    "pozycje": [{
                        "jednostka": o.name,
                        "szansa_pojedynku_proc": round(o.p_single * 100, 1),
                        "potrzeba_na_90proc": o.attacks_90,
                        "srednie_straty": round(o.mean_losses, 2),
                        "koszt_strat_tarcze": round(o.shields_lost),
                        "inwestycja_tarcze": o.invest_90,
                        "zajmie_miasto": o.can_occupy,
                        "technologia": o.req_techs,
                    } for o in opts[:limit]]}

        opts = rank_defenders(rs, enemy, sit, self.known_techs(), 0.95,
                              st.promotions, 3000, st.from_barracks)
        return {"tryb": "obrona",
                "opis": "najmniejszy garnizon, który utrzyma miasto z pewnością 95%",
                "pozycje": [{
                    "jednostka": o.name,
                    "minimum_sztuk": o.min_count,
                    "utrzymanie_proc": round(o.p_at_min * 100, 2),
                    "jedna_sztuka_zatrzyma": o.stops_alone,
                    "obrona": round(o.defense_power / 10, 1),
                    "koszt_tarcze": o.shields,
                    "stopien": o.vet_name,
                    "leczy_sie_do_pelna": o.heals_fully,
                    "technologia": o.req_techs,
                } for o in opts[:limit]]}

    def ai_resilience(self) -> dict:
        rs, st = self.rs, self.state
        if st.mode != MODE_DEFENSE:
            return {"blad": "tabela wytrzymałości działa tylko w trybie obrony"}
        sit = self.situation()
        ut = rs.units.get(st.my_unit)
        if ut is None:
            return {"blad": "nie wybrano jednostki obronnej"}
        vet = veteran_build_level(rs, sit, ut) if st.from_barracks else st.my_vet
        threats = [u for u in rs.units_available(self.known_techs())
                   if u.attack > 0 and "NonMil" not in u.flags
                   and rs.uclass_of(u).can_occupy_city]
        threats.sort(key=lambda u: (rs.unit_tech_depth(u), -u.attack))
        threats = threats[:7]
        rng = np.random.default_rng(77)
        rows = []
        for m in (1, 2, 3, 4, 6):
            row = {"garnizon": f"{m} × {ut.name}"}
            for threat in threats:
                row[threat.name] = max_wave_stopped(
                    rs, Side(threat, 0), [Side(ut, vet, m)], sit, 0.95,
                    st.promotions, 2500, rng)
            rows.append(row)
        return {"opis": "ilu napastników odeprze garnizon przy 95% pewności",
                "obronca": ut.name,
                "stopien": ut.vet_levels[vet].name if ut.vet_levels else "green",
                "wiersze": rows}

    def ai_catalog(self, what: str = "jednostki") -> dict:
        rs = self.rs
        if what == "zestawy":
            return {"zestawy": sorted(self._dirs)}
        if what == "teren":
            return {"teren": [{"nazwa": t.name, "obrona_proc": t.defense_bonus,
                               "koszt_ruchu": t.movement_cost}
                              for t in rs.land_terrains()]}
        if what == "budowle":
            return {"budowle": [{"nazwa": b.name, "koszt": b.build_cost,
                                 "cud": b.is_wonder, "technologia": b.req_techs()}
                                for b in rs.defensive_buildings()]}
        if what == "ulepszenia":
            return {"ulepszenia": [{"nazwa": e.name, "obrona_proc": e.defense_bonus}
                                   for e in rs.defensive_extras()]}
        if what == "ustroje":
            return {"ustroje": list(rs.governments)}
        known = self.known_techs()
        return {
            "poziom_technologiczny": self.ai_snapshot()["poziom_technologiczny"],
            "jednostki": [{
                "nazwa": u.name, "atak": u.attack, "obrona": u.defense,
                "zycie": u.hitpoints, "koszt": u.build_cost,
                "technologia": u.req_techs(),
            } for u in sorted(rs.units_available(known),
                              key=lambda u: (rs.unit_tech_depth(u), u.name))
                if u.attack > 0 or u.defense > 0],
        }

    def ai_unit(self, name: str) -> dict:
        rs = self.rs
        ut = rs.units.get(name)
        if ut is None:
            close = difflib.get_close_matches(name, list(rs.units), n=5, cutoff=0.6)
            close += [n for n in rs.units if name.lower() in n.lower() and n not in close]
            return {"blad": f"nie ma jednostki {name} w zestawie {rs.name}",
                    "podobne": close[:5]}
        uc = rs.uclass_of(ut)
        return {
            "nazwa": ut.name, "klasa": uc.name,
            "atak": ut.attack, "obrona": ut.defense, "zycie": ut.hitpoints,
            "sila_ognia": ut.firepower, "ruch": ut.move_rate,
            "koszt": ut.build_cost, "utrzymanie": ut.uk_shield,
            "technologia": ut.req_techs(),
            "zajmie_miasto": uc.can_occupy_city,
            "premia_terenu": uc.terrain_defense,
            "flagi": sorted(ut.flags),
            "bonusy": [{"wobec_flagi": b.flag, "typ": b.type, "wartosc": b.value}
                       for b in ut.bonuses],
            "stopnie": [{"nazwa": lv.name, "mnoznik": lv.power_fact / 100}
                        for lv in ut.vet_levels],
        }

    # -------------------------------------------------------------- pomocnicze

    def _intel_apply_ruleset(self, name: str) -> str:
        """Po wczytaniu zapisu przestawia kalkulator na jego zestaw regul."""
        if name in self._dirs and name != self.state.ruleset:
            self.load_ruleset(name)
            return name
        return self.state.ruleset

    def _intel_ruleset(self):
        return self.rs

    def context_note(self) -> str:
        s = self.ai_snapshot()
        return (f"tryb={s['tryb']}, zestaw_regul={s['zestaw_regul']}, "
                f"teren={s['teren_miasta']}, budowle={s['budowle']}, "
                f"moja_jednostka={s['moja_jednostka']['jednostka']}, "
                f"sily_wroga={s['sily_wroga']}")


def duel_summary(bridge: HeadlessBridge) -> dict:
    """Skrocony pojedynek 1 na 1 - przydatny do szybkich pytan."""
    mine, enemy = bridge.my_side(), bridge.enemy_sides()
    if mine is None or not enemy:
        return {"blad": "brak kompletu jednostek"}
    sit = bridge.situation()
    att, dfn = (mine, enemy[0]) if bridge.state.mode == MODE_ATTACK else (enemy[0], mine)
    d = duel(bridge.rs, att, dfn, sit)
    return {"atakujacy": att.utype.name, "obronca": dfn.utype.name,
            "sila_ataku": round(d.attack_power / 10, 2),
            "sila_obrony": round(d.defense_power / 10, 2),
            "szansa_atakujacego_proc": round(d.p_win * 100, 2)}
