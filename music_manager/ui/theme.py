"""Tema aplicației: întunecată (implicit, ca Traktor / Lexicon) sau deschisă."""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette

ACCENT = "#e0a458"


def apply_theme(app, name: str = "dark") -> None:
    app.setStyle("Fusion")
    if name != "dark":
        app.setPalette(app.style().standardPalette())
        app.setStyleSheet("")
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
    )
