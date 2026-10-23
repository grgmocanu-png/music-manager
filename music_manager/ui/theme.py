"""Tema aplicației: întunecată (implicit, ca Traktor / Lexicon) sau deschisă."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

ACCENT = "#e0a458"


def apply_theme(app, name: str = "dark") -> None:
    app.setStyle("Fusion")
    if name != "dark":
        app.setPalette(app.style().standardPalette())
        app.setStyleSheet(
            "QPushButton#nav { border: 0; background: transparent; padding: 7px 14px; border-radius: 6px; }"
            "QPushButton#nav:checked { background: #e0a458; color: #15171b; font-weight: 600; }"
            "QLabel#logo { font-weight: 700; font-size: 15px; } QLabel#pageTitle { font-size: 17px; font-weight: 600; }"
            "QLabel#muted, QLabel#navlabel { color: gray; }"
            "QPushButton#primary { background: #e0a458; color: #15171b; font-weight: 600; }"
            "QFrame#card { border: 1px solid #d0d0d0; border-radius: 10px; }"
            "QDialog#jobsPopup { border: 1px solid #b0b0b0; }")
        return
    p = QPalette()
    c = {
        "window": "#1c1f24", "base": "#15171b", "alt": "#191b1f", "text": "#e8e6e3", "muted": "#9aa0a8",
        "button": "#262a30", "line": "#2f343b", "tip": "#23272d",
    }
    p.setColor(QPalette.Window, QColor(c["window"]))
    p.setColor(QPalette.WindowText, QColor(c["text"]))
    p.setColor(QPalette.Base, QColor(c["base"]))
    p.setColor(QPalette.AlternateBase, QColor(c["alt"]))
    p.setColor(QPalette.ToolTipBase, QColor(c["tip"]))
    p.setColor(QPalette.ToolTipText, QColor(c["text"]))
    p.setColor(QPalette.Text, QColor(c["text"]))
    p.setColor(QPalette.PlaceholderText, QColor(c["muted"]))
    p.setColor(QPalette.Button, QColor(c["button"]))
    p.setColor(QPalette.ButtonText, QColor(c["text"]))
    p.setColor(QPalette.BrightText, QColor("#ffffff"))
    p.setColor(QPalette.Link, QColor(ACCENT))
    p.setColor(QPalette.Highlight, QColor("#3a3328"))
    p.setColor(QPalette.HighlightedText, QColor("#f6e3c6"))
    p.setColor(QPalette.Mid, QColor(c["line"]))
    p.setColor(QPalette.Dark, QColor("#121417"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, QColor("#6c727a"))
    app.setPalette(p)
    app.setStyleSheet(
        "QToolTip { color: #e8e6e3; background: #23272d; border: 1px solid #3a4048; padding: 4px; }"
        "QHeaderView::section { background: #1c1f24; color: #9aa0a8; border: 0; border-bottom: 1px solid #2c3138;"
        " padding: 5px 6px; }"
        "QTableView, QTreeView, QListView { gridline-color: #23272d; border: 1px solid #2c3138; }"
        "QPushButton { padding: 5px 12px; border: 1px solid #343a42; border-radius: 5px; background: #262a30; }"
        "QPushButton:hover { border-color: #e0a458; }"
        "QPushButton:disabled { color: #6c727a; }"
        "QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateEdit { padding: 4px 6px; border: 1px solid #343a42;"
        " border-radius: 5px; background: #1f2227; }"
        "QTabBar::tab:selected { color: #e0a458; }"
        "QProgressBar { border: 1px solid #343a42; border-radius: 4px; text-align: center; background: #1f2227; }"
        "QProgressBar::chunk { background: #5fb3a6; border-radius: 3px; }"
        "QGroupBox { border: 1px solid #2c3138; border-radius: 8px; margin-top: 14px; padding-top: 8px; }"
        "QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #c9ccd1; }"
        # bara de sus
        "QWidget#topbar { background: #1c1f24; border-bottom: 1px solid #2c3138; }"
        "QLabel#logo { font-weight: 700; font-size: 15px; color: #f1efec; padding-left: 4px; }"
        "QLabel#navlabel { color: #8f959d; font-size: 11px; letter-spacing: 1px; padding: 0 6px; }"
        "QPushButton#nav { border: 0; background: transparent; color: #b8bcc2; padding: 7px 14px; border-radius: 6px; }"
        "QPushButton#nav:hover { background: #262a30; color: #f1efec; }"
        "QPushButton#nav:checked { background: #e0a458; color: #15171b; font-weight: 600; }"
        "QFrame#navsep { color: #2f343b; }"
        "QPushButton#jobs { border: 1px solid #2f343b; border-radius: 14px; padding: 5px 12px; background: #262a30;"
        " color: #c9ccd1; }"
        # titluri, texte, butoane speciale
        "QLabel#pageTitle { font-size: 17px; font-weight: 600; color: #f1efec; }"
        "QLabel#muted { color: #9aa0a8; }"
        "QPushButton#primary { background: #e0a458; color: #15171b; border: 0; font-weight: 600; }"
        "QPushButton#primary:hover { background: #f0bd7a; }"
        "QPushButton#danger { color: #d9826b; border-color: #4a3530; }"
        "QPushButton#chip { border-radius: 13px; padding: 5px 12px; }"
        "QPushButton#chip:checked { background: #2f4a44; color: #9fe0d2; border-color: #2f4a44; }"
        "QToolButton { padding: 5px 12px; border: 1px solid #343a42; border-radius: 5px; background: #262a30; }"
        "QToolButton::menu-indicator { image: none; }"
        # bara din stânga (playlisturi / surse)
        "QTreeWidget#sidebar { background: #1a1d21; border: 0; border-right: 1px solid #2c3138; padding: 8px 4px; }"
        "QTreeWidget#sidebar::item { padding: 5px 6px; border-radius: 5px; }"
        "QTreeWidget#sidebar::item:selected { background: #2a2f36; color: #f1efec; }"
        "QTreeWidget#sidebar::item:hover { background: #22262c; }"
        # carduri (Curățenie) și secțiuni (Setări)
        "QFrame#card { background: #1c1f24; border: 1px solid #2c3138; border-radius: 10px; }"
        "QDialog#jobsPopup { background: #1c1f24; border: 1px solid #3a4048; }"
        "QListWidget#sections { background: #1a1d21; border: 0; border-right: 1px solid #2c3138; padding: 10px 6px; }"
        "QListWidget#sections::item { padding: 9px 12px; border-radius: 6px; color: #c9ccd1; }"
        "QListWidget#sections::item:selected { background: #2a2f36; color: #f1efec; font-weight: 600; }"
        "QTableView { selection-background-color: #3a3328; }"
        "QMenu { background: #23272d; border: 1px solid #3a4048; padding: 6px; }"
        "QMenu::item { padding: 6px 18px; border-radius: 4px; }"
        "QMenu::item:selected { background: #2f343b; }"
        "QMenu::item:disabled { color: #8f959d; }"
        "QSplitter::handle { background: #2c3138; width: 1px; }"
    )
