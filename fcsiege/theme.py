"""Paleta barw i arkusz stylow aplikacji."""

from __future__ import annotations

BG = "#0E1320"
SURFACE = "#161D2D"
SURFACE_2 = "#1D2740"
SURFACE_3 = "#243052"
BORDER = "#2A3654"
BORDER_SOFT = "#212B44"

TEXT = "#E8ECF6"
TEXT_DIM = "#9AA7C4"
TEXT_FAINT = "#6C7A9C"

ATTACK = "#FF7A4D"
ATTACK_DIM = "#7A3B26"
DEFEND = "#4DA3FF"
DEFEND_DIM = "#22456F"
GOOD = "#3DD68C"
WARN = "#FFC24D"
BAD = "#FF5D6C"
ACCENT = "#8B7BFF"

GRID = "#243052"

FONT_STACK = ('"Inter", "Segoe UI", "Cantarell", "Noto Sans", '
              '"DejaVu Sans", sans-serif')
MONO_STACK = '"JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace'


def stylesheet() -> str:
    return f"""
* {{
    font-family: {FONT_STACK};
    color: {TEXT};
}}

QWidget#Root {{
    background: {BG};
}}

/* ---------------------------------------------------------------- karty */
QFrame#Card {{
    background: {SURFACE};
    border: 1px solid {BORDER_SOFT};
    border-radius: 14px;
}}
QFrame#CardAttack {{
    background: {SURFACE};
    border: 1px solid {BORDER_SOFT};
    border-top: 3px solid {ATTACK};
    border-radius: 14px;
}}
QFrame#CardDefend {{
    background: {SURFACE};
    border: 1px solid {BORDER_SOFT};
    border-top: 3px solid {DEFEND};
    border-radius: 14px;
}}
QFrame#Inner {{
    background: {SURFACE_2};
    border: 1px solid {BORDER_SOFT};
    border-radius: 10px;
}}

QLabel#CardTitle {{
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.4px;
    color: {TEXT_FAINT};
}}
QLabel#CardTitleAttack {{
    font-size: 11px; font-weight: 700; letter-spacing: 1.4px; color: {ATTACK};
}}
QLabel#CardTitleDefend {{
    font-size: 11px; font-weight: 700; letter-spacing: 1.4px; color: {DEFEND};
}}
QLabel#FieldLabel {{
    font-size: 11px;
    color: {TEXT_DIM};
    font-weight: 600;
}}
QLabel#Hint {{
    font-size: 11px;
    color: {TEXT_FAINT};
}}
QLabel#Hint a, QTextBrowser a {{
    color: {DEFEND};
    text-decoration: none;
}}
QLabel#AppTitle {{
    font-size: 19px;
    font-weight: 800;
    letter-spacing: -0.3px;
}}
QLabel#AppSub {{
    font-size: 11px;
    color: {TEXT_FAINT};
}}
QLabel#SectionHead {{
    font-size: 12px;
    font-weight: 700;
    color: {TEXT_DIM};
    letter-spacing: 0.6px;
}}

/* ------------------------------------------------------------ formularze */
QComboBox, QSpinBox, QLineEdit {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 10px;
    min-height: 18px;
    selection-background-color: {DEFEND_DIM};
}}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{
    border-color: {SURFACE_3};
}}
QComboBox:focus, QSpinBox:focus, QLineEdit:focus {{
    border-color: {ACCENT};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {TEXT_DIM};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 4px;
    outline: none;
    selection-background-color: {SURFACE_3};
}}
QSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    background: {SURFACE_3};
    border: none;
    border-top-right-radius: 7px;
    width: 18px;
    margin: 2px 2px 0 0;
}}
QSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    background: {SURFACE_3};
    border: none;
    border-bottom-right-radius: 7px;
    width: 18px;
    margin: 0 2px 2px 0;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {BORDER};
}}
QSpinBox::up-arrow {{
    image: none;
    width: 0; height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid {TEXT_DIM};
}}
QSpinBox::down-arrow {{
    image: none;
    width: 0; height: 0;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {TEXT_DIM};
}}

QCheckBox {{
    spacing: 8px;
    font-size: 12px;
    color: {TEXT};
    padding: 2px 0;
}}
QCheckBox::indicator {{
    width: 16px; height: 16px;
    border: 1px solid {BORDER};
    border-radius: 5px;
    background: {SURFACE_2};
}}
QCheckBox::indicator:hover {{
    border-color: {ACCENT};
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QCheckBox:disabled {{
    color: {TEXT_FAINT};
}}

QSlider::groove:horizontal {{
    height: 4px;
    background: {SURFACE_3};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {TEXT};
    width: 14px;
    height: 14px;
    margin: -6px 0;
    border-radius: 7px;
}}

QPushButton {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 600;
    font-size: 12px;
}}
QPushButton:hover {{
    background: {SURFACE_3};
}}
QPushButton:pressed {{
    background: {BORDER};
}}
QWidget#ModeSwitch {{
    background: {SURFACE};
    border: 1px solid {BORDER_SOFT};
    border-radius: 11px;
}}
QPushButton#ModeButton {{
    background: transparent;
    border: none;
    border-radius: 8px;
    padding: 7px 12px;
    color: {TEXT_FAINT};
    font-weight: 700;
    font-size: 12px;
}}
QPushButton#ModeButton:hover {{
    color: {TEXT_DIM};
}}
QPushButton#ModeButton:checked {{
    background: {SURFACE_3};
    color: {TEXT};
}}

QPushButton#Primary {{
    background: {ACCENT};
    border: none;
    color: #FFFFFF;
}}
QPushButton#Primary:hover {{
    background: #9C8EFF;
}}

/* ---------------------------------------------------------------- zakladki */
QTabWidget::pane {{
    border: none;
    background: transparent;
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_FAINT};
    padding: 8px 14px;
    margin-right: 4px;
    border-radius: 8px;
    font-size: 12px;
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {TEXT_DIM};
}}
QTabBar::tab:selected {{
    background: {SURFACE_2};
    color: {TEXT};
}}

/* ---------------------------------------------------------------- tabela */
QTableWidget {{
    background: transparent;
    border: none;
    gridline-color: transparent;
    outline: none;
}}
QTableWidget::item {{
    padding: 7px 8px;
    border-bottom: 1px solid {BORDER_SOFT};
}}
QTableWidget::item:selected {{
    background: {SURFACE_3};
}}
QHeaderView::section {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 8px;
    color: {TEXT_FAINT};
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.5px;
}}
QTableCornerButton::section {{
    background: transparent;
    border: none;
}}

/* ---------------------------------------------------------------- suwaki */
QScrollArea, QScrollArea > QWidget > QWidget {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {SURFACE_3};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {BORDER};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0; width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {SURFACE_3};
    border-radius: 5px;
    min-width: 30px;
}}

QTextBrowser#ChatView {{
    background: {SURFACE_2};
    border: 1px solid {BORDER_SOFT};
    border-radius: 10px;
    padding: 10px 12px;
    font-size: 12.5px;
}}
QPlainTextEdit {{
    background: {SURFACE_2};
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 7px 10px;
    font-size: 12.5px;
    selection-background-color: {DEFEND_DIM};
}}
QPlainTextEdit:focus {{
    border-color: {ACCENT};
}}
QPushButton#Suggestion {{
    background: {SURFACE_2};
    border: 1px solid {BORDER_SOFT};
    border-radius: 12px;
    padding: 5px 10px;
    font-size: 11px;
    font-weight: 500;
    color: {TEXT_DIM};
}}
QPushButton#Suggestion:hover {{
    background: {SURFACE_3};
    color: {TEXT};
}}

QToolTip {{
    background: {SURFACE_3};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 8px;
}}

QSplitter::handle {{
    background: transparent;
}}
"""
