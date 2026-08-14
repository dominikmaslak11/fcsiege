#!/usr/bin/env python3
"""Generuje zrzuty ekranu do dokumentacji.

Uruchom: python3 tools/screenshots.py [katalog]
Domyslnie zapisuje do docs/. Dziala bez ekranu (platforma offscreen).
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from fcsiege import theme  # noqa: E402
from fcsiege.app import MODE_ATTACK, MODE_DEFENSE, MainWindow  # noqa: E402

SIZE = (1480, 940)


def pick(combo, value):
    i = combo.findData(value)
    if i >= 0:
        combo.setCurrentIndex(i)


def tab(win, title):
    for i in range(win.tabs.count()):
        if win.tabs.tabText(i) == title:
            win.tabs.setCurrentIndex(i)
            return True
    return False


def shot(app, win, path, title):
    if not tab(win, title):
        print(f"  pomijam (brak zakladki {title})")
        return
    app.processEvents()
    win.grab().save(path)
    print(f"  {os.path.basename(path)}")


def setup_attack(win):
    """Szturm: katapulty na 5 wojownikow w miescie na wzgorzu z murami."""
    win._set_mode(MODE_ATTACK)
    pick(win.cmb_att_unit, "Catapult")
    pick(win.cmb_def_terrain, "Hills")
    pick(win.cmb_att_terrain, "Forest")
    win.chk_city.setChecked(True)
    for name, cb in win.chk_buildings.items():
        cb.setChecked(name == "City Walls")
    win.def_rows[0].unit.setCurrentIndex(
        max(0, win.def_rows[0].unit.findData("Warriors")))
    win.def_rows[0].count.setValue(5)
    win.def_rows[1].count.setValue(0)
    win.def_rows[2].count.setValue(0)
    win.spn_planned.setValue(8)
    win._recalculate()


def setup_defense(win):
    """Obrona: 5 wojownikow-weteranow naciera z gory na moje miasto."""
    win._set_mode(MODE_DEFENSE)
    pick(win.cmb_att_unit, "Phalanx")
    pick(win.cmb_def_terrain, "Hills")
    pick(win.cmb_att_terrain, "Mountains")
    win.chk_city.setChecked(True)
    for name, cb in win.chk_buildings.items():
        cb.setChecked(name in ("City Walls", "Barracks"))
    row = win.def_rows[0]
    pick(row.unit, "Warriors")
    row.count.setValue(5)
    row.vet.setCurrentIndex(min(1, row.vet.count() - 1))
    win.def_rows[1].count.setValue(0)
    win.def_rows[2].count.setValue(0)
    win.spn_planned.setValue(2)
    win._recalculate()


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")
    os.makedirs(out, exist_ok=True)

    app = QApplication(sys.argv)
    f = QFont()
    f.setPointSizeF(10)
    app.setFont(f)
    app.setStyleSheet(theme.stylesheet())
    win = MainWindow()
    win.resize(*SIZE)
    win.show()
    app.processEvents()

    print("Tryb szturmu:")
    setup_attack(win)
    shot(app, win, os.path.join(out, "01-szturm.png"), "Szansa zdobycia")
    shot(app, win, os.path.join(out, "02-czym-uderzyc.png"), "Czym uderzyć")
    shot(app, win, os.path.join(out, "03-rozbicie-sil.png"), "Rozbicie sił")
    shot(app, win, os.path.join(out, "04-skad-atakowac.png"), "Skąd atakować")

    print("Tryb obrony:")
    setup_defense(win)
    shot(app, win, os.path.join(out, "05-obrona.png"), "Szansa utrzymania")
    shot(app, win, os.path.join(out, "06-czym-bronic.png"), "Czym bronić")
    shot(app, win, os.path.join(out, "07-wytrzymalosc.png"), "Wytrzymałość")
    shot(app, win, os.path.join(out, "08-wskazowki.png"), "Wskazówki")

    print("Asystent:")
    win.btn_chat.setChecked(True)
    app.processEvents()
    win.resize(1780, 960)
    app.processEvents()
    shot(app, win, os.path.join(out, "09-asystent.png"), "Szansa utrzymania")
    win.btn_chat.setChecked(False)
    win.resize(*SIZE)

    print(f"\nZapisano w {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
