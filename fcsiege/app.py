"""FCSiege - kalkulator szturmu na miasto we Freecivie."""

from __future__ import annotations

import os
import sys

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox,
                               QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QSlider, QSpinBox, QTabWidget,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from . import theme
from .advisor import (counter_advice, defense_advice, max_wave_stopped,
                      min_defenders, rank_defenders, rank_staging_terrain,
                      rank_units, wave_is_capped)
from .combat import (Side, Situation, defense_stand, duel, heals_fully_in_city,
                     siege, veteran_build_level)
from .model import Ruleset, discover_rulesets, default_ruleset_roots
from .widgets import (AnswerCard, Card, FlowRow, ModifierBars, PowerScale,
                      ProbabilityChart, StatTile)

MAX_DEFENDER_GROUPS = 3
MODE_ATTACK = "attack"
MODE_DEFENSE = "defense"


def _combo(min_chars: int = 8) -> QComboBox:
    """Lista rozwijana, ktora nie rozpycha kolumny."""
    c = QComboBox()
    c.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    c.setMinimumContentsLength(min_chars)
    c.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
    return c


def _wave(k: int) -> str:
    """Liczba napastnikow; przy gornym limicie sprawdzania dopisuje plus."""
    return f"{k}+" if wave_is_capped(k) else str(k)


def _label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("FieldLabel")
    return lbl


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("Hint")
    lbl.setWordWrap(True)
    return lbl


def _row(*widgets, spacing: int = 8) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for item in widgets:
        if isinstance(item, tuple):
            lay.addWidget(item[0], item[1])
        else:
            lay.addWidget(item)
    return w


class DefenderRow(QWidget):
    """Jeden typ obroncy w garnizonie: jednostka, liczba, doswiadczenie."""

    def __init__(self, on_change, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self.unit = _combo(9)
        self.count = QSpinBox()
        self.count.setRange(0, 24)
        self.count.setValue(0)
        self.count.setFixedWidth(62)
        self.vet = _combo(6)
        self.vet.setFixedWidth(96)

        lay.addWidget(self.unit, 1)
        lay.addWidget(self.count)
        lay.addWidget(self.vet)

        self.unit.currentIndexChanged.connect(on_change)
        self.count.valueChanged.connect(on_change)
        self.vet.currentIndexChanged.connect(on_change)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("Root")
        self.setWindowTitle("FCSiege — kalkulator szturmu (Freeciv)")
        self.resize(1480, 940)
        self.setMinimumSize(1180, 760)

        self._loading = True
        self.mode = MODE_ATTACK
        self._rs: Ruleset | None = None
        self._ruleset_dirs: dict[str, str] = {}
        self._rank_cache_valid = False
        self._last_result = None

        self._build_ui()
        self._discover_rulesets()
        self._loading = False
        self._reload_ruleset()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 16, 20, 18)
        root.setSpacing(14)

        root.addWidget(self._build_header())

        cols = QHBoxLayout()
        cols.setSpacing(14)
        cols.addWidget(self._scroll(self._build_attacker_column()), 23)
        cols.addWidget(self._scroll(self._build_defender_column()), 26)
        cols.addWidget(self._build_result_column(), 51)
        root.addLayout(cols, 1)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(90)
        self._debounce.timeout.connect(self._recalculate)

        self._apply_mode()

    def _scroll(self, inner: QWidget) -> QScrollArea:
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(inner)
        area.setFrameShape(QFrame.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.viewport().setAutoFillBackground(False)
        inner.setAutoFillBackground(False)
        return area

    def _build_header(self) -> QWidget:
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(16)

        titles = QVBoxLayout()
        titles.setSpacing(1)
        t = QLabel("FCSiege")
        t.setObjectName("AppTitle")
        s = QLabel("Szturm i obrona miasta, liczone z plików reguł Freeciva")
        s.setObjectName("AppSub")
        titles.addWidget(t)
        titles.addWidget(s)
        lay.addLayout(titles)
        lay.addSpacing(18)

        self.btn_attack = QPushButton("Szturm")
        self.btn_defense = QPushButton("Obrona")
        self.mode_group = QButtonGroup(self)
        for btn, mode in ((self.btn_attack, MODE_ATTACK),
                          (self.btn_defense, MODE_DEFENSE)):
            btn.setCheckable(True)
            btn.setObjectName("ModeButton")
            btn.setFixedWidth(84)
            self.mode_group.addButton(btn)
            btn.clicked.connect(lambda _=False, m=mode: self._set_mode(m))
        self.btn_attack.setChecked(True)
        switch = QWidget()
        sl = QHBoxLayout(switch)
        sl.setContentsMargins(3, 3, 3, 3)
        sl.setSpacing(3)
        sl.addWidget(self.btn_attack)
        sl.addWidget(self.btn_defense)
        switch.setObjectName("ModeSwitch")
        lay.addWidget(switch)
        lay.addStretch(1)

        lay.addWidget(_label("ZESTAW REGUŁ"))
        self.cmb_ruleset = QComboBox()
        self.cmb_ruleset.setFixedWidth(136)
        self.cmb_ruleset.currentIndexChanged.connect(self._on_ruleset_changed)
        lay.addWidget(self.cmb_ruleset)

        lay.addSpacing(8)
        lay.addWidget(_label("POZIOM TECHNOLOGICZNY"))
        self.sld_tech = QSlider(Qt.Horizontal)
        self.sld_tech.setFixedWidth(150)
        self.sld_tech.valueChanged.connect(self._on_tech_changed)
        lay.addWidget(self.sld_tech)
        self.lbl_tech = QLabel("—")
        self.lbl_tech.setObjectName("Hint")
        self.lbl_tech.setFixedWidth(168)
        lay.addWidget(self.lbl_tech)
        return bar

    # ------------------------------------------------------- kolumna ataku

    def _build_attacker_column(self) -> QWidget:
        col = QWidget()
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(14)

        self.card_solo = card = Card("Atakujący", "attack")
        b = card.body()
        self.lbl_solo_unit = _label("Jednostka")
        b.addWidget(self.lbl_solo_unit)
        self.cmb_att_unit = _combo(12)
        self.cmb_att_unit.currentIndexChanged.connect(self._queue)
        b.addWidget(self.cmb_att_unit)

        b.addWidget(_label("Doświadczenie"))
        self.cmb_att_vet = _combo(10)
        self.cmb_att_vet.currentIndexChanged.connect(self._queue)
        b.addWidget(self.cmb_att_vet)

        self.wrap_moves = QWidget()
        wl = QVBoxLayout(self.wrap_moves)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(6)
        wl.addWidget(_label("Ułamki ruchu w chwili ataku"))
        self.spn_moves = QSpinBox()
        self.spn_moves.setRange(1, 24)
        self.spn_moves.valueChanged.connect(self._queue)
        wl.addWidget(self.spn_moves)
        self.lbl_tired = _hint("")
        wl.addWidget(self.lbl_tired)
        b.addWidget(self.wrap_moves)

        self.chips_att = FlowRow(3)
        b.addWidget(self.chips_att)
        lay.addWidget(card)

        self.card_staging = card2 = Card("Skąd atakujesz", "attack")
        b2 = card2.body()
        self.lbl_staging_field = _label("Teren jednostki szturmowej")
        b2.addWidget(self.lbl_staging_field)
        self.cmb_att_terrain = _combo(12)
        self.cmb_att_terrain.currentIndexChanged.connect(self._queue)
        b2.addWidget(self.cmb_att_terrain)
        self.lbl_staging = _hint("")
        b2.addWidget(self.lbl_staging)
        lay.addWidget(card2)

        self.card_plan = card3 = Card("Plan", "attack")
        b3 = card3.body()
        self.lbl_plan_field = _label("Ile jednostek zamierzasz wysłać")
        b3.addWidget(self.lbl_plan_field)
        self.spn_planned = QSpinBox()
        self.spn_planned.setRange(1, 200)
        self.spn_planned.setValue(8)
        self.spn_planned.valueChanged.connect(self._queue)
        b3.addWidget(self.spn_planned)
        self.lbl_planned = _hint("")
        b3.addWidget(self.lbl_planned)
        lay.addWidget(card3)

        lay.addStretch(1)
        return col

    # ---------------------------------------------------- kolumna obroncow

    def _build_defender_column(self) -> QWidget:
        col = QWidget()
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(14)

        self.card_group = card = Card("Garnizon", "defend")
        b = card.body()
        head = QWidget()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(8)
        hl.addWidget(_label("Jednostka"), 1)
        lbl_n = _label("Ile")
        lbl_n.setFixedWidth(62)
        hl.addWidget(lbl_n)
        lbl_v = _label("Stopień")
        lbl_v.setFixedWidth(96)
        hl.addWidget(lbl_v)
        b.addWidget(head)
        self.def_rows: list[DefenderRow] = []
        for i in range(MAX_DEFENDER_GROUPS):
            r = DefenderRow(self._queue)
            self.def_rows.append(r)
            b.addWidget(r)
        self.lbl_group_hint = _hint("Ustaw liczbę 0, żeby pominąć dany typ.")
        b.addWidget(self.lbl_group_hint)
        lay.addWidget(card)

        self.card_city = card2 = Card("Miasto i teren", "defend")
        b2 = card2.body()
        self.lbl_city_terrain = _label("Teren pod miastem")
        b2.addWidget(self.lbl_city_terrain)
        self.cmb_def_terrain = _combo(12)
        self.cmb_def_terrain.currentIndexChanged.connect(self._queue)
        b2.addWidget(self.cmb_def_terrain)

        self.chk_city = QCheckBox("Obrońcy stoją w mieście")
        self.chk_city.setObjectName("ChkCity")
        self.chk_city.setChecked(True)
        self.chk_city.stateChanged.connect(self._queue)
        b2.addWidget(self.chk_city)

        b2.addWidget(_label("Wielkość miasta"))
        self.spn_size = QSpinBox()
        self.spn_size.setRange(1, 40)
        self.spn_size.setValue(8)
        self.spn_size.valueChanged.connect(self._queue)
        b2.addWidget(self.spn_size)

        self.chk_fort = QCheckBox("Obrońcy okopani (fortify)")
        self.chk_fort.setChecked(True)
        self.chk_fort.stateChanged.connect(self._queue)
        b2.addWidget(self.chk_fort)

        b2.addWidget(_label("Ulepszenia kafla"))
        self.box_extras = QWidget()
        self.lay_extras = QGridLayout(self.box_extras)
        self.lay_extras.setContentsMargins(0, 0, 0, 0)
        self.lay_extras.setSpacing(2)
        b2.addWidget(self.box_extras)

        b2.addWidget(_label("Ustrój obrońcy"))
        self.cmb_gov = _combo(10)
        self.cmb_gov.currentIndexChanged.connect(self._queue)
        b2.addWidget(self.cmb_gov)
        lay.addWidget(card2)

        self.card_blds = card3 = Card("Budowle i cuda obrońcy", "defend")
        b3 = card3.body()
        self.box_buildings = QWidget()
        self.lay_buildings = QGridLayout(self.box_buildings)
        self.lay_buildings.setContentsMargins(0, 0, 0, 0)
        self.lay_buildings.setSpacing(2)
        b3.addWidget(self.box_buildings)
        b3.addWidget(_hint("Lista pochodzi wprost z efektów obronnych "
                           "wybranego zestawu reguł."))
        lay.addWidget(card3)

        card4 = Card("Założenia obliczeń")
        b4 = card4.body()
        self.chk_barracks = QCheckBox("Obrońcy budowani w tym mieście")
        self.chk_barracks.setChecked(True)
        self.chk_barracks.stateChanged.connect(self._queue)
        b4.addWidget(self.chk_barracks)
        self.lbl_barracks_hint = _hint(
            "Stopień weterana wynika wtedy z koszar i cudów w tym mieście.")
        b4.addWidget(self.lbl_barracks_hint)
        self.chk_promo = QCheckBox("Obrońcy awansują po wygranej obronie")
        self.chk_promo.setChecked(True)
        self.chk_promo.stateChanged.connect(self._queue)
        b4.addWidget(self.chk_promo)
        b4.addWidget(_hint(
            "Szturm rozlicza się w jednej turze: każda jednostka atakuje raz, "
            "obrońcy nie zdążą się wyleczyć, a do obrony staje zawsze ten "
            "obrońca, który ma największe szanse przeżyć."))
        lay.addWidget(card4)

        lay.addStretch(1)
        return col

    # ----------------------------------------------------- kolumna wynikow

    def _build_result_column(self) -> QWidget:
        col = QWidget()
        lay = QVBoxLayout(col)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)

        self.answer = AnswerCard()
        lay.addWidget(self.answer)

        tiles = QHBoxLayout()
        tiles.setSpacing(10)
        self.tile_need = StatTile("jednostek na 90%")
        self.tile_loss = StatTile("średnie straty")
        self.tile_cost = StatTile("koszt strat")
        self.tile_duel = StatTile("pojedynek 1 na 1")
        for t in (self.tile_need, self.tile_loss, self.tile_cost, self.tile_duel):
            tiles.addWidget(t)
        lay.addLayout(tiles)

        scale_card = Card("Starcie jednostka na jednostkę")
        self.scale = PowerScale()
        scale_card.body().addWidget(self.scale)
        self.lbl_fp = _hint("")
        scale_card.body().addWidget(self.lbl_fp)
        lay.addWidget(scale_card)

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._on_tab_changed)

        self.chart = ProbabilityChart()
        self.chart_page = chart_page = QWidget()
        cp = QVBoxLayout(chart_page)
        cp.setContentsMargins(0, 8, 0, 0)
        cp.addWidget(self.chart)
        self.tabs.addTab(chart_page, "Szansa zdobycia")

        self.breakdown_page = self._build_breakdown_page()
        self.tabs.addTab(self.breakdown_page, "Rozbicie sił")

        self.tbl_units = self._make_table(
            ["Jednostka", "1 na 1", "Potrzeba", "Straty", "Koszt strat",
             "Inwestycja", "Zajmie miasto", "Technologia"])
        self.units_page = units_page = QWidget()
        up = QVBoxLayout(units_page)
        up.setContentsMargins(0, 8, 0, 0)
        up.setSpacing(8)
        self.chk_occupiers = QCheckBox(
            "Tylko jednostki, które mogą samodzielnie zająć miasto")
        self.chk_occupiers.setChecked(True)
        self.chk_occupiers.stateChanged.connect(self._on_rank_filter)
        up.addWidget(self.chk_occupiers)
        up.addWidget(self.tbl_units)
        self.tabs.addTab(units_page, "Czym uderzyć")

        self.tbl_terrain = self._make_table(
            ["Kafel wypadowy", "Wejście na kafel", "Twoja obrona",
             "Ryzyko kontrataku"])
        self.page_terrain = self._wrap_table(self.tbl_terrain)
        self.tabs.addTab(self.page_terrain, "Skąd atakować")

        # strony uzywane tylko w trybie obrony
        self.tbl_defenders = self._make_table(
            ["Jednostka", "Minimum", "Utrzymanie", "1 sztuka zatrzyma",
             "Obrona", "Koszt", "Technologia"])
        self.page_defenders = QWidget()
        dp = QVBoxLayout(self.page_defenders)
        dp.setContentsMargins(0, 8, 0, 0)
        dp.setSpacing(8)
        self.lbl_def_table_hint = _hint("")
        dp.addWidget(self.lbl_def_table_hint)
        dp.addWidget(self.tbl_defenders)
        self.tbl_resilience = self._make_table(["Garnizon"])
        self.page_resilience = QWidget()
        rp = QVBoxLayout(self.page_resilience)
        rp.setContentsMargins(0, 8, 0, 0)
        rp.setSpacing(8)
        rp.addWidget(_hint(
            "Ilu napastników danego typu odeprze garnizon w jednej turze "
            "przy 95% pewności. Liczby dotyczą wybranej jednostki obronnej."))
        rp.addWidget(self.tbl_resilience)

        self.tips_page = QWidget()
        tp = QVBoxLayout(self.tips_page)
        tp.setContentsMargins(4, 10, 4, 4)
        tp.setSpacing(8)
        self.lbl_tips = QLabel("")
        self.lbl_tips.setWordWrap(True)
        self.lbl_tips.setTextFormat(Qt.RichText)
        self.lbl_tips.setAlignment(Qt.AlignTop)
        tp.addWidget(self.lbl_tips)
        tp.addStretch(1)
        self.tabs.addTab(self.tips_page, "Wskazówki")

        lay.addWidget(self.tabs, 1)
        return col

    def _build_breakdown_page(self) -> QWidget:
        page = QWidget()
        lay = QHBoxLayout(page)
        lay.setContentsMargins(0, 10, 0, 0)
        lay.setSpacing(12)

        left = Card("Atak")
        self.bars_att = ModifierBars()
        left.body().addWidget(self.bars_att)
        left.body().addStretch(1)

        right = Card("Obrona")
        self.bars_def = ModifierBars()
        right.body().addWidget(self.bars_def)
        self.lbl_def_detail = _hint("")
        right.body().addWidget(self.lbl_def_detail)
        right.body().addStretch(1)

        lay.addWidget(left, 1)
        lay.addWidget(right, 1)
        return page

    def _make_table(self, headers: list[str]) -> QTableWidget:
        t = QTableWidget(0, len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QTableWidget.SelectRows)
        t.setEditTriggers(QTableWidget.NoEditTriggers)
        t.setShowGrid(False)
        t.setAlternatingRowColors(False)
        hh = t.horizontalHeader()
        for i in range(len(headers)):
            hh.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        # ostatnia kolumna wypelnia pozostale miejsce, reszta dopasowuje sie
        # do tresci - dzieki temu nazwy jednostek nie sa obcinane
        hh.setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)
        hh.setMinimumSectionSize(64)
        return t

    def _wrap_table(self, table: QTableWidget) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.addWidget(table)
        return w

    # ------------------------------------------------------------ tryb pracy

    def _set_mode(self, mode: str) -> None:
        # przyciski trzeba zsynchronizowac takze przy zmianie z kodu
        self.btn_attack.setChecked(mode == MODE_ATTACK)
        self.btn_defense.setChecked(mode == MODE_DEFENSE)
        if mode == self.mode:
            return
        self.mode = mode
        self._apply_mode()
        self._loading = True
        self._populate_units()
        self._loading = False
        self._rank_cache_valid = False
        self._recalculate()

    def _apply_mode(self) -> None:
        """Przestawia napisy i zakladki pod wybrany tryb."""
        atk = self.mode == MODE_ATTACK
        self.card_solo.set_title("Atakujący" if atk else "Mój obrońca",
                                 "attack" if atk else "defend")
        self.card_staging.set_title("Skąd atakujesz" if atk else "Skąd naciera wróg",
                                    "attack" if atk else "defend")
        self.card_plan.set_title("Plan" if atk else "Plan obrony",
                                 "attack" if atk else "defend")
        self.card_group.set_title("Garnizon wroga" if atk else "Siły wroga",
                                  "defend" if atk else "attack")
        self.card_city.set_title("Miasto wroga i teren" if atk else "Moje miasto",
                                 "defend" if atk else "defend")
        self.card_blds.set_title("Budowle i cuda obrońcy" if atk
                                 else "Moje budowle i cuda", "defend")
        self.lbl_solo_unit.setText("Jednostka szturmowa" if atk
                                   else "Jednostka obronna")
        self.lbl_staging_field.setText("Teren jednostki szturmowej" if atk
                                       else "Teren, z którego wróg naciera")
        self.lbl_plan_field.setText("Ile jednostek zamierzasz wysłać" if atk
                                    else "Ilu obrońców zostawiasz")
        self.lbl_city_terrain.setText("Teren pod miastem")
        self.chk_city.setText("Obrońcy stoją w mieście" if atk
                              else "Bronię się w mieście")
        self.lbl_group_hint.setText(
            "Ustaw liczbę 0, żeby pominąć dany typ." if atk else
            "Wpisz siły, którymi wróg uderzy w jednej turze.")
        self.chk_barracks.setVisible(not atk)
        self.lbl_barracks_hint.setVisible(not atk)
        self.tile_need.set_label("jednostek na 90%" if atk else "obrońców na 95%")
        self.tile_loss.set_label("średnie straty" if atk else "straty wroga")
        self.tile_cost.set_label("koszt strat" if atk else "zatrzyma")
        self.tile_duel.set_label("pojedynek 1 na 1" if atk else "utrzymanie")
        self.wrap_moves.setVisible(atk and bool(self._rs)
                                   and self._rs.combat.tired_attack)

        # przebudowa zakladek
        current = self.tabs.tabText(self.tabs.currentIndex())
        while self.tabs.count():
            self.tabs.removeTab(0)
        pages = [(self.chart_page, "Szansa zdobycia" if atk else "Szansa utrzymania"),
                 (self.breakdown_page, "Rozbicie sił")]
        if atk:
            pages.append((self.units_page, "Czym uderzyć"))
            pages.append((self.page_terrain, "Skąd atakować"))
        else:
            pages.append((self.page_defenders, "Czym bronić"))
            pages.append((self.page_resilience, "Wytrzymałość"))
        pages.append((self.tips_page, "Wskazówki"))
        for page, title in pages:
            page.setVisible(True)
            self.tabs.addTab(page, title)
        for i in range(self.tabs.count()):
            if self.tabs.tabText(i) == current:
                self.tabs.setCurrentIndex(i)
                break

    # --------------------------------------------------------- zestawy regul

    def _discover_rulesets(self) -> None:
        seen: dict[str, str] = {}
        for root in default_ruleset_roots():
            for d in discover_rulesets(root):
                name = os.path.basename(d)
                if name in ("stub", "ruledit", "override"):
                    continue
                seen.setdefault(name, d)
        self._ruleset_dirs = seen
        self.cmb_ruleset.blockSignals(True)
        self.cmb_ruleset.clear()
        for name in sorted(seen):
            self.cmb_ruleset.addItem(name, seen[name])
        idx = self.cmb_ruleset.findText("classic")
        self.cmb_ruleset.setCurrentIndex(max(0, idx))
        self.cmb_ruleset.blockSignals(False)

    def _on_ruleset_changed(self) -> None:
        if not self._loading:
            self._reload_ruleset()

    def _reload_ruleset(self) -> None:
        path = self.cmb_ruleset.currentData()
        if not path:
            return
        self._loading = True
        self._rs = Ruleset.load(path)
        rs = self._rs

        # suwak technologii
        maxd = max(1, rs.max_tech_depth())
        self.sld_tech.blockSignals(True)
        self.sld_tech.setRange(0, maxd)
        self.sld_tech.setValue(maxd)
        self.sld_tech.blockSignals(False)

        self._populate_units()
        self._populate_terrain()
        self._populate_extras()
        self._populate_buildings()
        self._populate_gov()

        self.wrap_moves.setVisible(rs.combat.tired_attack
                                   and self.mode == MODE_ATTACK)
        self.spn_moves.blockSignals(True)
        self.spn_moves.setRange(1, max(1, rs.move_fragments * 3))
        self.spn_moves.setValue(rs.move_fragments)
        self.spn_moves.blockSignals(False)
        self.lbl_tired.setText(
            f"Ten zestaw ma tired_attack: atak z mniej niż {rs.move_fragments} "
            f"ułamkami ruchu jest proporcjonalnie słabszy."
            if rs.combat.tired_attack else "")

        self._loading = False
        self._rank_cache_valid = False
        self._recalculate()

    def _known_techs(self) -> set[str] | None:
        assert self._rs
        depth = self.sld_tech.value()
        if depth >= self._rs.max_tech_depth():
            return None
        return self._rs.techs_up_to(depth)

    def _on_tech_changed(self) -> None:
        if self._loading or not self._rs:
            return
        self._loading = True
        self._populate_units(keep=True)
        self._loading = False
        self._rank_cache_valid = False
        self._queue()

    def _unit_label(self, ut) -> str:
        return f"{ut.name}   {ut.attack}/{ut.defense}/{ut.hitpoints}hp"

    def _populate_units(self, keep: bool = False) -> None:
        assert self._rs
        rs = self._rs
        known = self._known_techs()
        avail = rs.units_available(known)

        attackers = sorted([u for u in avail if u.attack > 0 and "NonMil" not in u.flags],
                           key=lambda u: (rs.unit_tech_depth(u), u.name))
        defenders = sorted([u for u in avail if u.defense > 0 and "NonMil" not in u.flags],
                           key=lambda u: (rs.unit_tech_depth(u), u.name))

        depth = self.sld_tech.value()
        self.lbl_tech.setText(
            f"próg {depth} · {len(avail)} jednostek" if known is not None
            else f"pełne drzewo · {len(avail)} jednostek")

        atk = self.mode == MODE_ATTACK
        # w trybie szturmu pojedyncza karta to moja jednostka atakujaca,
        # w trybie obrony - moj obronca; grupa jest zawsze stroną wroga
        solo_pool = attackers if atk else defenders
        group_pool = defenders if atk else attackers
        solo_default = ["Catapult", "Legion", "Archers", "Star Marines"] if atk \
            else ["Phalanx", "Pikemen", "Musketeers", "Warriors", "Militia"]

        prev_att = self.cmb_att_unit.currentData() if keep else None
        self.cmb_att_unit.blockSignals(True)
        self.cmb_att_unit.clear()
        for u in solo_pool:
            self.cmb_att_unit.addItem(self._unit_label(u), u.name)
        self._select_data(self.cmb_att_unit, prev_att, solo_default)
        self.cmb_att_unit.blockSignals(False)
        self._populate_vet(self.cmb_att_vet, self._current_attacker())

        defaults = [["Warriors", "Militia", "Phalanx"], ["Phalanx"], ["Musketeers"]] \
            if atk else [["Warriors", "Militia", "Legion"], ["Archers"], ["Catapult"]]
        for i, row in enumerate(self.def_rows):
            prev = row.unit.currentData() if keep else None
            row.unit.blockSignals(True)
            row.unit.clear()
            for u in group_pool:
                row.unit.addItem(self._unit_label(u), u.name)
            self._select_data(row.unit, prev, defaults[i])
            row.unit.blockSignals(False)
            if not keep:
                row.count.blockSignals(True)
                row.count.setValue(5 if i == 0 else 0)
                row.count.blockSignals(False)
            ut = rs.units.get(row.unit.currentData())
            self._populate_vet(row.vet, ut)

    def _select_data(self, combo: QComboBox, prev, fallbacks: list[str]) -> None:
        if prev is not None:
            i = combo.findData(prev)
            if i >= 0:
                combo.setCurrentIndex(i)
                return
        for name in fallbacks:
            i = combo.findData(name)
            if i >= 0:
                combo.setCurrentIndex(i)
                return
        if combo.count():
            combo.setCurrentIndex(0)

    def _populate_vet(self, combo: QComboBox, ut) -> None:
        prev = combo.currentIndex()
        combo.blockSignals(True)
        combo.clear()
        levels = ut.vet_levels if ut else []
        for i, lv in enumerate(levels):
            combo.addItem(f"{lv.name} ×{lv.power_fact / 100:g}", i)
        combo.setCurrentIndex(prev if 0 <= prev < combo.count() else 0)
        combo.blockSignals(False)

    def _populate_terrain(self) -> None:
        assert self._rs
        lands = sorted(self._rs.land_terrains(),
                       key=lambda t: (-t.defense_bonus, t.name))
        for combo, fallback in ((self.cmb_def_terrain, "Hills"),
                                (self.cmb_att_terrain, "Forest")):
            prev = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            for t in lands:
                suffix = f"  +{t.defense_bonus}% obrony" if t.defense_bonus else ""
                combo.addItem(f"{t.name}{suffix}", t.name)
            self._select_data(combo, prev, [fallback])
            combo.blockSignals(False)

    def _clear_grid(self, layout: QGridLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                # setParent(None) usuwa widget natychmiast; samo deleteLater
                # zostawiloby go na ekranie do nastepnej petli zdarzen
                w.setParent(None)
                w.deleteLater()

    def _populate_extras(self) -> None:
        assert self._rs
        self._clear_grid(self.lay_extras)
        self.chk_extras: dict[str, QCheckBox] = {}
        extras = self._rs.defensive_extras()
        for i, e in enumerate(extras):
            suffix = f" (+{e.defense_bonus}%)" if e.defense_bonus else ""
            cb = QCheckBox(f"{e.name}{suffix}")
            cb.stateChanged.connect(self._queue)
            self.chk_extras[e.name] = cb
            self.lay_extras.addWidget(cb, i // 2, i % 2)
        if not extras:
            self.lay_extras.addWidget(_hint("brak w tym zestawie reguł"), 0, 0)

    def _populate_buildings(self) -> None:
        assert self._rs
        self._clear_grid(self.lay_buildings)
        self.chk_buildings: dict[str, QCheckBox] = {}
        blds = self._rs.defensive_buildings()
        for i, b in enumerate(blds):
            tag = " ★" if b.is_wonder else ""
            cb = QCheckBox(f"{b.name}{tag}")
            cb.setToolTip(f"{b.genus}, koszt {b.build_cost}")
            cb.setChecked(b.name in ("City Walls", "Force Walls"))
            cb.stateChanged.connect(self._queue)
            self.chk_buildings[b.name] = cb
            self.lay_buildings.addWidget(cb, i // 2, i % 2)

    def _populate_gov(self) -> None:
        assert self._rs
        prev = self.cmb_gov.currentText()
        self.cmb_gov.blockSignals(True)
        self.cmb_gov.clear()
        for g in self._rs.governments:
            self.cmb_gov.addItem(g)
        i = self.cmb_gov.findText(prev)
        if i < 0:
            i = self.cmb_gov.findText("Despotism")
        self.cmb_gov.setCurrentIndex(max(0, i))
        self.cmb_gov.blockSignals(False)

    # ------------------------------------------------------------ obliczenia

    def _queue(self) -> None:
        if not self._loading:
            self._rank_cache_valid = False
            self._debounce.start()

    def _current_attacker(self):
        if not self._rs:
            return None
        return self._rs.units.get(self.cmb_att_unit.currentData())

    def _situation(self) -> Situation:
        assert self._rs
        rs = self._rs
        buildings, wonders = set(), set()
        for name, cb in self.chk_buildings.items():
            if cb.isChecked():
                b = rs.buildings.get(name)
                if b and b.is_wonder:
                    wonders.add(name)
                else:
                    buildings.add(name)
        extras = {n for n, cb in self.chk_extras.items() if cb.isChecked()}
        terr = rs.terrains.get(self.cmb_def_terrain.currentData())
        att_terr = rs.terrains.get(self.cmb_att_terrain.currentData())
        defenders_total = sum(r.count.value() for r in self.def_rows)

        return Situation(
            terrain=terr or next(iter(rs.terrains.values())),
            extras=extras,
            in_city=self.chk_city.isChecked(),
            city_size=self.spn_size.value(),
            buildings=buildings,
            player_buildings=wonders,
            fortified=self.chk_fort.isChecked(),
            gov=self.cmb_gov.currentText(),
            techs=self._known_techs() or set(rs.techs),
            units_on_tile=max(1, defenders_total),
            attacker_terrain=att_terr,
        )

    def _defenders(self) -> list[Side]:
        assert self._rs
        out = []
        for row in self.def_rows:
            n = row.count.value()
            if n <= 0:
                continue
            ut = self._rs.units.get(row.unit.currentData())
            if ut:
                out.append(Side(ut, max(0, row.vet.currentIndex()), n))
        return out

    def _attacker(self) -> Side | None:
        ut = self._current_attacker()
        if not ut:
            return None
        moves = self.spn_moves.value() if self._rs.combat.tired_attack else None
        return Side(ut, max(0, self.cmb_att_vet.currentIndex()), 1, moves)

    def _recalculate(self) -> None:
        if not self._rs:
            return
        if self.mode == MODE_DEFENSE:
            self._recalculate_defense()
        else:
            self._recalculate_attack()

    # ------------------------------------------------------------- obrona

    def _recalculate_defense(self) -> None:
        rs = self._rs
        sit = self._situation()
        enemy = self._defenders()          # grupa = siły wroga
        ut = self._current_attacker()      # pojedyncza karta = mój obrońca
        if ut is None:
            return

        vet = veteran_build_level(rs, sit, ut) if self.chk_barracks.isChecked() \
            else max(0, self.cmb_att_vet.currentIndex())
        self._populate_vet(self.cmb_att_vet, ut)
        self.cmb_att_vet.blockSignals(True)
        self.cmb_att_vet.setCurrentIndex(min(vet, self.cmb_att_vet.count() - 1))
        self.cmb_att_vet.blockSignals(False)
        self.cmb_att_vet.setEnabled(not self.chk_barracks.isChecked())
        self._update_defender_chips(ut, vet, sit)

        if not enemy:
            self.answer.set_answer(
                "Brak zagrożenia", "Nie wskazałeś żadnych sił wroga.",
                "Wpisz w karcie „Siły wroga”, czym i ilu jednostkami uderzy.",
                theme.GOOD)
            self._clear_results()
            return

        planned = self.spn_planned.value()
        promo = self.chk_promo.isChecked()
        rng = np.random.default_rng(2024)

        res = defense_stand(rs, enemy, [Side(ut, vet, planned)], sit,
                            promotions=promo, trials=30000)
        mc, p_at = min_defenders(rs, enemy, Side(ut, vet), sit,
                                 confidence=0.95, promotions=promo,
                                 trials=12000, rng=rng)
        main_att = max(enemy, key=lambda a: a.utype.attack * a.vet_fact())
        stops = max_wave_stopped(rs, main_att, [Side(ut, vet, planned)], sit,
                                 0.95, promo, 8000, rng)
        self._last_result = res

        self._update_defense_answer(ut, vet, res, mc, p_at, planned, stops)
        self._update_defense_tiles(ut, vet, res, mc, stops, planned)

        d = res.duels[0][2] if res.duels else None
        if d is not None:
            self.scale.set_values(d.attack_power, d.defense_power,
                                  main_att.utype.name, ut.name)
            self.lbl_fp.setText(
                f"siła ognia {d.attacker_fp} : {d.defender_fp}   ·   "
                f"punkty życia {d.attacker_hp} : {d.defender_hp}   ·   "
                f"trafień do zabicia {d.rounds_needed_att} : {d.rounds_needed_def}")
            self._update_breakdown(d)

        self._update_defense_chart(rs, enemy, ut, vet, sit, promo, planned)
        self._update_defense_staging(rs, enemy, ut, vet, sit)
        tips = defense_advice(rs, res, enemy, [Side(ut, vet, planned)], sit, stops)
        self._render_tips(tips, res.notes, rs,
                          f"{res.n_attacks} napastników kontra {planned} obrońców")
        self._refresh_active_tab()

    # ------------------------------------------------------------- szturm

    def _recalculate_attack(self) -> None:
        rs = self._rs
        att = self._attacker()
        defenders = self._defenders()
        sit = self._situation()

        if att is None:
            return

        self.cmb_att_vet.setEnabled(True)
        self._populate_vet(self.cmb_att_vet, att.utype)
        self._update_attacker_chips(att)

        if not defenders:
            self.answer.set_answer(
                "1 jednostka", "Miasto jest puste — wystarczy do niego wejść.",
                "Pamiętaj, że wejście do miasta zabiera 1 punkt ludności.",
                theme.GOOD)
            self._clear_results()
            return

        res = siege(rs, att, defenders, sit,
                    promotions=self.chk_promo.isChecked(), trials=30000)
        self._last_result = res
        d = res.duel

        self._update_answer(att, res, sit)
        self._update_tiles(att, res)
        self._update_scale(att, defenders, d)
        self._update_chart(res)
        self._update_breakdown(d)
        self._update_staging(att, defenders, sit)
        self._update_tips(rs, res, att, defenders, sit)
        self._refresh_active_tab()

    # ------------------------------------------------ prezentacja obrony

    def _update_defender_chips(self, ut, vet: int, sit: Situation) -> None:
        rs = self._rs
        lv = ut.vet_levels[min(vet, len(ut.vet_levels) - 1)] if ut.vet_levels else None
        chips = [
            (f"obrona {ut.defense}", theme.DEFEND),
            (f"atak {ut.attack}", theme.ATTACK),
            (f"{ut.hitpoints} HP", theme.TEXT_DIM),
            (f"{ut.build_cost} tarcz", theme.TEXT_DIM),
            (f"utrzymanie {ut.uk_shield}", theme.TEXT_DIM),
        ]
        if lv is not None and lv.power_fact > 100:
            chips.append((f"{lv.name} ×{lv.power_fact / 100:g}", theme.GOOD))
        if heals_fully_in_city(rs, sit, ut):
            chips.append(("leczy się do pełna", theme.GOOD))
        self.chips_att.set_chips(chips)

    def _update_defense_answer(self, ut, vet: int, res, mc, p_at: float,
                               planned: int, stops: int) -> None:
        if mc is None:
            self.answer.set_answer(
                "Nie obronisz tego miasta",
                f"Nawet {12} × {ut.name} nie utrzyma miasta z pewnością 95%.",
                "Zmień jednostkę obronną albo dobuduj umocnienia.", theme.BAD)
            return
        tone = theme.GOOD if res.p_hold >= 0.95 else \
            theme.WARN if res.p_hold >= 0.75 else theme.BAD
        self.answer.set_answer(
            f"minimum {mc} × {ut.name}",
            f"tyle wystarczy, by odeprzeć {res.n_attacks} napastników "
            f"z pewnością {p_at * 100:.1f}%",
            f"twoje {planned} obrońców utrzyma miasto w {res.p_hold * 100:.1f}% "
            f"i zatrzyma {_wave(stops)} takich napastników na turę",
            tone)
        self.lbl_planned.setText(
            f"Przy {planned} obrońcach miasto utrzyma się z "
            f"prawdopodobieństwem {res.p_hold * 100:.1f}%.")

    def _update_defense_tiles(self, ut, vet: int, res, mc, stops: int,
                              planned: int) -> None:
        self.tile_need.set_value(
            str(mc) if mc is not None else "—",
            "na 95% pewności", theme.ACCENT)
        self.tile_loss.set_value(
            f"{res.mean_att_losses:.1f}",
            f"z {res.n_attacks} napastników", theme.GOOD)
        self.tile_cost.set_value(
            _wave(stops), "napastników na turę", theme.GOOD)
        self.tile_duel.set_value(
            f"{res.p_hold * 100:.1f}%",
            f"przy {planned} obrońcach",
            theme.GOOD if res.p_hold > 0.95 else
            theme.WARN if res.p_hold > 0.75 else theme.BAD)

    def _update_defense_chart(self, rs, enemy, ut, vet, sit, promo,
                              planned: int) -> None:
        """Krzywa: ilu obroncow kontra szansa utrzymania miasta."""
        rng = np.random.default_rng(555)
        top = max(6, min(12, planned + 3))
        cdf = np.zeros(top + 1)
        for m in range(1, top + 1):
            r = defense_stand(rs, enemy, [Side(ut, vet, m)], sit,
                              promotions=promo, trials=6000, rng=rng)
            cdf[m] = r.p_hold
        cdf = np.maximum.accumulate(cdf)
        marks = []
        seen_x: set[int] = set()
        for conf, label in ((0.5, "50%"), (0.95, "95%"), (0.99, "99%")):
            idx = int(np.searchsorted(cdf, conf, side="left"))
            if idx <= top and idx not in seen_x:
                seen_x.add(idx)
                marks.append((idx, float(cdf[idx]), label))
        self.chart.set_data(cdf, marks, min(planned, top))
        self.chart.set_axis_label("liczba obrońców w mieście")

    def _update_defense_staging(self, rs, enemy, ut, vet, sit) -> None:
        """Kafel wroga: nie zmienia jego ataku, ale decyduje o wypadzie."""
        terr = sit.attacker_terrain
        if terr is None:
            return
        field = Situation(terrain=terr, in_city=False, fortified=True,
                          gov=sit.gov, techs=sit.techs)
        best = max(enemy, key=lambda a: a.utype.attack * a.vet_fact())
        sortie = duel(rs, Side(ut, vet), Side(best.utype, best.vet), field)
        self.lbl_staging.setText(
            f"Teren wroga <b>nie zmienia siły jego ataku</b> — liczy się tylko "
            f"twój kafel. Ma za to znaczenie, gdybyś chciał wyjść i uderzyć: "
            f"na „{terr.name}” {best.utype.name} broni się z siłą "
            f"{sortie.defense_power / 10:.1f}, więc twój {ut.name} "
            f"w natarciu wygrywa tylko w {sortie.p_win * 100:.0f}%.")
        self.lbl_staging.setTextFormat(Qt.RichText)

    def _update_defenders_table(self) -> None:
        rs = self._rs
        sit = self._situation()
        enemy = self._defenders()
        if not enemy:
            self.tbl_defenders.setRowCount(0)
            return
        opts = rank_defenders(rs, enemy, sit, self._known_techs(),
                              confidence=0.95,
                              promotions=self.chk_promo.isChecked(),
                              trials=3000,
                              from_barracks=self.chk_barracks.isChecked())
        current = self.cmb_att_unit.currentData()
        stopien = opts[0].vet_name if opts else "green"
        self.lbl_def_table_hint.setText(
            f"Sortowane od najtańszego garnizonu, który utrzyma miasto z 95% "
            f"pewnością. Jednostki budowane tutaj startują na stopniu "
            f"„{stopien}”. Kolumna „1 sztuka zatrzyma” mówi, ilu takich "
            f"napastników powstrzyma pojedyncza jednostka — to miara zapasu "
            f"bezpieczeństwa.")
        self.tbl_defenders.setRowCount(len(opts))
        for i, o in enumerate(opts):
            self._set_cell(self.tbl_defenders, i, 0, o.name,
                           bold=(o.name == current))
            self._set_cell(self.tbl_defenders, i, 1,
                           str(o.min_count) if o.min_count else "—",
                           color=theme.GOOD if i == 0 else None)
            self._set_cell(self.tbl_defenders, i, 2, f"{o.p_at_min * 100:.1f}%")
            self._set_cell(self.tbl_defenders, i, 3, _wave(o.stops_alone))
            self._set_cell(self.tbl_defenders, i, 4, f"{o.defense_power / 10:.1f}")
            self._set_cell(self.tbl_defenders, i, 5,
                           str(o.shields) if o.shields else "—")
            self._set_cell(self.tbl_defenders, i, 6, ", ".join(o.req_techs) or "—")

    def _update_resilience_table(self) -> None:
        """Ilu napastnikow kazdego typu zatrzyma garnizon danej wielkosci."""
        rs = self._rs
        sit = self._situation()
        ut = self._current_attacker()
        if ut is None:
            return
        vet = veteran_build_level(rs, sit, ut) if self.chk_barracks.isChecked() \
            else max(0, self.cmb_att_vet.currentIndex())

        known = self._known_techs()
        threats = [u for u in rs.units_available(known)
                   if u.attack > 0 and "NonMil" not in u.flags
                   and rs.uclass_of(u).can_occupy_city]
        threats.sort(key=lambda u: (rs.unit_tech_depth(u), -u.attack))
        threats = threats[:7]

        headers = ["Garnizon"] + [u.name for u in threats]
        self.tbl_resilience.setColumnCount(len(headers))
        self.tbl_resilience.setHorizontalHeaderLabels(headers)
        for i in range(len(headers)):
            self.tbl_resilience.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeToContents)
        self.tbl_resilience.horizontalHeader().setSectionResizeMode(
            len(headers) - 1, QHeaderView.Stretch)

        rng = np.random.default_rng(77)
        counts = [1, 2, 3, 4, 6]
        self.tbl_resilience.setRowCount(len(counts))
        for r, m in enumerate(counts):
            self._set_cell(self.tbl_resilience, r, 0, f"{m} × {ut.name}")
            for c, threat in enumerate(threats, start=1):
                k = max_wave_stopped(rs, Side(threat, 0), [Side(ut, vet, m)],
                                     sit, 0.95,
                                     self.chk_promo.isChecked(), 2500, rng)
                tone = theme.GOOD if k >= 5 else theme.WARN if k >= 2 else theme.BAD
                self._set_cell(self.tbl_resilience, r, c, _wave(k), color=tone)

    def _render_tips(self, tips: list[str], notes: list[str], rs,
                     footer: str) -> None:
        html = ["<div style='line-height:170%'>"]
        for t in tips:
            html.append(f"<p style='margin:0 0 10px 0'>"
                        f"<span style='color:{theme.ACCENT}'>▸</span> {t}</p>")
        if notes:
            html.append(f"<p style='margin-top:14px;color:{theme.TEXT_FAINT}'>"
                        "<b>Uwagi silnika:</b></p>")
            for n in notes:
                html.append(f"<p style='margin:0 0 6px 0;color:{theme.TEXT_FAINT}'>"
                            f"• {n}</p>")
        html.append(f"<p style='margin-top:16px;color:{theme.TEXT_FAINT}'>"
                    f"Model: {footer}, zestaw reguł <b>{rs.name}</b>.</p></div>")
        self.lbl_tips.setText("".join(html))

    def _update_attacker_chips(self, att: Side) -> None:
        ut = att.utype
        rs = self._rs
        uc = rs.uclass_of(ut)
        chips = [
            (f"atak {ut.attack}", theme.ATTACK),
            (f"obrona {ut.defense}", theme.DEFEND),
            (f"{ut.hitpoints} HP", theme.TEXT_DIM),
            (f"ogień {ut.firepower}", theme.WARN),
            (f"{ut.build_cost} tarcz", theme.TEXT_DIM),
        ]
        if not uc.can_occupy_city:
            chips.append(("nie zajmie miasta", theme.BAD))
        self.chips_att.set_chips(chips)

    def _update_answer(self, att: Side, res, sit: Situation) -> None:
        need90 = res.attacks_for(0.90)
        planned = self.spn_planned.value()
        p_plan = res.p_with(planned)

        if need90 is None:
            self.answer.set_answer(
                "Nie do zdobycia", f"{att.utype.name} nie przebije tej obrony.",
                "Zmień jednostkę albo poczekaj na lepszą technologię.", theme.BAD)
            return

        occupy = "" if self._rs.uclass_of(att.utype).can_occupy_city \
            else "  (+ jednostka lądowa do zajęcia miasta)"
        total = need90 + 1
        self.answer.set_answer(
            f"{total} × {att.utype.name}",
            f"{need90} do wybicia obrońców z pewnością 90% + 1 na wejście "
            f"do miasta{occupy}",
            f"średnio zginie {res.mean_losses:.1f} jednostek "
            f"({res.mean_shields_lost:.0f} tarcz); "
            f"przy {planned} jednostkach szansa wynosi {p_plan * 100:.1f}%",
            theme.GOOD if p_plan >= 0.9 else theme.WARN if p_plan >= 0.5 else theme.BAD)

        self.lbl_planned.setText(
            f"Przy {planned} jednostkach zdobędziesz miasto z "
            f"prawdopodobieństwem {p_plan * 100:.1f}%.")

    def _update_tiles(self, att: Side, res) -> None:
        n90 = res.attacks_for(0.90)
        n50 = res.attacks_for(0.50)
        self.tile_need.set_value(
            str(n90) if n90 is not None else "—",
            f"mediana {n50}" if n50 is not None else "",
            theme.ACCENT)
        self.tile_loss.set_value(
            f"{res.mean_losses:.1f}",
            f"z {res.mean_attacks:.1f} ataków",
            theme.BAD if res.mean_losses > res.n_defenders else theme.WARN)
        self.tile_cost.set_value(
            f"{res.mean_shields_lost:.0f}",
            f"tarcz · {att.utype.build_cost}/szt.", theme.WARN)
        self.tile_duel.set_value(
            f"{res.duel.p_win * 100:.1f}%",
            "na jeden atak",
            theme.GOOD if res.duel.p_win > 0.6 else
            theme.WARN if res.duel.p_win > 0.3 else theme.BAD)

    def _update_scale(self, att: Side, defenders: list[Side], d) -> None:
        self.scale.set_values(d.attack_power, d.defense_power,
                              att.utype.name, defenders[0].utype.name)
        parts = [f"siła ognia {d.attacker_fp} : {d.defender_fp}",
                 f"punkty życia {d.attacker_hp} : {d.defender_hp}",
                 f"trafień do zabicia {d.rounds_needed_att} : {d.rounds_needed_def}"]
        self.lbl_fp.setText("   ·   ".join(parts))

    def _update_chart(self, res) -> None:
        marks = []
        for conf, label in ((0.5, "50%"), (0.9, "90%"), (0.99, "99%")):
            x = res.attacks_for(conf)
            if x is not None:
                marks.append((x, float(res.p_success_by_attacks[x]), label))
        self.chart.set_axis_label("liczba atakujących jednostek")
        self.chart.set_data(res.p_success_by_attacks, marks, self.spn_planned.value())

    def _update_breakdown(self, d) -> None:
        self.bars_att.set_rows(
            d.attack_bd.base, d.attack_bd.total,
            [(m.label, m.factor, theme.ATTACK, m.details)
             for m in d.attack_bd.modifiers])
        self.bars_def.set_rows(
            d.defense_bd.base, d.defense_bd.total,
            [(m.label, m.factor, theme.DEFEND, m.details)
             for m in d.defense_bd.modifiers])
        details = []
        for m in d.defense_bd.modifiers:
            if m.details:
                details.append(f"<b>{m.label}</b>: " + ", ".join(m.details))
        self.lbl_def_detail.setText("<br>".join(details))
        self.lbl_def_detail.setTextFormat(Qt.RichText)

    def _update_staging(self, att: Side, defenders: list[Side],
                        sit: Situation) -> None:
        rs = self._rs
        att_terr = sit.attacker_terrain
        if att_terr is None:
            return
        opts = rank_staging_terrain(rs, att, defenders, sit)
        chosen = next((o for o in opts if o.terrain.name == att_terr.name), None)

        msg = ("We Freecivie teren, z którego atakujesz, <b>nie zmienia siły "
               "twojego ataku</b> — liczy się wyłącznie kafel obrońcy. "
               "Twój kafel decyduje o czymś innym: ")
        if chosen:
            msg += (f"stojąc na „{att_terr.name}” masz obronę "
                    f"{chosen.own_defense / 10:.1f}, a kontratak najgroźniejszego "
                    f"obrońcy zabiłby cię z prawdopodobieństwem "
                    f"{chosen.p_counter * 100:.0f}%. "
                    f"Wejście na kafel miasta kosztuje {sit.terrain.movement_cost} "
                    f"pkt. ruchu.")
        self.lbl_staging.setText(msg)
        self.lbl_staging.setTextFormat(Qt.RichText)

        self.tbl_terrain.setRowCount(len(opts))
        for i, o in enumerate(opts):
            self._set_cell(self.tbl_terrain, i, 0, o.terrain.name,
                           bold=(o.terrain.name == att_terr.name))
            self._set_cell(self.tbl_terrain, i, 1, str(o.move_cost))
            self._set_cell(self.tbl_terrain, i, 2, f"{o.own_defense / 10:.1f}")
            tone = theme.GOOD if o.p_counter < 0.25 else \
                theme.WARN if o.p_counter < 0.6 else theme.BAD
            self._set_cell(self.tbl_terrain, i, 3, f"{o.p_counter * 100:.0f}%",
                           color=tone)

    def _update_tips(self, rs, res, att, defenders, sit) -> None:
        tips = counter_advice(rs, res, att, defenders, sit)
        html = ["<div style='line-height:170%'>"]
        for t in tips:
            html.append(f"<p style='margin:0 0 10px 0'>"
                        f"<span style='color:{theme.ACCENT}'>▸</span> {t}</p>")
        if res.notes:
            html.append(f"<p style='margin-top:14px;color:{theme.TEXT_FAINT}'>"
                        "<b>Uwagi silnika:</b></p>")
            for n in res.notes:
                html.append(f"<p style='margin:0 0 6px 0;color:{theme.TEXT_FAINT}'>"
                            f"• {n}</p>")
        html.append(f"<p style='margin-top:16px;color:{theme.TEXT_FAINT}'>"
                    f"Model: {res.n_defenders} obrońców, symulacja 30 000 "
                    f"szturmów na zestawie reguł <b>{rs.name}</b>.</p></div>")
        self.lbl_tips.setText("".join(html))

    def _set_cell(self, table: QTableWidget, r: int, c: int, text: str,
                  color: str | None = None, bold: bool = False) -> None:
        item = QTableWidgetItem(text)
        if color:
            from PySide6.QtGui import QColor
            item.setForeground(QColor(color))
        if bold:
            f = item.font()
            f.setBold(True)
            item.setFont(f)
        if c > 0:
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        table.setItem(r, c, item)

    def _on_tab_changed(self) -> None:
        self._refresh_active_tab()

    def _on_rank_filter(self) -> None:
        self._rank_cache_valid = False
        self._refresh_active_tab()

    def _refresh_active_tab(self) -> None:
        title = self.tabs.tabText(self.tabs.currentIndex())
        if self._rank_cache_valid:
            return
        if title == "Czym uderzyć":
            self._update_ranking()
        elif title == "Czym bronić":
            self._update_defenders_table()
            self._rank_cache_valid = True
        elif title == "Wytrzymałość":
            self._update_resilience_table()
            self._rank_cache_valid = True

    def _update_ranking(self) -> None:
        if not self._rs:
            return
        defenders = self._defenders()
        if not defenders:
            self.tbl_units.setRowCount(0)
            return
        sit = self._situation()
        att = self._attacker()
        opts = rank_units(self._rs, defenders, sit, self._known_techs(),
                          attacker_vet=att.vet if att else 0,
                          promotions=self.chk_promo.isChecked(), trials=4000,
                          occupiers_only=self.chk_occupiers.isChecked())
        self._rank_cache_valid = True
        self.tbl_units.setRowCount(len(opts))
        current = att.utype.name if att else ""
        for i, o in enumerate(opts):
            self._set_cell(self.tbl_units, i, 0, o.name, bold=(o.name == current))
            self._set_cell(self.tbl_units, i, 1, f"{o.p_single * 100:.0f}%")
            self._set_cell(self.tbl_units, i, 2,
                           str(o.attacks_90) if o.attacks_90 is not None else "—")
            self._set_cell(self.tbl_units, i, 3, f"{o.mean_losses:.1f}")
            self._set_cell(self.tbl_units, i, 4,
                           f"{o.shields_lost:.1f}" if o.shields_lost < 10
                           else f"{o.shields_lost:.0f}",
                           color=theme.GOOD if i == 0 else None)
            self._set_cell(self.tbl_units, i, 5,
                           str(o.invest_90) if o.invest_90 is not None else "—")
            self._set_cell(self.tbl_units, i, 6, "tak" if o.can_occupy else "nie",
                           color=None if o.can_occupy else theme.BAD)
            self._set_cell(self.tbl_units, i, 7, ", ".join(o.req_techs) or "—")

    def _clear_results(self) -> None:
        self.chart.set_data(np.ones(3), [], None)
        self.tbl_units.setRowCount(0)
        self.lbl_tips.setText("")


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("FCSiege")
    font = QFont()
    font.setPointSizeF(10)
    app.setFont(font)
    app.setStyleSheet(theme.stylesheet())
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
