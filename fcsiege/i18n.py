"""Dwujezycznosc: polski i angielski.

Polski jest jezykiem zrodlowym - wszystkie napisy w kodzie sa po polsku, a ten
modul tlumaczy je na angielski. Dzieki temu nie ma sztucznych identyfikatorow
w rodzaju "msg.attack.title": kluczem jest sam polski napis, wiec kod czyta sie
tak samo jak wczesniej.

Tlumaczymy trzy rozne rzeczy i kazda inaczej:

  UI      napisy w oknie                     -> `_("Tryb")`
  KEYS    klucze w odpowiedziach narzedzi    -> `translate(wynik)`
  TOOLS   nazwy i opisy narzedzi asystenta   -> `tool_name()`, `tool_desc()`

Nazwy z zestawu regul (Monarchy, Output_Bonus, Knights) NIE sa tlumaczone -
one naleza do gry, nie do nas. Dziala to samo z siebie: czego nie ma
w slowniku, przechodzi bez zmian.
"""

from __future__ import annotations

import contextvars
import re

LANGS = ("pl", "en")

# Kontekstowa, nie globalna: serwer HTTP obsluguje zadania w wielu watkach,
# a kazde moze byc w innym jezyku. Watek dostaje wlasny kontekst, wiec
# ustawienie w jednym nie przecieka do drugiego.
_current_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "fcsiege_language", default="pl")


def set_language(lang: str) -> str:
    _current_var.set(lang if lang in LANGS else "pl")
    return _current_var.get()


def language() -> str:
    return _current_var.get()


def normalize(lang: str | None) -> str:
    """Przyjmuje 'en', 'en-GB', 'EN;q=0.9' i tak dalej."""
    if not lang:
        return "pl"
    head = re.split(r"[,;]", str(lang))[0].strip().lower()
    return "en" if head.startswith("en") else "pl"


# ══════════════════════════════════════════════════════ napisy w interfejsie

UI: dict[str, str] = {
    # --- tryby i naglowki
    "szturm": "assault",
    "obrona": "defense",
    "Szturm na miasto": "Assault on a city",
    "Obrona miasta": "City defense",
    "Tryb": "Mode",
    "Zestaw reguł": "Ruleset",
    "Poziom technologiczny": "Tech level",
    "próg {n}": "step {n}",
    "całe drzewo": "whole tree",
    "{n} jedn": "{n} units",
    "{n} bud": "{n} bldgs",
    "{n} cud": "{n} wonders",
    "{n} tech": "{n} techs",
    "z zapisu": "from savegame",
    # --- scenariusz
    "Scenariusz": "Scenario",
    "Teren miasta": "City terrain",
    "Teren atakującego": "Attacker terrain",
    "Wielkość miasta": "City size",
    "Ulepszenia kafla": "Tile improvements",
    "Budowle": "Buildings",
    "Ustrój": "Government",
    "W mieście": "In the city",
    "Okopani": "Fortified",
    "Z koszar": "From barracks",
    "Moja jednostka": "My unit",
    "Siły wroga": "Enemy forces",
    "Obrońcy": "Defenders",
    "Atakujący": "Attackers",
    "Jednostka": "Unit",
    "Liczba": "Count",
    "Stopień": "Veterancy",
    # --- wyniki
    "Wynik": "Result",
    "Siła ataku": "Attack strength",
    "Siła obrony": "Defense strength",
    "Siła ognia": "Firepower",
    "Szansa pojedynku": "Duel odds",
    "Potrzeba jednostek": "Units needed",
    "Średnie straty": "Average losses",
    "Koszt strat": "Cost of losses",
    "Zajmie miasto": "Takes the city",
    "na 50%": "for 50%",
    "na 90%": "for 90%",
    "na 99%": "for 99%",
    "Ranking": "Ranking",
    "Tabela wytrzymałości": "Resilience table",
    "Mnożniki obrony": "Defense multipliers",
    "Mnożniki": "Multipliers",
    "Składniki": "Components",
    # --- biezaca partia
    "Bieżąca partia": "Current game",
    "Wczytaj zapis": "Load savegame",
    "Pełny wgląd — świadomie chituję": "Full intel — I am knowingly cheating",
    "Mgła wojny": "Fog of war",
    "pełny wgląd (świadome chity)": "full intel (deliberate cheating)",
    "mgła wojny": "fog of war",
    "Moje wojska": "My forces",
    "Wywiad": "Intelligence",
    "Miasta": "Cities",
    "Technologie": "Technologies",
    "Korupcja": "Waste",
    "Plan budowy": "Build plan",
    # --- czat
    "Asystent": "Assistant",
    "Zapytaj o cokolwiek…": "Ask anything…",
    "Wyślij": "Send",
    "Przerwij": "Stop",
    "Wyczyść rozmowę": "Clear conversation",
    "Zaloguj się": "Sign in",
    "Klucz API": "API key",
    "Zapisz klucz": "Save key",
    "Model": "Model",
    "Myśli…": "Thinking…",
    "Model odmówił odpowiedzi": "The model declined to answer",
    "Brak klucza API": "No API key",
    # --- okno: naglowek, karty, zakladki
    'Atak': 'Attack',
    'Budowle i cuda obrońcy': 'Defender buildings and wonders',
    'Czym uderzyć': 'What to strike with',
    'Doświadczenie': 'Veterancy',
    'FCSiege': 'FCSiege',
    'FCSiege — kalkulator szturmu (Freeciv)': 'FCSiege — city assault calculator (Freeciv)',
    'Garnizon': 'Garrison',
    'Ile': 'How many',
    'Ile jednostek zamierzasz wysłać': 'How many units you intend to send',
    'Język interfejsu': 'Interface language',
    'Klucz zapisany. Możesz zadawać pytania.': 'Key saved. You can start asking questions.',
    'Miasto i teren': 'City and terrain',
    'Myślę…': 'Thinking…',
    'Nie wczytano zapisu.': 'No savegame loaded.',
    'Obrona': 'Defense',
    'Obrońcy awansują po wygranej obronie': 'Defenders gain veterancy after a won defense',
    'Obrońcy budowani w tym mieście': 'Defenders built in this city',
    'Obrońcy okopani (fortify)': 'Defenders fortified',
    'Obrońcy stoją w mieście': 'Defenders stand inside the city',
    'Odłącz klucz': 'Detach key',
    'POZIOM TECHNOLOGICZNY': 'TECH LEVEL',
    'Plan': 'Plan',
    'Połączenie z Claude': 'Connection to Claude',
    'Przerywam…': 'Stopping…',
    'Rozbicie sił': 'Strength breakdown',
    'Skąd atakować': 'Where to attack from',
    'Skąd atakujesz': 'Where you attack from',
    'Starcie jednostka na jednostkę': 'Unit against unit',
    'Stopień weterana wynika wtedy z koszar i cudów w tym mieście.': 'Veterancy then follows from the barracks and wonders in that city.',
    'Szansa zdobycia': 'Odds of capture',
    'Szturm': 'Assault',
    'Szturm i obrona miasta, liczone z plików reguł Freeciva': "Assault and defense of a city, computed from Freeciv's own ruleset files",
    'Teren jednostki szturmowej': 'Terrain of the assaulting unit',
    'Teren pod miastem': 'Terrain under the city',
    'Tylko jednostki, które mogą samodzielnie zająć miasto': 'Only units that can take a city on their own',
    'Ustaw liczbę 0, żeby pominąć dany typ.': 'Set the count to 0 to skip a type.',
    'Ustrój obrońcy': "Defender's government",
    'Ułamki ruchu w chwili ataku': 'Movement fragments at the moment of attack',
    'Wczytaj najnowszy zapis': 'Load the newest savegame',
    'Wczytuję…': 'Loading…',
    'Wskazówki': 'Advice',
    'Wyczyść': 'Clear',
    'ZESTAW REGUŁ': 'RULESET',
    'Zapytaj o scenariusz… (Enter wysyła, Shift+Enter nowa linia)': 'Ask about the scenario… (Enter sends, Shift+Enter for a new line)',
    'Założenia obliczeń': 'Assumptions',
    'brak w tym zestawie reguł': 'absent from this ruleset',
    'liczba atakujących jednostek': 'number of attacking units',
    'liczba obrońców w mieście': 'number of defenders in the city',
    'średnie straty': 'average losses',
    # --- przelaczanie trybu szturm/obrona
    'Bronię się w mieście': 'I am defending inside the city',
    'Czym bronić': 'What to defend with',
    'Ilu obrońców zostawiasz': 'How many defenders you leave',
    'Mój obrońca': 'My defender',
    'Skąd naciera wróg': 'Where the enemy advances from',
    'Teren, z którego wróg naciera': 'Terrain the enemy advances from',
    'Wpisz siły, którymi wróg uderzy w jednej turze.': 'Enter the forces the enemy will strike with in one turn.',
    'Wytrzymałość': 'Resilience',
    'obrońców (95%)': 'defenders (95%)',
    'Atakujący': 'Attacker',
    'Garnizon wroga': 'Enemy garrison',
    'Miasto wroga i teren': 'Enemy city and terrain',
    'Moje miasto': 'My city',
    'Moje budowle i cuda': 'My buildings and wonders',
    'Jednostka szturmowa': 'Assaulting unit',
    'Jednostka obronna': 'Defending unit',
    'Plan obrony': 'Defense plan',
    'Szansa utrzymania': 'Odds of holding',
    'potrzeba (90%)': 'needed (90%)',
    'straty wroga': 'enemy losses',
    'koszt strat': 'cost of losses',
    'zatrzyma': 'stops',
    'pojedynek': 'duel',
    'utrzymanie': 'holds',
    # --- komunikaty bledow (API, MCP, narzedzia)
    'nie ma ścieżki': 'no such path',
    'nie ma narzędzia': 'no such tool',
    'brak lub zły token': 'missing or invalid token',
    'ciało żądania jest za duże': 'the request body is too large',
    'oczekiwano obiektu JSON': 'a JSON object was expected',
    'nieprawidłowy JSON': 'malformed JSON',
    'wynik narzędzia': 'tool result',
    'nieznane narzędzie': 'unknown tool',
    'brak gracza ludzkiego': 'no human player in this savegame',
    'zapis nie zawiera drzewa technologii': 'the savegame has no technology tree',
    'brak miast': 'no cities',
    # --- ogolne
    "Gotowe": "Ready",
    "Liczę…": "Computing…",
    "Błąd": "Error",
    "Uwaga": "Note",
    "Anuluj": "Cancel",
    "Zamknij": "Close",
    "brak": "none",
    "tak": "yes",
    "nie": "no",
}


def _(text: str, **fmt) -> str:
    """Tlumaczy napis interfejsu; nieznany zwraca bez zmian."""
    out = UI.get(text, text) if language() == "en" else text
    return out.format(**fmt) if fmt else out


# ═══════════════════════════════════════════════ klucze w wynikach narzedzi

KEYS: dict[str, str] = {
    # --- stan i scenariusz
    "tryb": "mode", "zestaw_regul": "ruleset", "teren_miasta": "city_terrain",
    "teren_atakujacego": "attacker_terrain", "w_miescie": "in_city",
    "wielkosc_miasta": "city_size", "okopani": "fortified",
    "budowle": "buildings", "ulepszenia_kafla": "tile_extras",
    "ustroj": "government", "poziom_technologiczny": "tech_level",
    "maks_poziom_technologiczny": "max_tech_level", "z_koszar": "from_barracks",
    "moja_jednostka": "my_unit", "sily_wroga": "enemy_forces",
    "jednostka": "unit", "jednostki": "units", "liczba": "count",
    "stopien": "veterancy", "sztuk": "pieces", "nazwa": "name",
    "opis": "description", "typ_trasy": "route_type",
    # --- walka
    "atak": "attack", "obrona": "defense", "atakujacy": "attacker",
    "obronca": "defender", "obroncy": "defenders", "sila_ataku": "attack_power",
    "sila_obrony": "defense_power", "sila_ognia": "firepower",
    "szansa_pojedynku_proc": "duel_win_pct",
    "szansa_przy_planie_proc": "plan_win_pct",
    "potrzeba_50proc": "needed_for_50pct", "potrzeba_90proc": "needed_for_90pct",
    "potrzeba_99proc": "needed_for_99pct", "potrzeba_na_90proc": "needed_for_90pct",
    "srednie_straty": "average_losses", "srednio_atakow": "average_attacks",
    "koszt_strat_tarcze": "loss_cost_shields", "zajmie_miasto": "takes_the_city",
    "mnozniki_obrony": "defense_multipliers", "mnoznik": "multiplier",
    "premia_terenu": "terrain_bonus", "mury": "walls", "ranne": "wounded",
    "zycie": "hit_points", "ruch": "movement", "awanse_obroncow": "defender_veterancy",
    "stopnie": "veteran_levels", "bonusy": "bonuses", "flagi": "flags",
    "klasa": "unit_class", "skladniki": "components", "mnozniki": "multipliers",
    # --- zapis gry, wywiad
    "plik": "file", "tura": "turn", "rok": "year", "wersja_gry": "game_version",
    "tryb_wywiadu": "intel_mode", "ja": "me", "przywodca": "leader",
    "nacja": "nation", "zloto": "gold", "miast": "cities", "jednostek": "units",
    "dyplomacja": "diplomacy", "stan_dyplomatyczny": "diplomatic_state",
    "zywy": "alive", "znane_miasta": "known_cities", "stolica": "capital",
    "miasto": "city", "miasta": "cities", "rozmiar": "size",
    "moje_miasto": "my_city", "moja_sytuacja": "my_situation",
    "zestaw_regul_ustawiony": "ruleset_applied", "uwaga": "note", "uwagi": "notes",
    "blad": "error", "razem_jednostek": "total_units", "wg_typu": "by_type",
    "pozycje": "positions", "obsadzone": "garrisoned", "kierunek": "direction",
    "dystans": "distance", "min_dystans": "min_distance", "cele": "targets",
    "partner": "partner", "rozmiar_partnera": "partner_size",
    # --- technologie i epoki
    "epoka": "era", "epoki": "eras", "prog": "step", "maks_prog": "max_step",
    "nowe": "new", "nowe_na_tym_progu": "new_at_this_step",
    "technologia": "technology", "technologie": "technologies",
    "znane": "known", "znanych_technologii": "techs_known",
    "epoka_wg_mediany": "era_by_median", "epoka_wg_najglebszej": "era_by_deepest",
    "wyprzedzaja_epoke": "ahead_of_era", "glebokosc": "depth",
    "badane_teraz": "researching_now", "cel_badan": "research_goal",
    "bulbs_zebrane": "bulbs_stored", "koszt_kolejnej": "next_tech_cost",
    "tempo_bulbs_na_ture": "bulbs_per_turn", "tur_do_konca": "turns_remaining",
    "najblizsze_oplacalne": "closest_worthwhile", "lancuch": "chain",
    "brakuje_technologii": "techs_missing", "koszt_bulbs": "bulb_cost",
    "tur_przy_obecnym_tempie": "turns_at_current_rate", "odblokowuje": "unlocks",
    "filtr_z_zapisu": "filter_from_savegame", "waga": "weight",
    "technologie_z_zapisu": "techs_from_savegame", "cud": "wonder", "cuda": "wonders",
    "budynki": "buildings", "budynek": "building",
    "mam_technologie": "have_technology", "wymaga_technologii": "requires_technology",
    "dostepny_teraz": "available_now", "brakujace_technologie": "missing_technologies",
    "propozycje": "suggestions",
    # --- korupcja
    "marnuje_tarcz_proc": "shield_waste_pct", "marnuje_handlu_proc": "trade_waste_pct",
    "dystans_do_wladzy": "distance_to_seat", "osrodki_wladzy": "seats_of_government",
    "ma_ratusz": "has_courthouse", "z_ratuszem_tarcze_proc": "with_courthouse_shield_pct",
    "z_ratuszem_handel_proc": "with_courthouse_trade_pct",
    "ratusz_odzyska_tarcz": "courthouse_recovers_shields",
    "ratusz_zwroci_sie_w_turach": "courthouse_pays_back_in_turns",
    "ratusz_oplaca_sie_w": "courthouse_worth_it_in",
    "budynki_znoszace_korupcje": "waste_reducing_buildings",
    "dziala_na": "applies_to", "efekt": "effect", "efekty": "effects",
    "nazwa_wewnetrzna": "rule_name", "procent": "percent",
    # --- plan budowy
    "metropolia": "metropolis", "kolonie": "colonies", "zasada": "principle",
    "zasady": "rules", "gdzie": "where", "dlaczego": "why",
    "zasieg_efektu": "effect_range", "mam_juz_w": "already_have_in",
    "tarcze_na_ture": "shields_per_turn", "nadwyzka_tarcz": "shield_surplus",
    "buduje": "building_now", "buduje_teraz": "building_now",
    "co_buduja_miasta": "what_cities_build", "plan_jednostek": "unit_plan",
    # --- miasta, zywnosc, utrzymanie
    "limit_wielkosci": "size_cap", "zapas_zywnosci": "food_stock",
    "zapas_do_limitu": "room_to_cap", "deficyt_zywnosci": "food_deficit",
    "miast_z_deficytem": "cities_in_deficit", "miast_na_granicy": "cities_at_margin",
    "jednostek_na_zywnosci": "units_on_food", "jednostki_jedzace": "food_eating_units",
    "jednostki_bez_zywnosci": "food_free_units",
    "darmowe_utrzymanie_zywnosci": "free_food_upkeep",
    "darmowych_na_miasto": "free_per_city", "zywnosc": "food",
    "zasada_darmowego_utrzymania": "free_upkeep_rule",
    "utrzymanie_wojsk": "military_upkeep", "utrzymanie": "upkeep",
    "utrzymanie_teraz": "upkeep_now", "utrzymanie_po": "upkeep_after",
    "oszczednosc_na_ture": "saving_per_turn", "placisz_w": "paid_in",
    "utrzymanie_placone_w": "upkeep_paid_in", "kara_za_wielkosc": "empire_size_penalty",
    "miast_do_kolejnej_kary": "cities_to_next_penalty",
    "poziomow_kary_przy_twoich_miastach": "penalty_levels_at_your_city_count",
    # --- rozwiazywanie jednostek
    "zwrot_procent": "recovery_percent", "zwrot_tarcz": "shields_recovered",
    "razem_tarcz": "total_shields", "kandydaci": "candidates",
    "kandydatow_lacznie": "candidates_total", "powod": "reason",
    "koszt_budowy": "build_cost", "gdzie_rozwiazac": "where_to_disband",
    "jednostek_do_rozwiazania_na_miejscu": "units_to_disband_here",
    "brakuje": "missing", "brakuje_w_miastach": "missing_in_cities",
    "najtanszy_brak": "cheapest_missing", "uwolniona_zywnosc": "food_freed",
    "co_za_to_kupisz": "what_this_buys", "inwestycja_tarcze": "shield_investment",
    "ile_za_zwrot": "how_many_for_the_refund",
    # --- przejezdnosc i drogi
    "wchodzi_na": "can_enter", "nie_wchodzi_bez_drogi": "blocked_without_road",
    "moje_sztuki_wg_obszaru": "my_units_by_region", "obszar": "region",
    "glowny_obszar": "main_region", "moich_sztuk_w_tym_obszarze": "my_units_in_region",
    "dojda": "can_reach", "odcietych_sztuk": "cut_off_units",
    "polaczenia_drogowe": "road_links", "kafle": "tiles",
    "kafli_do_zbudowania": "tiles_to_build", "tur_pracy": "worker_turns",
    "lacznie_tur_pracy": "total_worker_turns",
    "robotnikow_na_jedna_ture": "workers_for_one_turn",
    "przy_8_robotnikach_tur": "turns_with_8_workers",
    # --- handel
    "max_tras_na_miasto": "max_routes_per_city", "wolnych_slotow": "free_slots",
    "w_ilu_miastach": "in_how_many_cities", "premia_jednorazowa": "one_off_bonus",
    "procent_wartosci": "percent_of_value", "miedzykontynentalna": "intercontinental",
    "koszt_na_ture": "cost_per_turn", "bez_wartosci": "worthless",
    "przyklad": "example", "ocena": "verdict",
    # --- ustroje
    "obecny_ustroj": "current_government", "ustroje": "governments",
    "wartosc": "value", "wartosci": "values", "warunki": "requirements",
    "czego_nie_wiem": "what_i_do_not_know", "co": "what", "krok": "step",
    "koszt": "cost", "teren": "terrain", "stan": "state", "typ": "type",
    "zniknie_przy_zdobyciu": "destroyed_if_captured",
    "wojsko_w_polu": "units_in_the_field", "limit_na_miasto": "limit_per_city",
    "wg_miasta_macierzystego": "by_home_city", "miasto_macierzyste": "home_city",
    "jednostek": "units", "wolno_bez_kosztu": "free_of_charge",
    "niezadowolonych_gdy_wszystkie_wyjda": "unhappy_if_all_march_out",
    "miast_ponad_limit": "cities_over_the_limit",
    "tur_marszu": "march_turns",
    "faza": "stage", "nastawienie": "posture",
    "strategia": "strategy", "sytuacja": "situation",
    "zawieszenie_broni_wygasa": "ceasefire_expires", "za_tur": "in_turns",
    "obcych_jednostek_zdolnych_do_ataku": "foreign_units_able_to_attack",
    "wolnych_miejsc_pod_miasto": "free_city_sites", "robotnikow": "workers",
    "zaleglosc_robot_w_turach": "work_backlog_turns",
    "miast_bez_garnizonu": "cities_without_garrison",
    "tarcz_na_ture": "shields_per_turn",
    "budowle_bez_utrzymania_do_wziecia": "zero_upkeep_buildings_available", "co_to_znaczy": "what_it_means",
    "dostepne_strategie": "available_strategies", "kolejnosc": "order",
    "ocena": "score", "ocena_na_technologie": "score_per_tech",
    "ocena_na_ture": "score_per_turn", "technologii_do_zdobycia": "techs_to_acquire",
    "bulbs_na_ture": "bulbs_per_turn", "jak_liczone": "how_it_is_scored", "podatki_teraz": "rates_now", "podatki_rada": "rates_advice",
    "podatki": "tax", "nauka": "science", "luksus": "luxury",
    "maksymalny_suwak": "rate_cap", "kara_despotyzmu_na_kafel": "per_tile_penalty",
    "badania_cel": "research_goal", "lepsze_ustroje": "better_governments",
    "produkcja": "production", "buduj": "build", "buduje_teraz": "building_now",
    "ma_garnizon": "has_garrison", "zmiana": "change",
    "budynki_bez_utrzymania_do_wziecia": "free_upkeep_buildings_available",
    "do_zmiany": "to_change", "inwestycje": "investments",
    "wojna_z": "at_war_with", "kara_za_miast": "penalty_from_cities",
    "kara_kafla": "tile_penalty", "maks_suwak": "rate_cap",
    "ile_technologii": "techs_needed", "ustroj": "government",
    "rozkazy": "orders", "odlozone": "postponed", "cel": "target",
    "wyslij_jednostek": "send_units", "w_tym_do_walki": "of_which_to_fight",
    "w_tym_garnizon": "of_which_garrison", "skad": "from",
    "dotra_w_turach": "arrive_in_turns", "oplacalnosc": "value_per_unit",
    "potrzeba_atakow_90proc": "attacks_needed_90pct", "z_rezerwa": "with_reserve",
    "moich_zaczepnych": "my_offensive_units", "zaangazowanych": "committed",
    "w_rezerwie": "in_reserve", "fronty": "fronts", "wartosc": "value",
    "tura_zasiegu": "reach_turns",
    "uklady": "treaties", "tur_do_zmiany": "turns_to_change",
    "co_sie_stanie": "what_will_happen", "ryzyko": "risk",
    "najblizszy_kiedykolwiek": "closest_ever", "ambasada": "embassy",
    "ich_sila": "their_strength", "zdolnych_do_ataku": "able_to_attack",
    "moje_jednostki_do_rozwiazania": "my_units_to_be_disbanded",
    "przeslanki": "considerations", "moja_sila": "my_strength",
    "wygasa_do_wojny": "expires_into_war", "stanie_sie_pokojem": "becomes_peace",
    "jak_to_dziala": "how_it_works", "ludnosci": "population",
    "limit_wielkosci": "size_cap", "deficyt_utrzymania": "upkeep_deficit",
    "zywnosc_z_obrabianych_kafli": "food_from_worked_tiles",
    "kafli_w_zasiegu": "tiles_in_radius",
    "zjadaja_obywatele": "citizens_eat", "powod": "reason",
    "kafle": "tiles", "kto_moze_pracowac": "who_can_work",
    "plan_robot": "worker_plan", "prac_lacznie": "jobs_total",
    "zywnosci_do_zyskania": "food_to_gain", "tur_pracy_lacznie": "worker_turns_total",
    "zysk_dostepny_teraz": "gain_available_now",
    "zysk_po_zdobyciu_technologii": "gain_after_research",
    "najlepsza_praca": "best_job", "opcje": "options", "praca": "job",
    "daje": "adds", "tur_pracy": "worker_turns", "wymaga": "requires",
    "dostepne_teraz": "available_now", "tur_na_zywnosc": "turns_per_food",
    "daje_zywnosci": "adds_food", "brakuje": "missing", "surowiec": "resource",
    "polowe": "field_work", "przemiana": "terrain_transform",
    "moge_budowac": "can_build", "mam_w_grze": "have_in_game",
    "dostepne": "available", "aktywny": "active", "dostawca": "provider",
    "protokol": "protocol", "skad_klucz": "key_source", "ma_klucz": "has_key",
    "modele": "models", "zmienne_srodowiskowe": "env_vars",
    "gdzie_wziac_klucz": "where_to_get_a_key", "format_klucza": "key_format",
    "kafel": "tile", "zywnosc_teraz": "food_now", "irygowany": "irrigated",
    "jak_czytac": "how_to_read",
    "alertow": "alerts_count", "krytycznych": "critical", "pilnych": "urgent",
    "waga": "severity", "tur_do_szkody": "turns_to_harm", "rodzaj": "kind",
    "co_sie_dzieje": "what_is_happening", "rada": "advice",
    "zasada_darmowej_zywnosci": "free_food_rule",
    "wartosc_zdobyczy": "spoils_value", "ocena_zdobyczy": "spoils_score",
    "drog_wokol": "roads_around", "polaczone_z_moja_siecia": "linked_to_my_network",
    "dystans_do_mojej_stolicy": "distance_to_my_capital", "otoczenie": "surroundings",
    "nadmorskie": "coastal", "przed_kim": "against_whom",
    "stopien_przy_budowie": "veterancy_when_built",
    "jedna_sztuka_zatrzyma": "one_unit_stops",
    "sztuk_by_utrzymac": "units_to_hold", "tarcz_lacznie": "shields_total",
    "szansa_przy_jednej_proc": "odds_with_one_pct",
    "koszt_na_zatrzymanego": "cost_per_attacker_stopped",
    "niezadowolonych_gdy_w_polu": "unhappy_when_in_field",
    "utrzymanie_tarcze": "shield_upkeep", "utrzymanie_zywnosc": "food_upkeep",
    "tur_dostawy": "delivery_turns", "czym": "how", "przez_port": "via_port",
    "moje_miasto_portowe": "my_city_is_port", "partner_portowy": "partner_is_port",
    "ocena_na_ture": "score_per_turn",
    "punkty_zborne": "rally_points", "cele_wroga_w_zasiegu": "enemy_targets_in_reach",
    "moich_w_zasiegu": "mine_in_reach", "najszybciej_tur": "fastest_turns",
    "wg_typu": "by_type", "grupy": "groups", "sredni_zasieg_kafli": "average_reach_tiles",
    "rozciagniecie": "overstretch", "w_polu": "in_the_field", "w_miastach": "in_cities",
    "limit_bez_kosztu_na_miasto": "free_limit_per_city",
    "jednostek_w_polu": "units_in_the_field", "niezadowolonych": "unhappy",
    "odciete": "cut_off", "jednostek_bojowych": "combat_units",
    "cel_wojny": "war_target", "tury_zasiegu": "reach_turns",
    "moje_jednostki_bojowe": "my_combat_units", "cele": "targets",
    "podsumowanie_celow": "target_summary", "miast_lacznie": "cities_total",
    "bez_murow": "without_walls", "bez_garnizonu": "without_garrison",
    "obroncow_lacznie": "defenders_total",
    "osiagalnych_w_tylu_turach": "reachable_in_those_turns",
    "obroncow": "defenders", "moich_w_zasiegu": "mine_within_reach",
    "najblizszy_dystans": "nearest_distance",
    "koszt_szczescia": "happiness_cost", "zadowolonych_bazowo": "content_by_default",
    "stan_wojenny": "martial_law", "garnizon": "garrison",
    "zadowolonych_teraz": "content_now",
    "zadowolonych_po_wymarszu": "content_after_march_out",
    "niepokrytych_po_wymarszu": "uncovered_after_march_out",
    "ma_swiatynie": "has_temple",
    "miast_z_niedoborem_po_wymarszu": "cities_short_after_march_out",
    "co_zmieni_czekanie": "what_waiting_changes", "buduja_mury": "building_walls",
    "buduja_osadnikow": "building_settlers", "nacje": "nations", "tury": "turns",
    "x": "x", "y": "y",
    # --- argumenty narzedzi
    "czego": "of_what", "limit": "limit", "pelny_wglad": "full_intel",
    "promien": "radius", "sciezka": "path", "zastosuj": "apply",
    "tylko_miedzykontynentalne": "intercontinental_only",
}

# klucze budowane dynamicznie, np. "rozmiar 12"
_DYNAMIC: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^rozmiar (\d+)$"), r"size \1"),
]

# wartosci tekstowe, ktore sa nasze, a nie z zestawu regul
VALUES: dict[str, str] = {
    "szturm": "assault", "obrona": "defense",
    "pełny wgląd (świadome chity)": "full intel (deliberate cheating)",
    "mgła wojny": "fog of war",
    "metropolia": "metropolis", "kolonie": "colonies", "wszędzie": "everywhere",
    "gdziekolwiek": "anywhere", "najpierw metropolia": "metropolis first",
    "metropolia (część działa wszędzie)": "metropolis (part works everywhere)",
    "miasto": "city", "gracz": "player", "mieszany": "mixed",
    "złocie": "gold", "tarczach": "shields", "żywności": "food",
    "efekt procentowy od produkcji miasta":
        "percentage of the city's own output",
    "efekt stały, taki sam w każdym mieście":
        "flat effect, identical in every city",
    "część efektu skaluje się z produkcją":
        "part of the effect scales with output",
    "zbija gotową stratę": "reduces the resulting loss",
    "zeruje odległość (drugi ośrodek władzy)":
        "zeroes the distance (second seat of government)",
    "znosi cały składnik odległości": "removes the whole distance term",
    "brak ośrodka władzy — przepada wszystko":
        "no seat of government — everything is lost",
    "odcięta od wszystkich celów — nie dojdzie do walki":
        "cut off from every target — it will never reach a fight",
    "wszystko": "everything",
}

VALUES_REVERSE: dict[str, str] = {}
for _pl, _en in VALUES.items():
    VALUES_REVERSE.setdefault(_en, _pl)

KEYS_REVERSE: dict[str, str] = {}
for _pl, _en in KEYS.items():
    KEYS_REVERSE.setdefault(_en, _pl)


def untranslate_args(args):
    """Przyjmuje argumenty po angielsku i sprowadza je do nazw kanonicznych."""
    if isinstance(args, dict):
        return {KEYS_REVERSE.get(k, k) if isinstance(k, str) else k:
                untranslate_args(v) for k, v in args.items()}
    if isinstance(args, list):
        return [untranslate_args(v) for v in args]
    if isinstance(args, str):
        return VALUES_REVERSE.get(args, args)
    return args


def key(k: str) -> str:
    """Tlumaczy pojedynczy klucz odpowiedzi."""
    if language() != "en":
        return k
    if k in KEYS:
        return KEYS[k]
    for pattern, repl in _DYNAMIC:
        if pattern.match(k):
            return pattern.sub(repl, k)
    return k


def value(v: str) -> str:
    return VALUES.get(v, v) if language() == "en" else v


def translate(obj):
    """Rekurencyjnie tlumaczy klucze i nasze wartosci w wyniku narzedzia.

    Nazwy z zestawu regul zostaja nietkniete, bo nie ma ich w slownikach.
    """
    if language() != "en":
        return obj
    if isinstance(obj, dict):
        return {key(k) if isinstance(k, str) else k: translate(v)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [translate(v) for v in obj]
    if isinstance(obj, str):
        return value(obj)
    return obj


# ═════════════════════════════════════════════════ narzedzia asystenta

# Nazwa polska jest kanoniczna; angielska to alias, ktory `dispatch` rozwiazuje.
TOOL_NAMES: dict[str, str] = {
    "pokaz_stan": "show_state",
    "ustaw_scenariusz": "set_scenario",
    "ustaw_moja_jednostke": "set_my_unit",
    "ustaw_sily_wroga": "set_enemy_forces",
    "policz": "compute",
    "ranking": "rank",
    "tabela_wytrzymalosci": "resilience_table",
    "dane_jednostki": "unit_data",
    "spis": "catalog",
    "wczytaj_zapis": "load_savegame",
    "moje_wojska": "my_forces",
    "wywiad_o_nacji": "nation_intel",
    "epoki": "eras",
    "plan_budowy": "build_plan",
    "gotowosc_wojenna": "war_readiness",
    "plan_kampanii": "campaign_plan",
    "plan_tury": "turn_plan",
    "plan_badan": "research_plan",
    "plan_produkcji": "production_plan",
    "ocena_zagrozenia": "threat_assessment",
    "mobilnosc": "mobility",
    "alerty": "alerts",
    "uklady_dyplomatyczne": "treaties",
    "dostawcy": "providers",
    "potencjal_wzrostu": "growth_potential",
    "obrona_miasta": "city_defense",
    "korupcja": "waste",
    "moje_technologie": "my_technologies",
    "szlaki_handlowe": "trade_routes",
    "plan_karawan": "caravan_plan",
    "co_da_rozwiazanie": "disbanding_yields",
    "audyt_miast": "city_audit",
    "przejezdnosc": "reachability",
    "porownaj_ustroje": "compare_governments",
    "linia_frontu": "front_line",
}

TOOL_NAMES_REVERSE = {v: k for k, v in TOOL_NAMES.items()}

TOOL_DESC: dict[str, str] = {
    "pokaz_stan":
        "Returns every current calculator setting: mode, ruleset, the player's "
        "unit, enemy forces, terrain, buildings, tech level. Call this at the "
        "start of a conversation and whenever you are unsure what is set — "
        "never guess the state.",
    "ustaw_scenariusz":
        "Sets the scenario in the interface: working mode, terrain, city "
        "parameters, buildings and tech level. Call it when the user describes "
        "a situation in the game. Pass only the fields that actually change; "
        "the rest stays as it was. Terrain and building names must come from "
        "the current ruleset — check with the 'catalog' tool if unsure.",
    "ustaw_moja_jednostke":
        "Sets the player's unit — in assault mode the one attacking, in "
        "defense mode the one holding the city. Call it when the user asks "
        "about a specific unit or when you want to compare variants in turn.",
    "ustaw_sily_wroga":
        "Sets the opponent's garrison or attacking force — the full list, "
        "replacing the previous one. Always call it when the user says what "
        "the enemy has. At most 3 unit types.",
    "policz":
        "Runs the full calculation for the current settings and returns the "
        "numbers: combat strengths with the multiplier breakdown, duel odds, "
        "the number of units needed, losses and cost. This is the source of "
        "every number you quote — NEVER estimate a result yourself.",
    "ranking":
        "Returns a unit ranking for the current scenario: in assault mode what "
        "strikes most cheaply, in defense mode what holds best. Call it when "
        "the user asks what is worth it or which unit to pick.",
    "tabela_wytrzymalosci":
        "For each defender, how large an attacking wave it stops. Useful when "
        "the user asks how long a garrison holds out.",
    "dane_jednostki":
        "Raw statistics of one unit type from the ruleset: attack, defense, "
        "hit points, firepower, cost, class, flags, veteran levels and bonuses.",
    "spis":
        "Lists what the current ruleset contains: units, terrains, buildings, "
        "governments. Call it before setting a name you are not sure exists.",
    "wczytaj_zapis":
        "Reads an actual Freeciv savegame and returns the state of the game: "
        "turn, nations, diplomacy, cities, forces. Respects fog of war by "
        "default; full intelligence requires deliberately setting the cheat "
        "flag and is labelled in every response.",
    "moje_wojska":
        "The player's own army from the loaded savegame: unit counts by type, "
        "veterancy, positions and which cities are garrisoned.",
    "wywiad_o_nacji":
        "What is known about a given nation: its cities, forces, diplomatic "
        "state. Under fog of war only what the player has actually seen.",
    "epoki":
        "The list of eras in the current ruleset with the tech-tree step that "
        "opens each one and what becomes available at that step: units, "
        "buildings and wonders.",
    "potencjal_wzrostu":
        "Why a city is not growing and how much labour it costs to fix. It "
        "separates three causes that look identical from outside: the size cap "
        "(a sewer system is needed), a food-upkeep deficit (too many units "
        "homed there), or simply barren land. For land it works out, tile by "
        "tile, what irrigation would add and what transforming the terrain "
        "would add, together with the worker-turns each takes — all read from "
        "terrain.ruleset.",
    "dostawcy":
        "Which model providers are configured, which model is selected and "
        "where the key comes from (environment variable or file). It does NOT "
        "reveal the keys and cannot set them — keys are entered only in the "
        "interface settings or with `fcsiege.py klucz`, so that they never pass "
        "through the conversation.",
    "uklady_dyplomatyczne":
        "What happens to every treaty and when. The distinction that trips "
        "people up most: an ARMISTICE counts down and turns into PEACE by "
        "itself, while a CEASE-FIRE counts down and expires into WAR. Reports "
        "turns to change, the units that will be disbanded when an armistice "
        "becomes peace, the other side's strength, shared enemies and embassy "
        "status. It does not report a probability of a treaty being accepted, "
        "because the savegame does not store the AI's attitude.",
    "alerty":
        "Scans the loaded savegame and returns everything that is going wrong, "
        "ordered by urgency, each with the number of turns until the damage "
        "lands and a concrete piece of advice: cities about to lose a "
        "population point to a food deficit, civil disorder, production being "
        "converted to gold, conquered cities left without a garrison, troops "
        "in the field generating unhappiness. Call it after loading a "
        "savegame and at the start of any conversation about the game.",
    "obrona_miasta":
        "What to defend a SPECIFIC city with. Unlike the generic 'rank' it "
        "takes the real terrain under that city, its tile improvements, its "
        "actual buildings, size and government, and the unit list from the "
        "technologies genuinely researched. It assumes the most dangerous "
        "attacker actually visible among the neighbours, and orders the "
        "options by cost per attacker stopped.",
    "mobilnosc":
        "Logistics and reach. The inverse of 'war_readiness': that tool says "
        "how many turns to a named city, this one says WHERE a unit can get at "
        "all. It uses real hex movement cost — hills and forest halve the "
        "reach, mountains cut it to a third, roads multiply it. Returns rally "
        "points (which of your cities can gather the most units, and how "
        "fast), enemy targets in reach, cut-off units, and the happiness cost "
        "of troops standing in the field. Call it for questions about "
        "regrouping, an overstretched front, \"will I make it\", \"where do "
        "I gather\", \"should I uncover this border\".",
    "ocena_zagrozenia":
        "Who is a real threat and who merely looks dangerous on the nation "
        "list. A raw unit count says nothing — an army on the far side of the "
        "map, with no land connection and no transport, will never take a "
        "city. The tool measures the ability to USE force: the real hex march "
        "to your nearest city, whether a shared landmass exists, whether they "
        "own ships with cargo capacity. Separately it rates how easy they are "
        "to attack — the terrain of their cities, their walls, their gold — "
        "because those are two different questions.",
    "plan_produkcji":
        "What to build in every city, against both the strategy AND the "
        "international situation. Strategy alone is not enough: with free land "
        "a settler beats any building, and with a cease-fire about to expire a "
        "diplomat beats a settler. The tool first assesses the situation — how "
        "many free city sites there are, who actually has anything to attack "
        "with, which treaties are expiring, how far the worker backlog runs, "
        "which cities stand without a garrison — and only then assigns roles. "
        "Strategy 'auto' infers it from the situation.",
    "plan_badan":
        "The research order for a CHOSEN STRATEGY, with a numeric rationale. "
        "Every technology is scored by what it unlocks: buildings weighted by "
        "their effects through the lens of the strategy, penalised for upkeep "
        "and rewarded for having none; units by strength per shield; "
        "governments by the penalty they lift from the whole realm. An effect "
        "that works in every city counts for more than a single-city one. The "
        "score is divided by turns-to-acquire so near and far goals compare. "
        "Strategies: gospodarka (economy), nauka (science), ekspansja "
        "(expansion), wojna (war).",
    "plan_tury":
        "What to do this turn: what to build in each city, how to set the tax "
        "sliders, which research to pick and what to spend gold on. The advice "
        "depends on the stage of the game, which the tool infers from the "
        "number of cities, the depth of the technology tree and whether a war "
        "is on — otherwise it would recommend wonders on turn one. Every "
        "threshold (the government's per-tile penalty, the rate cap, the "
        "empire-size threshold) is read from the rules. This is the "
        "first-choice tool for \"what should I do now\".",
    "plan_kampanii":
        "Concrete orders for this turn in a war, including a war on several "
        "fronts. It joins three things that are insufficient separately: what "
        "taking a target costs (the combat engine on its real terrain and "
        "walls), what the target is worth (buildings, harbour, roads, distance "
        "to your capital) and whether you can even get there (hex movement "
        "cost). It then allocates your units to targets — best value-per-unit "
        "first — and says how many to send, from where and in how many turns. "
        "Each group includes a garrison, because an empty prize is cheap for "
        "the enemy to buy back.",
    "gotowosc_wojenna":
        "Answers \"strike now or wait\" with numbers rather than opinion. For "
        "the given nations it collects the state of every one of their cities "
        "(walls, garrison, what it is building), computes how many of your "
        "units can reach each one within a given number of turns using the "
        "real map geometry, and prices the happiness cost of the garrisons "
        "marching out — martial law leaves with the troops. It also shows what "
        "delay changes: which enemy cities are raising walls and which are "
        "producing settlers.",
    "plan_budowy":
        "Splits the cities into a metropolis and colonies and says what to "
        "build where, era by era, separating buildings from wonders. The split "
        "follows from the kind of effect read out of the rules: a percentage "
        "bonus pays off in a large city, a flat effect is identical "
        "everywhere, a City-range wonder works only in its own city while a "
        "Player/World-range one works everywhere.",
    "korupcja":
        "Computes waste of shields and trade in every city of the player "
        "straight from the game's own formula: a government constant plus a "
        "term for the distance to the nearest seat of government, reduced by "
        "buildings. Shows what a courthouse recovers in each city and how many "
        "turns it takes to pay back, and which buildings and governments "
        "reduce waste at all in this ruleset.",
    "moje_technologie":
        "Reads the player's ACTUAL technology tree from the loaded savegame: "
        "what is researched, what is being researched now, bulbs per turn, "
        "turns remaining, which technologies run ahead of their era and — most "
        "usefully — which missing technologies are closest and what exactly "
        "they unlock. Use this INSTEAD of 'eras' when a savegame is loaded: "
        "research often runs ahead of its era, so the depth step misleads.",
    "szlaki_handlowe":
        "Works out optimal trade routes for caravans straight from the rules "
        "of the given ruleset. The route type decides its value: in many "
        "rulesets a route between two of your own cities gives 0%.",
    "co_da_rozwiazanie":
        "What disbanding a given unit type yields: shields recovered "
        "(rulesets usually return only part of the build cost), upkeep saved "
        "per turn, food freed, and where to disband so the shields go into "
        "something useful.",
    "audyt_miast":
        "City sizes, growth caps and how many units each city still feeds. In "
        "many rulesets free food upkeep grows with the city and the size cap "
        "is raised by an aqueduct and a sewer system.",
    "przejezdnosc":
        "Which tiles a given unit class can actually enter. Heavy classes are "
        "blocked by swamp, jungle and mountains without a road, and merchant "
        "units may need roads or rivers outright. Shows connected regions, how "
        "many of your units sit in each, and which targets they can reach.",
    "porownaj_ustroje":
        "Compares governments in the current ruleset on the effects that "
        "actually matter: tax rates, waste, unit upkeep, martial law, empire "
        "size penalties.",
    "linia_frontu":
        "Where the front runs: the player's cities and units nearest to a "
        "given nation, with distances, so an offensive or a defense can be "
        "planned.",
}


def tool_name(polish: str) -> str:
    """Nazwa narzedzia w biezacym jezyku."""
    return TOOL_NAMES.get(polish, polish) if language() == "en" else polish


def canonical_tool(name: str) -> str:
    """Sprowadza angielski alias do kanonicznej nazwy polskiej."""
    return TOOL_NAMES_REVERSE.get(name, name)


def tool_desc(polish: str, fallback: str) -> str:
    return TOOL_DESC.get(polish, fallback) if language() == "en" else fallback
