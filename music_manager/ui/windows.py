"""Ferestre de lucru separate (Taguri online, Dubluri, Redenumire, Curăță nume).

- fiecare e o fereastră de sine stătătoare: buton propriu în bara de task-uri,
  minimize/maximize, Alt+Tab între ea și Manager;
- butonul „— În Activități” o ascunde; o redeschizi din Activități, cu tot progresul.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QPushButton, QWidget

_open: list[QWidget] = []


class _Hub(QObject):
    changed = Signal()


hub = _Hub()


def windows() -> list[QWidget]:
    out = []
    for w in list(_open):
        try:
            w.isVisible()
        except RuntimeError:        # obiectul Qt a fost șters
            _open.remove(w)
            continue
        out.append(w)
    return out


def _forget(w):
    if w in _open:
        _open.remove(w)
    hub.changed.emit()


def tool_window(dlg: QWidget, title: str | None = None) -> QWidget:
    """Face din dialog o fereastră separată și o afișează (nu blochează Manager)."""
    owner = dlg.parentWidget()
    dlg.setParent(None)
    # codul dialogurilor folosește self.parent() pentru activitățile din fundal: îl păstrăm
    dlg.parent = lambda: owner
    dlg.setWindowFlags(Qt.Window | Qt.WindowMinimizeButtonHint | Qt.WindowMaximizeButtonHint
                       | Qt.WindowCloseButtonHint)
    dlg.setWindowModality(Qt.NonModal)
    dlg.setAttribute(Qt.WA_DeleteOnClose)
    if title:
        dlg.setWindowTitle(title)
    if owner is not None:
        icon = owner.window().windowIcon()
        if not icon.isNull():
            dlg.setWindowIcon(icon)
    lay = dlg.layout()
    if lay is not None and lay.count():
        last = lay.itemAt(lay.count() - 1).layout()
        b = collapse_button(dlg)
        if last is not None and hasattr(last, "insertWidget"):
            last.insertWidget(0, b)      # în rândul de butoane de jos
        else:
            lay.addWidget(b, 0, Qt.AlignLeft)
    _open.append(dlg)
    dlg.destroyed.connect(lambda *_: _forget(dlg))
    if hasattr(dlg, "finished"):
        dlg.finished.connect(lambda *_: (_forget(dlg), dlg.deleteLater()))
    hub.changed.emit()
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


def collapse_button(dlg: QWidget) -> QPushButton:
    """„— În Activități”: ascunde fereastra; o găsești în lista de activități."""
    b = QPushButton("— În Activități")
    b.setToolTip("Ascunde fereastra. Lucrul continuă; o redeschizi din Activități (sus, dreapta).")
    b.clicked.connect(lambda: (dlg.hide(), hub.changed.emit()))
    return b


def reopen(w: QWidget):
    w.showNormal()
    w.raise_()
    w.activateWindow()
    hub.changed.emit()


def close_all():
    for w in windows():
        w.close()


def describe(w: QWidget) -> str:
    fn = getattr(w, "progress_text", None)
    extra = ""
    if callable(fn):
        try:
            extra = fn() or ""
        except Exception:
            extra = ""
    return w.windowTitle() + (f" — {extra}" if extra else "")
