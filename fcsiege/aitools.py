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

from . import i18n

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
    def ai_savegame(self, args: dict) -> dict: ...
    def ai_army(self, args: dict) -> dict: ...
    def ai_nation(self, args: dict) -> dict: ...
    def ai_front(self, args: dict) -> dict: ...
    def ai_governments(self, args: dict) -> dict: ...
    def ai_reach(self, args: dict) -> dict: ...
    def ai_cities(self, args: dict) -> dict: ...
    def ai_disband(self, args: dict) -> dict: ...
    def ai_trade(self, args: dict) -> dict: ...
    def ai_eras(self, args: dict) -> dict: ...


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
    {
        "name": "wczytaj_zapis",
        "description": (
            "Wczytuje zapis gry Freeciva (domyślnie najnowszy z ~/.freeciv/saves) "
            "i zwraca sytuację: tura, twoja nacja, złoto, liczba miast i wojsk, "
            "stany dyplomatyczne. Ustawia też zestaw reguł zgodny z zapisem. "
            "Wywołaj to na początku każdej rozmowy o bieżącej partii — dzięki temu "
            "nie musisz pytać użytkownika o liczby, które są w zapisie.\n\n"
            "MGŁA WOJNY: domyślnie widzisz wyłącznie to, co wie gracz (własne "
            "miasta i wojska, odkryte miasta obcych, dyplomacja). Parametr "
            "pelny_wglad=True ujawnia wszystko, co jest w zapisie, łącznie z "
            "wojskami i miastami, których gracz nie odkrył — to świadome "
            "oszustwo wobec gry i wolno go użyć TYLKO gdy użytkownik wprost o to "
            "poprosi. Zawsze mów w odpowiedzi, w którym trybie liczysz."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sciezka": {"type": "string",
                            "description": "ścieżka do pliku zapisu; pusta = najnowszy"},
                "pelny_wglad": {"type": "boolean",
                                "description": "True = pokaż też to, czego gracz nie widzi (chity)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "moje_wojska",
        "description": (
            "Rozpiska twojej armii z wczytanego zapisu: ile jednostek każdego typu, "
            "na jakich stopniach weterana, ile rannych, oraz co budują twoje miasta. "
            "Wywołaj, zanim doradzisz, co rekrutować — żeby wiedzieć, co gracz już ma."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "wywiad_o_nacji",
        "description": (
            "Co wiadomo o wskazanej nacji: stan dyplomatyczny i odkryte miasta wraz "
            "z rozmiarem, murami i tym, czy są obsadzone. Wywołaj przed planowaniem "
            "ofensywy albo oceną zagrożenia.\n\n"
            "Przy pelny_wglad=True dochodzą ich wszystkie miasta, całe wojsko i skład "
            "garnizonów — to świadome oszustwo, użyj tylko na wyraźną prośbę."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nacja": {"type": "string", "description": "np. Hittite albo Labarnas"},
                "pelny_wglad": {"type": "boolean"},
            },
            "required": ["nacja"],
            "additionalProperties": False,
        },
    },
    {
        "name": "epoki",
        "description": (
            "Lista epok w aktualnym zestawie reguł wraz z progiem drzewa "
            "technologii, który je otwiera, oraz tym, co dochodzi na danym "
            "progu: jednostki, budynki i cuda świata. Wywołaj, gdy użytkownik "
            "pyta, w jakiej jest epoce, co mu się odblokuje albo do czego warto "
            "dobić technologicznie."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prog": {"type": "integer",
                         "description": "sprawdź konkretny próg; puste = obecny"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "potencjal_wzrostu",
        "description": (
            "Dlaczego miasto nie rośnie i ile pracy kosztuje to naprawić. "
            "Rozdziela trzy przyczyny, które z zewnątrz wyglądają tak samo: "
            "limit wielkości (potrzebna kanalizacja), deficyt utrzymania na "
            "żywności (za dużo jednostek macierzystych) albo jałowa ziemia. "
            "Przy ziemi wylicza dla każdego sąsiedniego kafla, ile da "
            "irygacja i ile przemiana terenu, wraz z liczbą tur pracy — "
            "wszystko z terrain.ruleset."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "miasto": {"type": "string", "description": "puste = wszystkie"},
                "limit": {"type": "integer", "description": "ile kafli pokazać"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "dostawcy",
        "description": (
            "Którzy dostawcy modeli są skonfigurowani, jaki model jest wybrany "
            "i skąd pochodzi klucz (zmienna środowiskowa czy plik). NIE ujawnia "
            "samych kluczy i nie pozwala ich ustawić — klucze wpisuje się "
            "wyłącznie w ustawieniach interfejsu albo poleceniem "
            "`fcsiege.py klucz`, żeby nigdy nie przechodziły przez rozmowę."
        ),
        "input_schema": {"type": "object", "properties": {},
                         "additionalProperties": False},
    },
    {
        "name": "uklady_dyplomatyczne",
        "description": (
            "Co się stanie z każdym układem i kiedy. Kluczowe rozróżnienie, "
            "które myli się najczęściej: ROZEJM (Armistice) odlicza tury i sam "
            "zamienia się w POKÓJ, a ZAWIESZENIE BRONI (Cease-fire) wygasa do "
            "WOJNY. Podaje tury do zmiany, jednostki, które zostaną rozwiązane "
            "przy przejściu rozejmu w pokój, siłę drugiej strony, wspólnych "
            "wrogów i status ambasady. Nie podaje prawdopodobieństwa przyjęcia "
            "układu, bo zapis nie przechowuje nastawienia AI."
        ),
        "input_schema": {"type": "object", "properties": {},
                         "additionalProperties": False},
    },
    {
        "name": "alerty",
        "description": (
            "Skanuje wczytany zapis i zwraca listę rzeczy, które się psują, "
            "posortowaną wg pilności, każdą z liczbą tur do szkody i "
            "konkretną radą: miasta, które zaraz stracą rozmiar przez deficyt "
            "żywności, zamieszki, produkcję zamienianą na złoto, zdobyte "
            "miasta bez garnizonu, wojsko w polu robiące niezadowolonych. "
            "Wywołuj po każdym wczytaniu zapisu i na początku rozmowy o partii."
        ),
        "input_schema": {"type": "object", "properties": {},
                         "additionalProperties": False},
    },
    {
        "name": "obrona_miasta",
        "description": (
            "Czym bronić KONKRETNEGO miasta z zapisu. W odróżnieniu od "
            "ogólnego 'ranking' bierze prawdziwy teren spod tego miasta, "
            "ulepszenia kafla, faktyczne budynki, rozmiar i ustrój, a listę "
            "jednostek z realnie zbadanych technologii. Za napastnika "
            "przyjmuje najgroźniejszą jednostkę, jaką widać u sąsiadów. "
            "Szereguje wg kosztu za jednego zatrzymanego napastnika."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "miasto": {"type": "string",
                           "description": "puste = najbardziej wysunięte miasto"},
                "napastnik": {"type": "string",
                              "description": "wymuś konkretny typ napastnika"},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "mobilnosc",
        "description": (
            "Logistyka i zasięg. Odwrotna perspektywa do 'gotowosc_wojenna': "
            "tamto mówi, ile tur do wskazanego miasta, to mówi, DOKĄD każda "
            "jednostka w ogóle zdąży. Liczy realny koszt ruchu po heksie — "
            "wzgórza i las skracają zasięg dwukrotnie, góry trzykrotnie, drogi "
            "wydłużają wielokrotnie. Zwraca punkty zborne (które własne miasto "
            "zbierze najwięcej jednostek i jak szybko), cele wroga w zasięgu, "
            "jednostki odcięte oraz koszt szczęścia wojsk stojących w polu. "
            "Wywołaj przy pytaniach o przegrupowanie, rozciągnięcie frontu, "
            "„czy zdążę”, „gdzie się zebrać”, „czy odsłonić granicę”."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tury": {"type": "integer",
                         "description": "ile tur marszu liczyć, 1-4 (domyślnie 2)"},
                "jednostka": {"type": "string",
                              "description": "policz tylko ten typ, np. Knights"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ocena_zagrozenia",
        "description": (
            "Kto realnie zagraża, a kto tylko wygląda groźnie na liście nacji. "
            "Sama liczba jednostek nie mówi nic — wojsko po drugiej stronie "
            "mapy, bez połączenia lądowego i bez transportu, nie zajmie "
            "żadnego miasta. Narzędzie liczy zdolność UŻYCIA siły: realny marsz "
            "po heksie do najbliższego Twojego miasta, istnienie wspólnego "
            "lądu, posiadanie statków z ładownością. Osobno ocenia łatwość "
            "uderzenia w drugą stronę — teren ich miast, mury, ich złoto — bo "
            "to są dwie różne rzeczy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tury": {"type": "integer",
                         "description": "horyzont marszu, domyślnie 8"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "plan_produkcji",
        "description": (
            "Co budować w każdym mieście, względem strategii I sytuacji "
            "międzynarodowej. Sama strategia nie wystarcza: przy wolnej ziemi "
            "osadnik bije każdy budynek, a przy wygasającym zawieszeniu broni "
            "dyplomata bije osadnika. Narzędzie ocenia najpierw sytuację — ile "
            "jest wolnych miejsc pod miasta, kto ma czym uderzyć, które układy "
            "wygasają, ilu robotników brakuje do zaległości, które miasta stoją "
            "bez garnizonu — a dopiero potem przydziela role. Strategia "
            "'auto' rozpoznaje ją z sytuacji."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "strategia": {"type": "string",
                              "enum": ["auto", "gospodarka", "nauka",
                                       "ekspansja", "wojna"]},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "plan_badan",
        "description": (
            "Kolejność badań pod OBRANĄ STRATEGIĘ, z uzasadnieniem liczbowym. "
            "Każda technologia dostaje ocenę za to, co odblokowuje: budowle "
            "ważone ich efektami przez pryzmat strategii, karane za utrzymanie "
            "i premiowane za jego brak; jednostki po sile na tarczę; ustroje "
            "za zdjęcie kary z całego państwa. Efekt działający we wszystkich "
            "miastach liczy się wyżej niż jednomiastowy. Wynik dzielony przez "
            "tury do zdobycia, żeby porównać cele bliższe z dalszymi. "
            "Strategie: gospodarka, nauka, ekspansja, wojna."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "strategia": {"type": "string",
                              "enum": ["gospodarka", "nauka", "ekspansja", "wojna"],
                              "description": "domyślnie gospodarka"},
                "limit": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "plan_tury",
        "description": (
            "Co robić w tej turze: co budować w każdym mieście, jak ustawić "
            "suwaki podatków, jakie badania obrać i w co włożyć złoto. Rada "
            "zależy od fazy gry, a fazę narzędzie rozpoznaje z liczby miast, "
            "głębokości drzewa technologii i tego, czy trwa wojna — inaczej "
            "doradzałoby cudy świata w pierwszej turze. Wszystkie progi (kara "
            "ustroju za kafel, maksymalny suwak, próg kary za wielkość "
            "imperium) czyta z reguł. To jest narzędzie pierwszego wyboru przy "
            "pytaniu „co mam teraz robić”."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nastawienie": {
                    "type": "string",
                    "enum": ["auto", "pokojowe", "wojenne"],
                    "description": "pokojowe = robotnicy, teren i cuda zamiast "
                                   "wojska; auto = rozpoznaj z sytuacji",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "plan_kampanii",
        "description": (
            "Konkretne rozkazy na tę turę przy wojnie, także na kilku frontach. "
            "Łączy trzy rzeczy, które osobno nie wystarczają: ile kosztuje "
            "zdobycie celu (silnik walki na jego prawdziwym terenie i murach), "
            "ile ten cel jest wart (budynki, port, drogi, dystans do stolicy) "
            "i czy w ogóle zdążysz (koszt ruchu po heksie). Potem przydziela "
            "Twoje jednostki do celów — najpierw tam, gdzie stosunek wartości "
            "do kosztu jest najlepszy — i mówi, ile wysłać, skąd i w ile tur. "
            "Do każdej grupy dolicza garnizon, bo pusta zdobycz jest tania do "
            "odkupienia przez wroga."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tury": {"type": "integer",
                         "description": "ile tur marszu uznać za zasięg (domyślnie 2)"},
                "rezerwa": {"type": "integer",
                            "description": "ile jednostek zostawić w zdobytym "
                                           "mieście (domyślnie 1)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "gotowosc_wojenna",
        "description": (
            "Odpowiada na pytanie „uderzać teraz czy czekać” liczbami, nie "
            "opinią. Dla wskazanych nacji zbiera stan każdego ich miasta "
            "(mury, garnizon, co buduje), liczy, ile Twoich jednostek dojdzie "
            "tam w zadanej liczbie tur po realnej geometrii mapy, oraz ile "
            "kosztuje szczęście wymarsz garnizonów — bo stan wojenny znika "
            "razem z wojskiem. Pokazuje też, co zmieni zwłoka: które miasta "
            "wroga stawiają mury, a które produkują osadników."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nacje": {"type": "array", "items": {"type": "string"},
                          "description": "nacje, z którymi planujesz wojnę"},
                "tury": {"type": "integer",
                         "description": "ile tur marszu liczyć (domyślnie 2)"},
            },
            "required": ["nacje"],
            "additionalProperties": False,
        },
    },
    {
        "name": "plan_budowy",
        "description": (
            "Dzieli miasta na metropolię i kolonie i mówi, co gdzie budować, "
            "epoka po epoce, z podziałem na budynki i cudy świata. Podział "
            "wynika z rodzaju efektu odczytanego z reguł: bonus procentowy "
            "opłaca się w dużym mieście, efekt stały wszędzie tak samo, cud "
            "o zasięgu City działa tylko w swoim mieście, a o zasięgu "
            "Player/World wszędzie. Wywołaj przy pytaniach o strategię "
            "rozbudowy, kolejność budynków, gdzie stawiać cudy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "metropolia": {"type": "string",
                               "description": "miasto-stolica produkcji; "
                                              "puste = wybierz automatycznie"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "korupcja",
        "description": (
            "Liczy marnotrawstwo (korupcję) tarcz i handlu w każdym Twoim "
            "mieście wprost ze wzoru z gry: stała od ustroju plus składnik od "
            "odległości do najbliższego ośrodka władzy, pomniejszone przez "
            "budynki. Pokazuje, ile odzyska ratusz w każdym mieście i po ilu "
            "turach się zwróci, oraz jakie budynki i ustroje w tym zestawie "
            "reguł w ogóle zbijają korupcję. Wywołaj, gdy pada pytanie o "
            "korupcję, o opłacalność odległych kolonii albo o to, gdzie "
            "produkcja przepada."
        ),
        "input_schema": {"type": "object", "properties": {},
                         "additionalProperties": False},
    },
    {
        "name": "moje_technologie",
        "description": (
            "Czyta FAKTYCZNE drzewo technologii gracza z wczytanego zapisu: co "
            "ma zbadane, co bada teraz, ile bulbs na turę, ile tur do końca, "
            "które technologie wyprzedzają jego epokę oraz — najważniejsze — "
            "które brakujące technologie są najbliżej i co konkretnie odblokują "
            "(jednostki, budynki, cuda). Używaj tego ZAMIAST narzędzia 'epoki', "
            "gdy zapis jest wczytany: badania często wyprzedzają epokę, więc "
            "próg głębokości bywa mylący. Ustawia też filtr dostępnych jednostek "
            "w kalkulatorze na to, co gracz naprawdę zna."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer",
                          "description": "ile propozycji badań (domyślnie 12)"},
                "zastosuj": {
                    "type": "boolean",
                    "description": ("true = filtruj kalkulator wg realnych "
                                    "technologii, false = wróć do suwaka"),
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "plan_karawan",
        "description": (
            "Odpowiada na trzy pytania naraz: w KTÓRYCH miastach opłaca się "
            "zbudować karawanę, DO KTÓRYCH obcych miast ją wysłać i CZYM tam "
            "dotrzeć — drogą czy morzem. Sprawdza dostępność dróg i tras "
            "morskich, bo klasa Merchant w wielu zestawach nie jest natywna "
            "dla żadnego terenu i porusza się wyłącznie po drogach, kolejach "
            "i rzekach — bez ciągłej drogi karawana nie ruszy się z miejsca "
            "i musi płynąć promem. Wartość trasy liczy dokładnymi wzorami "
            "z traderoutes.c (styl CLASSIC/SIMPLE brany z ustawień partii), "
            "a nie przybliżeniem po rozmiarze miasta. Podaje też czas "
            "produkcji karawany w każdym mieście i karę za zmianę produkcji. "
            "Wywołaj, gdy użytkownik pyta gdzie budować karawany, dokąd "
            "prowadzić szlaki, czy budować drogę do obcego miasta albo czy "
            "wozić karawany morzem."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer",
                          "description": "ile tras zaproponować, domyślnie 12"},
                "max_tur": {"type": "integer",
                            "description": "odrzuć trasy dłuższe niż tyle tur "
                                           "(produkcja + marsz), domyślnie 60"},
                "pelny_wglad": {"type": "boolean",
                                "description": "użyj wiedzy spoza mgły wojny"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "szlaki_handlowe",
        "description": (
            "Wyznacza optymalne szlaki handlowe dla karawan, wprost z reguł "
            "danego zestawu. Typ trasy decyduje o jej wartości: w wielu "
            "zestawach trasa między własnymi miastami daje 0% (tylko premię "
            "jednorazową), zagraniczna 100%, a międzykontynentalna 200%; trasa "
            "z wrogiem, z którym jesteś w stanie wojny, jest warta 0 i zostaje "
            "anulowana. Uwzględnia minimalny dystans, limit tras na miasto "
            "i zajęte już sloty. Wywołaj, gdy użytkownik pyta o karawany, "
            "handel, trasy handlowe albo rozważa handel zamiast podboju."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "ile tras zaproponować, domyślnie 15"},
                "tylko_miedzykontynentalne": {"type": "boolean",
                    "description": "pokaż wyłącznie trasy przez morze (zwykle najcenniejsze)"},
                "pelny_wglad": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "co_da_rozwiazanie",
        "description": (
            "Liczy, co da rozwiązanie zbędnych jednostek: ile tarcz wróci "
            "(procent zwrotu czytany z reguł), ile zaoszczędzisz na utrzymaniu "
            "co turę, ile uwolni się żywności, w których miastach je rozwiązać, "
            "żeby tarcze trafiły tam, gdzie brakuje budynku, i co za to kupisz. "
            "Sam typuje kandydatów: jednostki odcięte od wszystkich celów (nie "
            "dojdą do walki) oraz bezczynne jednostki cywilne. Wywołaj, gdy "
            "użytkownik pyta o rozwiązywanie jednostek, o odzysk tarcz albo "
            "narzeka na koszt utrzymania armii."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jednostki": {"type": "array", "items": {"type": "string"},
                              "description": "ogranicz do tych typów; puste = typuj sam"},
                "pelny_wglad": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "audyt_miast",
        "description": (
            "Dla każdego twojego miasta: rozmiar, ile jednostek utrzymuje, ile "
            "z nich zjada żywność, ile utrzyma jej za darmo i jaki ma limit "
            "wzrostu. W wielu zestawach reguł darmowe utrzymanie żywnościowe "
            "ROŚNIE razem z miastem, a limit wielkości podnoszą akwedukt "
            "i kanalizacja — narzędzie czyta te progi wprost z reguł. Wywołaj, "
            "gdy użytkownik pyta, czemu miasto nie rośnie, ile jeszcze jednostek "
            "wyżywi albo czy warto rozbudowywać miasta."
        ),
        "input_schema": {"type": "object", "properties": {},
                         "additionalProperties": False},
    },
    {
        "name": "przejezdnosc",
        "description": (
            "Sprawdza, czy twoje jednostki w ogóle DOJDĄ do celów. W wielu "
            "zestawach reguł ciężkie jednostki (klasa Big Land: katapulty, "
            "działa) nie wchodzą na bagna, dżunglę i góry bez drogi — droga, "
            "kolej i rzeka liczą się jako przejezdne, a każde miasto ma drogę "
            "z automatu. Zwraca, po jakim terenie dana jednostka chodzi, ile "
            "twoich sztuk stoi w którym obszarze przejezdnym i do których miast "
            "wroga faktycznie dotrą.\n\n"
            "Wywołaj ZAWSZE przed planowaniem ofensywy ciężkimi jednostkami — "
            "sama siła ataku nic nie znaczy, jeśli jednostka nie dojdzie."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "jednostki": {"type": "array", "items": {"type": "string"},
                              "description": "np. ['Catapult','Knights']; puste = wszystkie twoje typy"},
                "pelny_wglad": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "porownaj_ustroje",
        "description": (
            "Porównuje ustroje z aktualnego zestawu reguł: maksymalne suwaki, "
            "utrzymanie wojsk, kary za wielkość imperium, stan wojenny, "
            "niezadowolenie od wojsk w polu, marnotrawstwo i premie. Jeśli jest "
            "wczytany zapis gry, liczy też realne skutki dla twojej partii "
            "(koszt utrzymania twoich jednostek, ile masz poziomów kary za "
            "wielkość, których technologii ci brakuje). Wywołaj, gdy użytkownik "
            "pyta o zmianę ustroju albo o to, co się bardziej opłaca — nie "
            "odpowiadaj z pamięci, zestawy reguł bardzo się tu różnią."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "ustroje": {
                    "type": "array", "items": {"type": "string"},
                    "description": "które porównać, np. ['Monarchy','Republic']; "
                                   "puste = wszystkie",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "linia_frontu",
        "description": (
            "Dla każdego miasta wskazanej nacji podaje twoje najbliższe miasta wraz "
            "z dystansem oraz twoje jednostki w promieniu. Wywołaj, gdy planujesz, "
            "skąd poprowadzić natarcie, gdzie ustawić front albo czy zdążysz "
            "przerzucić wojsko."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nacja": {"type": "string"},
                "promien": {"type": "integer", "description": "w kaflach, domyślnie 12"},
                "pelny_wglad": {"type": "boolean"},
            },
            "required": ["nacja"],
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
    "wczytaj_zapis": "ai_savegame",
    "moje_wojska": "ai_army",
    "wywiad_o_nacji": "ai_nation",
    "linia_frontu": "ai_front",
    "porownaj_ustroje": "ai_governments",
    "przejezdnosc": "ai_reach",
    "audyt_miast": "ai_cities",
    "co_da_rozwiazanie": "ai_disband",
    "szlaki_handlowe": "ai_trade",
    "plan_karawan": "ai_caravans",
    "epoki": "ai_eras",
    "moje_technologie": "ai_techs",
    "korupcja": "ai_corruption",
    "plan_budowy": "ai_build_plan",
    "gotowosc_wojenna": "ai_war_readiness",
    "plan_kampanii": "ai_campaign",
    "plan_tury": "ai_turn_plan",
    "plan_badan": "ai_research_plan",
    "plan_produkcji": "ai_production_plan",
    "ocena_zagrozenia": "ai_threats",
    "mobilnosc": "ai_mobility",
    "obrona_miasta": "ai_city_defense",
    "alerty": "ai_alerts",
    "uklady_dyplomatyczne": "ai_diplomacy",
    "dostawcy": "ai_providers",
    "potencjal_wzrostu": "ai_growth",
}


def dispatch(bridge: ScenarioBridge, name: str, args: dict) -> dict:
    """Wykonuje narzedzie w biezacym jezyku.

    Nazwy i argumenty moga przyjsc po angielsku - sprowadzamy je do postaci
    kanonicznej (polskiej), liczymy, a wynik tlumaczymy z powrotem. Przy jezyku
    polskim wszystkie trzy kroki sa tozsamosciowe, wiec nic nie kosztuja.
    """
    return i18n.translate(_dispatch(bridge, i18n.canonical_tool(name),
                                    i18n.untranslate_args(args or {})))


def localized_specs(lang: str | None = None) -> list[dict[str, Any]]:
    """Definicje narzedzi w danym jezyku - nazwy, opisy i nazwy argumentow."""
    prev = i18n.language()
    if lang:
        i18n.set_language(i18n.normalize(lang))
    try:
        if i18n.language() != "en":
            return TOOL_SPECS
        out = []
        for spec in TOOL_SPECS:
            out.append({
                **spec,
                "name": i18n.tool_name(spec["name"]),
                "description": i18n.tool_desc(spec["name"], spec["description"]),
                "input_schema": _localize_schema(spec["input_schema"]),
            })
        return out
    finally:
        i18n.set_language(prev)


def _localize_schema(schema: dict) -> dict:
    out = dict(schema)
    props = schema.get("properties")
    if props:
        out["properties"] = {
            i18n.key(k): (_localize_schema(v) if isinstance(v, dict)
                          and (v.get("properties") or v.get("items")) else v)
            for k, v in props.items()
        }
    if isinstance(schema.get("items"), dict):
        out["items"] = _localize_schema(schema["items"])
    if schema.get("required"):
        out["required"] = [i18n.key(k) for k in schema["required"]]
    return out


def _dispatch(bridge: ScenarioBridge, name: str, args: dict) -> dict:
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
    if name == "wczytaj_zapis":
        return bridge.ai_savegame(dict(args))
    if name == "moje_wojska":
        return bridge.ai_army(dict(args))
    if name == "wywiad_o_nacji":
        return bridge.ai_nation(dict(args))
    if name == "linia_frontu":
        return bridge.ai_front(dict(args))
    if name == "porownaj_ustroje":
        return bridge.ai_governments(dict(args))
    if name == "przejezdnosc":
        return bridge.ai_reach(dict(args))
    if name == "audyt_miast":
        return bridge.ai_cities(dict(args))
    if name == "co_da_rozwiazanie":
        return bridge.ai_disband(dict(args))
    if name == "szlaki_handlowe":
        return bridge.ai_trade(dict(args))
    if name == "plan_karawan":
        return bridge.ai_caravans(dict(args))
    if name == "epoki":
        return bridge.ai_eras(dict(args))
    if name == "moje_technologie":
        return bridge.ai_techs(dict(args))
    if name == "korupcja":
        return bridge.ai_corruption(dict(args))
    if name == "plan_budowy":
        return bridge.ai_build_plan(dict(args))
    if name == "gotowosc_wojenna":
        return bridge.ai_war_readiness(dict(args))
    if name == "plan_kampanii":
        return bridge.ai_campaign(dict(args))
    if name == "plan_tury":
        return bridge.ai_turn_plan(dict(args))
    if name == "plan_badan":
        return bridge.ai_research_plan(dict(args))
    if name == "plan_produkcji":
        return bridge.ai_production_plan(dict(args))
    if name == "ocena_zagrozenia":
        return bridge.ai_threats(dict(args))
    if name == "mobilnosc":
        return bridge.ai_mobility(dict(args))
    if name == "obrona_miasta":
        return bridge.ai_city_defense(dict(args))
    if name == "alerty":
        return bridge.ai_alerts(dict(args))
    if name == "uklady_dyplomatyczne":
        return bridge.ai_diplomacy(dict(args))
    if name == "dostawcy":
        return bridge.ai_providers(dict(args))
    if name == "potencjal_wzrostu":
        return bridge.ai_growth(dict(args))
    return {"blad": f"{i18n._('nieznane narzędzie')}: {name}"}


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

Bieżąca partia:
- Gdy użytkownik pyta o swoją grę („planuję ofensywę”, „czy zdążę”, „co budować”),
  zacznij od `wczytaj_zapis` — zapis zawiera turę, twoje wojska, miasta
  i dyplomację, więc nie musisz o to pytać.
- Domyślnie widzisz tylko wiedzę gracza. Pełny wgląd (`pelny_wglad=True`) to
  świadome oszustwo wobec gry — użyj go wyłącznie, gdy użytkownik wprost o to
  poprosi, i zawsze napisz, w którym trybie liczysz.

Mechanika, o której warto pamiętać:
- Zanim doradzisz ofensywę ciężkimi jednostkami, sprawdź `przejezdnosc`. Klasa
  Big Land (katapulty, działa) nie wchodzi na bagna, dżunglę i góry bez drogi —
  najsilniejsza jednostka jest bezużyteczna, jeśli nie dojdzie do celu.
- Teren, z którego atakujesz, NIE zmienia siły ataku. Liczy się wyłącznie kafel
  obrońcy. Kafel atakującego decyduje o koszcie ruchu i o tym, jak przeżyjesz
  kontratak.
- Efekty obronne tego samego typu sumują procenty, a dopiero suma mnoży obronę.
- W większości zestawów reguł rozkaz `fortify` w mieście nic nie daje, bo kafel
  miasta przyznaje tę samą premię automatycznie.
- Model liczy jedną turę szturmu: każda jednostka atakuje raz, obrońcy się nie
  leczą, a do obrony staje ten obrońca, który ma największe szanse przeżyć.
"""