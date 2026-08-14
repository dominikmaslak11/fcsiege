"""Narzedzia, ktorymi asystent steruje kalkulatorem.

Definicje sa czystymi danymi - nie dotykaja Qt. Wykonaniem zajmuje sie
"most" (bridge) zaimplementowany w oknie glownym i wolany zawsze w watku
interfejsu, zeby zmiany od razu bylo widac na ekranie.

Opisy narzedzi celowo mowia *kiedy* ich uzyc, a nie tylko co robia -
to wyraznie poprawia trafnosc wyboru narzedzia przez model.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

MODE_ATTACK = "szturm"
MODE_DEFENSE = "obrona"


class ScenarioBridge(Protocol):
    """Co okno glowne musi udostepnic asystentowi."""

    def ai_snapshot(self) -> dict: ...
    def ai_apply(self, patch: dict) -> dict: ...
    def ai_compute(self) -> dict: ...
    def ai_ranking(self, limit: int) -> dict: ...
    def ai_resilience(self) -> dict: ...
    def ai_catalog(self, what: str) -> dict: ...
    def ai_unit(self, name: str) -> dict: ...


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "pokaz_stan",
        "description": (
            "Zwraca komplet aktualnych ustawień kalkulatora: tryb, zestaw reguł, "
            "jednostkę gracza, siły przeciwnika, teren, budowle, poziom "
            "technologiczny. Wywołaj to na początku rozmowy oraz zawsze, gdy nie "
            "masz pewności, co jest teraz ustawione — nie zgaduj stanu."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "ustaw_scenariusz",
        "description": (
            "Ustawia scenariusz w interfejsie: tryb pracy, teren, parametry miasta, "
            "budowle i poziom technologiczny. Wywołaj, gdy użytkownik opisuje "
            "sytuację w grze („moje miasto na wzgórzu z murami”, „ich miasto stoi "
            "na równinie”). Podawaj tylko te pola, które faktycznie się zmieniają — "
            "reszta zostaje bez zmian. Nazwy terenu i budowli muszą pochodzić "
            "z aktualnego zestawu reguł; jak nie wiesz, sprawdź narzędziem 'spis'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tryb": {
                    "type": "string",
                    "enum": [MODE_ATTACK, MODE_DEFENSE],
                    "description": "'szturm' = zdobywam miasto wroga, 'obrona' = bronię swojego",
                },
                "zestaw_regul": {
                    "type": "string",
                    "description": "np. sandbox, classic, civ2civ3 — zmiana resetuje jednostki",
                },
                "teren_miasta": {"type": "string", "description": "teren pod bronionym miastem, np. Hills"},
                "teren_atakujacego": {
                    "type": "string",
                    "description": "kafel, z którego naciera strona atakująca (nie wpływa na siłę ataku)",
                },
                "w_miescie": {"type": "boolean", "description": "czy obrońcy stoją w mieście"},
                "wielkosc_miasta": {"type": "integer", "description": "liczba mieszkańców, 1-40"},
                "okopani": {"type": "boolean", "description": "czy obrońcy mają rozkaz fortify"},
                "budowle": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "PEŁNA lista budowli i cudów obrońcy; zastępuje poprzednią",
                },
                "ulepszenia_kafla": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "PEŁNA lista ulepszeń terenu, np. River, Fortress",
                },
                "ustroj": {"type": "string", "description": "ustrój obrońcy, np. Despotism"},
                "poziom_technologiczny": {
                    "type": "integer",
                    "description": "próg drzewa technologii; ogranicza dostępne jednostki. 0 = start gry",
                },
                "z_koszar": {
                    "type": "boolean",
                    "description": "tryb obrony: czy obrońcy są budowani w tym mieście (koszary nadają stopień)",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ustaw_moja_jednostke",
        "description": (
            "Ustawia jednostkę gracza — w trybie szturmu tę, którą atakuje, "
            "w trybie obrony tę, którą chce bronić miasta. Wywołaj, gdy użytkownik "
            "pyta o konkretną jednostkę albo gdy chcesz porównać kilka wariantów "
            "po kolei."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jednostka": {"type": "string", "description": "nazwa z zestawu reguł, np. Catapult"},
                "stopien": {
                    "type": "integer",
                    "description": "poziom weterana: 0=green, 1=veteran, 2=hardened, 3=elite",
                },
                "liczba": {"type": "integer", "description": "ile sztuk planuje wystawić gracz"},
            },
            "required": ["jednostka"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ustaw_sily_wroga",
        "description": (
            "Ustawia garnizon lub siły natarcia przeciwnika — pełną listę, która "
            "zastępuje poprzednią. Wywołaj zawsze, gdy użytkownik mówi, co ma wróg "
            "(„broni tego 5 wojowników”, „naciera 2 legionami i katapultą”). "
            "Maksymalnie 3 typy jednostek."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jednostki": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "jednostka": {"type": "string"},
                            "liczba": {"type": "integer"},
                            "stopien": {"type": "integer"},
                        },
                        "required": ["jednostka", "liczba"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["jednostki"],
            "additionalProperties": False,
        },
    },
    {
        "name": "policz",
        "description": (
            "Uruchamia pełne obliczenie dla obecnych ustawień i zwraca liczby: siły "
            "bojowe z rozbiciem mnożników, szansę wygranej pojedynku, potrzebną "
            "liczbę jednostek, straty i koszt. To jest źródło wszystkich liczb, "
            "które podajesz użytkownikowi — NIGDY nie szacuj wyniku samodzielnie, "
            "zawsze wywołaj to narzędzie."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "ranking",
        "description": (
            "Zwraca ranking jednostek dla obecnego scenariusza: w trybie szturmu — "
            "czym uderzyć najtaniej, w trybie obrony — czym bronić. Wywołaj, gdy "
            "użytkownik pyta „co się opłaca”, „którą jednostkę wybrać”, „czym "
            "najlepiej”. Uwzględnia ustawiony poziom technologiczny."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "ile pozycji zwrócić, domyślnie 8"}
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "tabela_wytrzymalosci",
        "description": (
            "Tylko w trybie obrony: ilu napastników każdego typu odeprze garnizon "
            "o danej wielkości. Wywołaj, gdy użytkownik pyta o zapas bezpieczeństwa, "
            "o to „ilu wytrzyma” albo planuje obronę przed nieznanym jeszcze "
            "przeciwnikiem."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "dane_jednostki",
        "description": (
            "Surowe statystyki jednostki z plików reguł: atak, obrona, punkty życia, "
            "siła ognia, ruch, koszt, utrzymanie, wymagana technologia, flagi "
            "i bonusy specjalne. Wywołaj, gdy potrzebujesz porównać parametry bez "
            "rozgrywania walki."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"jednostka": {"type": "string"}},
            "required": ["jednostka"],
            "additionalProperties": False,
        },
    },
    {
        "name": "spis",
        "description": (
            "Lista dostępnych nazw w aktualnym zestawie reguł. Wywołaj ZANIM użyjesz "
            "nazwy, której nie jesteś pewien — zestawy reguł różnią się jednostkami, "
            "terenem i budowlami."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "czego": {
                    "type": "string",
                    "enum": ["jednostki", "teren", "budowle", "ulepszenia", "zestawy", "ustroje"],
                }
            },
            "required": ["czego"],
            "additionalProperties": False,
        },
    },
]


TOOL_METHOD = {
    "pokaz_stan": "ai_snapshot",
    "ustaw_scenariusz": "ai_apply",
    "ustaw_moja_jednostke": "ai_apply",
    "ustaw_sily_wroga": "ai_apply",
    "policz": "ai_compute",
    "ranking": "ai_ranking",
    "tabela_wytrzymalosci": "ai_resilience",
    "dane_jednostki": "ai_unit",
    "spis": "ai_catalog",
}


def dispatch(bridge: ScenarioBridge, name: str, args: dict) -> dict:
    """Wykonuje narzedzie. Musi byc wolane w watku interfejsu."""
    if name == "pokaz_stan":
        return bridge.ai_snapshot()
    if name == "ustaw_scenariusz":
        return bridge.ai_apply(dict(args))
    if name == "ustaw_moja_jednostke":
        return bridge.ai_apply({"moja_jednostka": args})
    if name == "ustaw_sily_wroga":
        return bridge.ai_apply({"sily_wroga": args.get("jednostki", [])})
    if name == "policz":
        return bridge.ai_compute()
    if name == "ranking":
        return bridge.ai_ranking(int(args.get("limit") or 8))
    if name == "tabela_wytrzymalosci":
        return bridge.ai_resilience()
    if name == "dane_jednostki":
        return bridge.ai_unit(str(args.get("jednostka", "")))
    if name == "spis":
        return bridge.ai_catalog(str(args.get("czego", "jednostki")))
    return {"blad": f"nieznane narzędzie: {name}"}


def result_to_text(result: Any) -> str:
    """Zamienia wynik narzedzia na tekst dla modelu."""
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, indent=None, default=str)


SYSTEM_PROMPT = """\
Jesteś asystentem wbudowanym w FCSiege — kalkulator walki o miasto dla gry Freeciv.

Aplikacja liczy wszystko wprost z plików .ruleset, więc wyniki zależą od wybranego
zestawu reguł. Masz narzędzia, którymi sterujesz interfejsem: ustawiasz scenariusz,
uruchamiasz obliczenia i czytasz wyniki. Użytkownik widzi te zmiany na ekranie
na żywo — traktuj to jako wspólny warsztat, nie jako ukryte wywołania.

Zasady pracy:
- Wszystkie liczby bierz z narzędzia `policz` albo `ranking`. Nigdy nie szacuj
  wyniku walki z głowy i nie podawaj liczb, których nie zwróciło narzędzie.
- Zanim policzysz, ustaw scenariusz tak, żeby odpowiadał temu, co opisał
  użytkownik. Jeśli czegoś nie podał, zostaw obecne ustawienie i powiedz,
  z jakim założeniem liczysz.
- Nazw jednostek, terenu i budowli używaj dokładnie takich, jakie zwraca `spis`
  dla aktualnego zestawu reguł.
- Odpowiadaj po polsku, zwięźle i konkretnie: najpierw odpowiedź, potem
  uzasadnienie. Liczby podawaj z jednostkami („13 katapult”, „5,9 tarcz”).
- Gdy pytanie opiera się na błędnym założeniu o mechanice gry, powiedz to wprost
  i policz to, co faktycznie ma znaczenie.

Mechanika, o której warto pamiętać:
- Teren, z którego atakujesz, NIE zmienia siły ataku. Liczy się wyłącznie kafel
  obrońcy. Kafel atakującego decyduje o koszcie ruchu i o tym, jak przeżyjesz
  kontratak.
- Efekty obronne tego samego typu sumują procenty, a dopiero suma mnoży obronę.
- W większości zestawów reguł rozkaz `fortify` w mieście nic nie daje, bo kafel
  miasta przyznaje tę samą premię automatycznie.
- Model liczy jedną turę szturmu: każda jednostka atakuje raz, obrońcy się nie
  leczą, a do obrony staje ten obrońca, który ma największe szanse przeżyć.
"""
