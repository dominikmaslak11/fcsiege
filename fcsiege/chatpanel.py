"""Panel czatu: logowanie, rozmowa i podglad wywolan narzedzi."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QScrollArea,
                               QSizePolicy, QTextBrowser, QVBoxLayout, QWidget)

from . import theme
from .i18n import _
from .aiclient import (ChatWorker, Conversation, Credentials,
                       anthropic_cli_present, detect_credentials, forget_key,
                       has_anthropic_profile, save_key)
from .widgets import Card

CONSOLE_URL = "https://console.claude.com/settings/keys"

PROMPTY = [
    "Ile katapult potrzebuję na to miasto?",
    "Czym najtaniej obronić to miasto?",
    "Co się zmieni, jak dobuduję mury?",
]


class ChatInput(QPlainTextEdit):
    """Pole tekstowe: Enter wysyła, Shift+Enter to nowa linia."""

    submitted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText(_("Zapytaj o scenariusz… (Enter wysyła, Shift+Enter nowa linia)"))
        self.setFixedHeight(76)

    def keyPressEvent(self, ev: QKeyEvent) -> None:  # noqa: N802
        if ev.key() in (Qt.Key_Return, Qt.Key_Enter) and not (ev.modifiers() & Qt.ShiftModifier):
            self.submitted.emit()
            return
        super().keyPressEvent(ev)


class ChatPanel(QWidget):
    """Cala kolumna czatu. Rozmawia z modelem i steruje kalkulatorem."""

    def __init__(self, bridge, parent=None):
        super().__init__(parent)
        self.bridge = bridge
        self.conversation = Conversation()
        self.creds: Credentials = detect_credentials()
        self._worker: ChatWorker | None = None
        self._answer_open = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        self.card_auth = self._build_auth_card()
        lay.addWidget(self.card_auth)

        self.card_chat = Card(_("Asystent"))
        body = self.card_chat.body()

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.setObjectName("ChatView")
        self.view.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        body.addWidget(self.view, 1)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("Hint")
        self.lbl_status.setWordWrap(True)
        body.addWidget(self.lbl_status)

        self.chips = QWidget()
        cl = QHBoxLayout(self.chips)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        for text in PROMPTY:
            b = QPushButton(text)
            b.setObjectName("Suggestion")
            b.clicked.connect(lambda _=False, t=text: self._send(t))
            cl.addWidget(b)
        cl.addStretch(1)
        body.addWidget(self.chips)

        self.input = ChatInput()
        self.input.submitted.connect(self._on_submit)
        body.addWidget(self.input)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_send = QPushButton(_("Wyślij"))
        self.btn_send.setObjectName("Primary")
        self.btn_send.clicked.connect(self._on_submit)
        self.btn_stop = QPushButton(_("Przerwij"))
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_stop.setVisible(False)
        self.btn_clear = QPushButton(_("Wyczyść"))
        self.btn_clear.clicked.connect(self._on_clear)
        row.addWidget(self.btn_send)
        row.addWidget(self.btn_stop)
        row.addStretch(1)
        row.addWidget(self.btn_clear)
        body.addLayout(row)

        lay.addWidget(self.card_chat, 1)

        self._refresh_auth()
        self._greet()

    # ---------------------------------------------------------- logowanie

    def _build_auth_card(self) -> Card:
        card = Card(_("Połączenie z Claude"))
        b = card.body()

        self.lbl_auth = QLabel("")
        self.lbl_auth.setObjectName("Hint")
        self.lbl_auth.setWordWrap(True)
        self.lbl_auth.setOpenExternalLinks(True)
        self.lbl_auth.setTextFormat(Qt.RichText)
        b.addWidget(self.lbl_auth)

        self.wrap_key = QWidget()
        kl = QVBoxLayout(self.wrap_key)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.setSpacing(6)
        self.edit_key = QLineEdit()
        self.edit_key.setPlaceholderText("sk-ant-…")
        self.edit_key.setEchoMode(QLineEdit.Password)
        self.edit_key.returnPressed.connect(self._on_save_key)
        kl.addWidget(self.edit_key)
        krow = QHBoxLayout()
        krow.setSpacing(8)
        self.btn_save_key = QPushButton(_("Zapisz klucz"))
        self.btn_save_key.setObjectName("Primary")
        self.btn_save_key.clicked.connect(self._on_save_key)
        krow.addWidget(self.btn_save_key)
        krow.addStretch(1)
        kl.addLayout(krow)
        b.addWidget(self.wrap_key)

        self.btn_forget = QPushButton(_("Odłącz klucz"))
        self.btn_forget.clicked.connect(self._on_forget)
        b.addWidget(self.btn_forget)
        return card

    def _refresh_auth(self) -> None:
        self.creds = detect_credentials()
        connected = self.creds.ok
        self.wrap_key.setVisible(not connected)
        self.btn_forget.setVisible(self.creds.source == "plik")
        self.input.setEnabled(connected)
        self.btn_send.setEnabled(connected)
        self.chips.setVisible(connected)

        if connected:
            self.lbl_auth.setText(
                f"<span style='color:{theme.GOOD}'>●</span> Połączono — "
                f"źródło poświadczeń: {html.escape(self.creds.detail)}."
                f"<br>Model: <b>claude-opus-5</b>. Rozmowa i ustawienia scenariusza "
                f"są wysyłane do API Anthropic.")
            return

        extra = ""
        if has_anthropic_profile():
            extra = "<br>Wykryto profil OAuth, ale nie udało się go odczytać."
        elif anthropic_cli_present():
            extra = ("<br>Masz CLI Anthropica — zamiast klucza możesz zalogować się "
                     "przeglądarką: <code>ant auth login</code>, a potem uruchomić "
                     "aplikację ponownie.")
        else:
            extra = ("<br>Uwaga: polecenie <code>ant</code> w tym systemie to Apache Ant, "
                     "nie CLI Anthropica — logowanie przeglądarką wymagałoby najpierw "
                     "instalacji CLI Anthropica.")
        self.lbl_auth.setText(
            f"<span style='color:{theme.WARN}'>●</span> Brak poświadczeń. "
            f"Wklej klucz API z <a href='{CONSOLE_URL}'>console.claude.com</a> — "
            f"zapiszę go w <code>~/.config/fcsiege/credentials.json</code> "
            f"z prawami tylko dla Ciebie (0600).{extra}")

    def _on_save_key(self) -> None:
        key = self.edit_key.text().strip()
        if not key:
            return
        save_key(key)
        self.edit_key.clear()
        self._refresh_auth()
        self._system_line(_("Klucz zapisany. Możesz zadawać pytania."))

    def _on_forget(self) -> None:
        forget_key()
        self._refresh_auth()
        self._system_line("Klucz usunięty z dysku.")

    # ------------------------------------------------------------ rozmowa

    def _greet(self) -> None:
        self._system_line(
            "Opisz sytuację z gry, a ustawię scenariusz i policzę. "
            "Zmiany zobaczysz od razu w panelach po lewej.")

    def _append_html(self, fragment: str) -> None:
        self.view.append(fragment)
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _system_line(self, text: str) -> None:
        self._append_html(
            f"<div style='color:{theme.TEXT_FAINT};margin:6px 0'>{html.escape(text)}</div>")

    def _on_submit(self) -> None:
        text = self.input.toPlainText().strip()
        if text:
            self.input.clear()
            self._send(text)

    def _send(self, text: str) -> None:
        if self._worker is not None or not self.creds.ok:
            return
        self._append_html(
            f"<div style='margin:10px 0 4px 0'><b style='color:{theme.ACCENT}'>Ty</b><br>"
            f"{html.escape(text).replace(chr(10), '<br>')}</div>")

        note = self.bridge.ai_context_note()
        self._worker = ChatWorker(self.creds, self.conversation, text, note, self)
        self._worker.tool_requested.connect(self.bridge.ai_run_tool)
        self._worker.delta.connect(self._on_delta)
        self._worker.thinking.connect(self._on_thinking)
        self._worker.tool_started.connect(self._on_tool_started)
        self._worker.finished_ok.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._cleanup)

        self._answer_open = False
        self.btn_send.setEnabled(False)
        self.btn_stop.setVisible(True)
        self.lbl_status.setText(_("Myślę…"))
        self._worker.start()

    def _open_answer(self) -> None:
        if not self._answer_open:
            self._append_html(
                f"<div style='margin:10px 0 2px 0'><b style='color:{theme.GOOD}'>"
                f"Claude</b></div>")
            self._answer_open = True

    def _on_delta(self, text: str) -> None:
        self._open_answer()
        self.lbl_status.setText("")
        cursor = self.view.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _on_thinking(self, _text: str) -> None:
        if not self._answer_open:
            self.lbl_status.setText(_("Myślę…"))

    def _on_tool_started(self, name: str, args: str) -> None:
        short = args if len(args) <= 90 else args[:87] + "…"
        self.lbl_status.setText(f"⚙ {name} {short}")
        self._append_html(
            f"<div style='color:{theme.TEXT_FAINT};font-size:11px;margin:2px 0'>"
            f"⚙ {html.escape(name)} <span style='opacity:.7'>{html.escape(short)}</span></div>")
        self._answer_open = False

    def _on_done(self) -> None:
        self.lbl_status.setText("")

    def _on_failed(self, message: str) -> None:
        self._append_html(
            f"<div style='color:{theme.BAD};margin:6px 0'>{html.escape(message)}</div>")
        self.lbl_status.setText("")

    def _cleanup(self) -> None:
        self._worker = None
        self.btn_send.setEnabled(self.creds.ok)
        self.btn_stop.setVisible(False)
        self._append_html("")

    def _on_stop(self) -> None:
        if self._worker is not None:
            self._worker.request_stop()
            self.lbl_status.setText(_("Przerywam…"))

    def _on_clear(self) -> None:
        if self._worker is not None:
            return
        self.conversation.clear()
        self.view.clear()
        self._greet()

    def shutdown(self) -> None:
        """Zatrzymuje watek przy zamykaniu okna."""
        if self._worker is not None:
            self._worker.request_stop()
            self._worker.wait(3000)
