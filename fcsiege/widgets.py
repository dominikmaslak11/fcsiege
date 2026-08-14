"""Wlasnorecznie rysowane elementy interfejsu."""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPolygonF)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import theme


def _c(name: str) -> QColor:
    return QColor(name)


class Card(QFrame):
    """Zaokraglona karta z tytulem."""

    def __init__(self, title: str = "", variant: str = "", parent=None):
        super().__init__(parent)
        self._variant = variant
        self.setObjectName({"attack": "CardAttack",
                            "defend": "CardDefend"}.get(variant, "Card"))
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(18, 16, 18, 16)
        self._lay.setSpacing(10)
        self._title: QLabel | None = None
        if title:
            self._title = QLabel(title.upper())
            self._title.setObjectName(
                {"attack": "CardTitleAttack",
                 "defend": "CardTitleDefend"}.get(variant, "CardTitle"))
            self._lay.addWidget(self._title)

    def set_title(self, title: str, variant: str | None = None) -> None:
        """Zmienia napis (i ewentualnie kolor) naglowka karty."""
        if self._title is not None:
            self._title.setText(title.upper())
        if variant is not None and variant != self._variant:
            self._variant = variant
            self.setObjectName({"attack": "CardAttack",
                                "defend": "CardDefend"}.get(variant, "Card"))
            if self._title is not None:
                self._title.setObjectName(
                    {"attack": "CardTitleAttack",
                     "defend": "CardTitleDefend"}.get(variant, "CardTitle"))
                self._title.style().unpolish(self._title)
                self._title.style().polish(self._title)
            self.style().unpolish(self)
            self.style().polish(self)

    def body(self) -> QVBoxLayout:
        return self._lay


class AnswerCard(QFrame):
    """Duza karta z glowna odpowiedzia."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setMinimumHeight(132)
        self._headline = "—"
        self._sub = ""
        self._detail = ""
        self._tone = theme.GOOD

    def set_answer(self, headline: str, sub: str, detail: str, tone: str) -> None:
        self._headline, self._sub, self._detail, self._tone = headline, sub, detail, tone
        self.update()

    def paintEvent(self, ev):  # noqa: N802 - API Qt
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        grad.setColorAt(0.0, _c(theme.SURFACE_2))
        grad.setColorAt(1.0, _c(theme.SURFACE))
        p.fillPath(path, QBrush(grad))

        # akcentowy pasek po lewej
        p.save()
        p.setClipPath(path)
        accent = QRectF(r.left(), r.top(), 4, r.height())
        p.fillRect(accent, _c(self._tone))
        glow = QLinearGradient(r.left(), 0, r.left() + 260, 0)
        gc = _c(self._tone)
        gc.setAlpha(38)
        glow.setColorAt(0.0, gc)
        gc2 = _c(self._tone)
        gc2.setAlpha(0)
        glow.setColorAt(1.0, gc2)
        p.fillRect(r, QBrush(glow))
        p.restore()

        p.setPen(QPen(_c(theme.BORDER_SOFT), 1))
        p.drawPath(path)

        x = r.left() + 24
        f = QFont(self.font())
        f.setPointSizeF(11.5)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
        p.setFont(f)
        p.setPen(_c(theme.TEXT_FAINT))
        p.drawText(QRectF(x, r.top() + 16, r.width() - 40, 18),
                   Qt.AlignLeft | Qt.AlignVCenter, "ODPOWIEDŹ")

        f2 = QFont(self.font())
        f2.setPointSizeF(25)
        f2.setBold(True)
        p.setFont(f2)
        p.setPen(_c(theme.TEXT))
        p.drawText(QRectF(x, r.top() + 38, r.width() - 40, 40),
                   Qt.AlignLeft | Qt.AlignVCenter, self._headline)

        f3 = QFont(self.font())
        f3.setPointSizeF(11.5)
        p.setFont(f3)
        p.setPen(_c(theme.TEXT_DIM))
        p.drawText(QRectF(x, r.top() + 80, r.width() - 40, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, self._sub)
        p.setPen(_c(theme.TEXT_FAINT))
        p.drawText(QRectF(x, r.top() + 101, r.width() - 40, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, self._detail)
        p.end()


class StatTile(QFrame):
    """Maly kafelek z liczba i podpisem."""

    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Inner")
        self.setMinimumHeight(74)
        self._label = label
        self._value = "—"
        self._note = ""
        self._tone = theme.TEXT

    def set_label(self, label: str) -> None:
        self._label = label
        self.update()

    def set_value(self, value: str, note: str = "", tone: str | None = None) -> None:
        self._value, self._note = value, note
        self._tone = tone or theme.TEXT
        self.update()

    def paintEvent(self, ev):  # noqa: N802
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(13, 10, -13, -10)

        f = QFont(self.font())
        f.setPointSizeF(9.5)
        f.setBold(True)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 0.8)
        p.setFont(f)
        p.setPen(_c(theme.TEXT_FAINT))
        p.drawText(QRectF(r.left(), r.top(), r.width(), 14),
                   Qt.AlignLeft | Qt.AlignVCenter, self._label.upper())

        f2 = QFont(self.font())
        f2.setPointSizeF(19)
        f2.setBold(True)
        p.setFont(f2)
        p.setPen(_c(self._tone))
        p.drawText(QRectF(r.left(), r.top() + 16, r.width(), 28),
                   Qt.AlignLeft | Qt.AlignVCenter, self._value)

        if self._note:
            f3 = QFont(self.font())
            f3.setPointSizeF(9.5)
            p.setFont(f3)
            p.setPen(_c(theme.TEXT_FAINT))
            p.drawText(QRectF(r.left(), r.top() + 44, r.width(), 14),
                       Qt.AlignLeft | Qt.AlignVCenter, self._note)
        p.end()


class PowerScale(QWidget):
    """Poziomy pasek porownujacy sile ataku i obrony."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(96)
        self._a = 0.0
        self._d = 0.0
        self._pa = ""
        self._pd = ""

    def set_values(self, attack: float, defense: float,
                   name_a: str, name_d: str) -> None:
        self._a, self._d = attack, defense
        self._pa, self._pd = name_a, name_d
        self.update()

    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        total = self._a + self._d
        if total <= 0:
            p.end()
            return

        share = self._a / total
        bar_y = h - 46
        bar_h = 22
        pad = 2

        # tlo paska
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(0, bar_y, w, bar_h), 11, 11)
        p.fillPath(bg, _c(theme.SURFACE_2))

        p.save()
        p.setClipPath(bg)
        split = max(0.0, min(1.0, share)) * w
        ga = QLinearGradient(0, 0, split, 0)
        ga.setColorAt(0.0, _c(theme.ATTACK).darker(130))
        ga.setColorAt(1.0, _c(theme.ATTACK))
        p.fillRect(QRectF(0, bar_y, split, bar_h), QBrush(ga))
        gd = QLinearGradient(split, 0, w, 0)
        gd.setColorAt(0.0, _c(theme.DEFEND))
        gd.setColorAt(1.0, _c(theme.DEFEND).darker(130))
        p.fillRect(QRectF(split + pad, bar_y, w - split - pad, bar_h), QBrush(gd))
        p.restore()

        # etykiety nad paskiem
        f = QFont(self.font())
        f.setPointSizeF(10)
        f.setBold(True)
        p.setFont(f)
        p.setPen(_c(theme.ATTACK))
        p.drawText(QRectF(0, bar_y - 44, w / 2, 16), Qt.AlignLeft | Qt.AlignVCenter,
                   self._pa)
        p.setPen(_c(theme.DEFEND))
        p.drawText(QRectF(w / 2, bar_y - 44, w / 2, 16), Qt.AlignRight | Qt.AlignVCenter,
                   self._pd)

        f2 = QFont(self.font())
        f2.setPointSizeF(17)
        f2.setBold(True)
        p.setFont(f2)
        p.setPen(_c(theme.TEXT))
        p.drawText(QRectF(0, bar_y - 28, w / 2, 24), Qt.AlignLeft | Qt.AlignVCenter,
                   f"{self._a / 10:.1f}")
        p.drawText(QRectF(w / 2, bar_y - 28, w / 2, 24), Qt.AlignRight | Qt.AlignVCenter,
                   f"{self._d / 10:.1f}")

        # podpis pod paskiem
        f3 = QFont(self.font())
        f3.setPointSizeF(10)
        p.setFont(f3)
        p.setPen(_c(theme.TEXT_FAINT))
        p.drawText(QRectF(0, bar_y + bar_h + 4, w, 16), Qt.AlignCenter,
                   f"szansa trafienia w rundzie: {share * 100:.1f}%  "
                   f"·  obrona {100 - share * 100:.1f}%")
        p.end()


class ProbabilityChart(QWidget):
    """Krzywa: ile jednostek kontra szansa zdobycia miasta."""

    hovered = Signal(int, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(210)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self._cdf: np.ndarray | None = None
        self._max_x = 20
        self._marks: list[tuple[int, float, str]] = []
        self._hover_x: int | None = None
        self._planned: int | None = None
        self._axis_label = "liczba atakujących jednostek"

    def set_axis_label(self, text: str) -> None:
        self._axis_label = text
        self.update()

    def set_data(self, cdf: np.ndarray, marks: list[tuple[int, float, str]],
                 planned: int | None = None) -> None:
        self._cdf = cdf
        self._marks = marks
        self._planned = planned
        if cdf is not None and len(cdf):
            idx = np.searchsorted(cdf, 0.995, side="left")
            self._max_x = int(max(6, min(len(cdf) - 1, idx + 2)))
            if planned:
                self._max_x = max(self._max_x, planned + 1)
        self.update()

    def _geom(self):
        left, right, top, bottom = 44, 14, 16, 34
        w = self.width() - left - right
        h = self.height() - top - bottom
        return left, top, max(1, w), max(1, h)

    def mouseMoveEvent(self, ev):  # noqa: N802
        left, top, w, h = self._geom()
        if self._cdf is None:
            return
        rel = (ev.position().x() - left) / w
        x = int(round(rel * self._max_x))
        self._hover_x = max(0, min(self._max_x, x))
        self.update()

    def leaveEvent(self, ev):  # noqa: N802
        self._hover_x = None
        self.update()

    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        left, top, w, h = self._geom()
        plot = QRectF(left, top, w, h)

        f = QFont(self.font())
        f.setPointSizeF(9)
        p.setFont(f)

        # siatka pozioma
        for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
            y = plot.bottom() - frac * h
            pen = QPen(_c(theme.GRID), 1)
            if frac in (0.0, 1.0):
                pen.setColor(_c(theme.BORDER))
            p.setPen(pen)
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(_c(theme.TEXT_FAINT))
            p.drawText(QRectF(0, y - 8, left - 8, 16),
                       Qt.AlignRight | Qt.AlignVCenter, f"{int(frac * 100)}%")

        if self._cdf is None or not len(self._cdf):
            p.end()
            return

        def px(i: float) -> float:
            return plot.left() + (i / self._max_x) * w

        def py(v: float) -> float:
            return plot.bottom() - max(0.0, min(1.0, v)) * h

        # os X
        step = max(1, self._max_x // 10)
        p.setPen(_c(theme.TEXT_FAINT))
        for i in range(0, self._max_x + 1, step):
            p.drawText(QRectF(px(i) - 14, plot.bottom() + 6, 28, 16),
                       Qt.AlignCenter, str(i))
        p.drawText(QRectF(plot.left(), plot.bottom() + 20, w, 14),
                   Qt.AlignCenter, self._axis_label)

        # krzywa schodkowa + wypelnienie
        pts: list[QPointF] = []
        for i in range(0, self._max_x + 1):
            v = float(self._cdf[i]) if i < len(self._cdf) else float(self._cdf[-1])
            if pts:
                pts.append(QPointF(px(i), pts[-1].y()))
            pts.append(QPointF(px(i), py(v)))

        fill = QPolygonF([QPointF(plot.left(), plot.bottom())] + pts
                         + [QPointF(plot.right(), plot.bottom())])
        grad = QLinearGradient(0, plot.top(), 0, plot.bottom())
        c1 = _c(theme.ACCENT)
        c1.setAlpha(110)
        c2 = _c(theme.ACCENT)
        c2.setAlpha(8)
        grad.setColorAt(0.0, c1)
        grad.setColorAt(1.0, c2)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPolygon(fill)

        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_c(theme.ACCENT), 2.2))
        p.drawPolyline(QPolygonF(pts))

        # progi pewnosci
        for x, val, label in self._marks:
            if x > self._max_x:
                continue
            pen = QPen(_c(theme.WARN), 1, Qt.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(px(x), py(val)), QPointF(px(x), plot.bottom()))
            p.setBrush(_c(theme.WARN))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(px(x), py(val)), 3.5, 3.5)
            p.setPen(_c(theme.WARN))
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(label)
            tx = min(px(x) + 6, plot.right() - tw - 2)
            p.drawText(QRectF(tx, py(val) - 18, tw + 4, 14),
                       Qt.AlignLeft | Qt.AlignVCenter, label)

        # zaplanowana liczba jednostek
        if self._planned is not None and 0 <= self._planned <= self._max_x:
            v = float(self._cdf[min(self._planned, len(self._cdf) - 1)])
            p.setPen(QPen(_c(theme.GOOD), 1.6))
            p.drawLine(QPointF(px(self._planned), plot.top()),
                       QPointF(px(self._planned), plot.bottom()))
            p.setBrush(_c(theme.GOOD))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(px(self._planned), py(v)), 4.5, 4.5)

        # kursor
        if self._hover_x is not None:
            i = self._hover_x
            v = float(self._cdf[min(i, len(self._cdf) - 1)])
            p.setPen(QPen(_c(theme.TEXT_DIM), 1, Qt.DotLine))
            p.drawLine(QPointF(px(i), plot.top()), QPointF(px(i), plot.bottom()))
            txt = f"{i} jedn. → {v * 100:.1f}%"
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(txt) + 14
            bx = min(px(i) + 8, plot.right() - tw)
            box = QRectF(bx, plot.top() + 4, tw, 22)
            bp = QPainterPath()
            bp.addRoundedRect(box, 6, 6)
            p.fillPath(bp, _c(theme.SURFACE_3))
            p.setPen(_c(theme.TEXT))
            p.drawText(box, Qt.AlignCenter, txt)
        p.end()


class ModifierBars(QWidget):
    """Lista mnoznikow obrony jako paski."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[tuple[str, float, str, list[str]]] = []
        self._base = 0.0
        self._total = 0.0
        self.setMinimumHeight(60)

    def set_rows(self, base: float, total: float,
                 rows: list[tuple[str, float, str, list[str]]]) -> None:
        self._base, self._total, self._rows = base, total, rows
        self.setMinimumHeight(34 + 26 * max(1, len(rows)))
        self.updateGeometry()
        self.update()

    def paintEvent(self, ev):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w = self.width()
        f = QFont(self.font())
        f.setPointSizeF(10)
        p.setFont(f)

        y = 4
        p.setPen(_c(theme.TEXT_DIM))
        p.drawText(QRectF(0, y, w * 0.62, 18), Qt.AlignLeft | Qt.AlignVCenter,
                   f"baza {self._base / 10:.1f}")
        p.setPen(_c(theme.TEXT))
        fb = QFont(f)
        fb.setBold(True)
        p.setFont(fb)
        p.drawText(QRectF(w * 0.62, y, w * 0.38, 18), Qt.AlignRight | Qt.AlignVCenter,
                   f"= {self._total / 10:.1f}")
        p.setFont(f)
        y += 24

        if not self._rows:
            p.setPen(_c(theme.TEXT_FAINT))
            p.drawText(QRectF(0, y, w, 18), Qt.AlignLeft | Qt.AlignVCenter,
                       "brak modyfikatorów")
            p.end()
            return

        max_factor = max((r[1] for r in self._rows), default=1.0)
        for label, factor, tone, details in self._rows:
            bar_w = (w * 0.34) * min(1.0, (factor - 1.0) / max(0.001, max_factor - 1.0)) \
                if max_factor > 1.0 else 0.0
            bar_w = max(bar_w, 3.0) if factor > 1.0 else 3.0
            rect = QRectF(w - bar_w, y + 5, bar_w, 8)
            path = QPainterPath()
            path.addRoundedRect(rect, 4, 4)
            col = _c(tone)
            if factor < 1.0:
                col = _c(theme.BAD)
            p.fillPath(path, col)

            p.setPen(_c(theme.TEXT))
            txt = f"×{factor:.2f}"
            p.drawText(QRectF(w - bar_w - 54, y, 48, 18),
                       Qt.AlignRight | Qt.AlignVCenter, txt)

            p.setPen(_c(theme.TEXT_DIM))
            elided = p.fontMetrics().elidedText(
                label, Qt.ElideRight, int(w * 0.5))
            p.drawText(QRectF(0, y, w * 0.52, 18),
                       Qt.AlignLeft | Qt.AlignVCenter, elided)
            y += 26
        p.end()


class Chip(QLabel):
    """Maly kolorowy znacznik informacyjny."""

    def __init__(self, text: str, tone: str = theme.TEXT_DIM, parent=None):
        super().__init__(text, parent)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        col = QColor(tone)
        bg = QColor(tone)
        bg.setAlpha(28)
        self.setStyleSheet(
            f"background: rgba({bg.red()},{bg.green()},{bg.blue()},{bg.alpha()});"
            f"color: {col.name()};"
            "border-radius: 6px; padding: 3px 9px;"
            "font-size: 11px; font-weight: 600;")


class FlowRow(QWidget):
    """Pojemnik na chipy, ktory sam zawija je do nowego wiersza."""

    def __init__(self, columns: int = 3, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QGridLayout
        self._columns = columns
        self._lay = QGridLayout(self)
        self._lay.setContentsMargins(0, 2, 0, 0)
        self._lay.setSpacing(5)

    def set_chips(self, items: list[tuple[str, str]]) -> None:
        while self._lay.count():
            item = self._lay.takeAt(0)
            wdg = item.widget()
            if wdg:
                wdg.setParent(None)
                wdg.deleteLater()
        for i, (text, tone) in enumerate(items):
            chip = Chip(text, tone)
            chip.setAlignment(Qt.AlignCenter)
            self._lay.addWidget(chip, i // self._columns, i % self._columns)
            # bez jawnego show() nowe chipy potrafia zostac bez ulozenia,
            # gdy podmieniamy je szybko jeden po drugim
            chip.show()
        self._lay.activate()
        self.updateGeometry()
