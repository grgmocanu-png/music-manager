"""Liste de alegere în tabele și meniuri cu bife, ușor de folosit.

- săgeata ▾ apare când treci cu mouse-ul peste o celulă cu listă; click pe ea deschide lista direct;
- lista se deschide singură când pornește editarea (F2 sau săgeata); alegerea se salvează imediat;
- meniurile cu bife (Familie ▾, Gen ▾, coloane) rămân deschise cât bifezi; cele lungi au căutare.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractItemView, QComboBox, QLineEdit, QMenu, QStyle, QWidgetAction

ARROW_W = 18


def arrow_rect(rect: QRect) -> QRect:
    return QRect(rect.right() - ARROW_W, rect.top(), ARROW_W, rect.height())


def paint_arrow(p: QPainter, opt, index) -> None:
    """Desenează ▾ în dreapta celulei, doar la hover (și doar dacă celula e editabilă)."""
    if not (opt.state & QStyle.State_MouseOver) or not (index.flags() & Qt.ItemIsEditable):
        return
    r = arrow_rect(opt.rect)
    p.save()
    p.fillRect(r.adjusted(0, 2, -1, -2), QColor(60, 66, 74, 200))
    p.setPen(QColor("#e8e6e3"))
    p.drawText(r, Qt.AlignCenter, "▾")
    p.restore()


def arrow_click(delegate, event, option, index) -> bool:
    """Click pe ▾: pornește editarea (lista se deschide singură)."""
    if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton \
            and index.flags() & Qt.ItemIsEditable and arrow_rect(option.rect).contains(event.position().toPoint()):
        view = delegate.parent()
        if isinstance(view, QAbstractItemView):
            sel = view.selectionModel()
            if sel is not None and not sel.isSelected(index):
                view.setCurrentIndex(index)
            view.edit(index)
            return True
    return False


def setup_view(view) -> None:
    """Hover pe celule (pentru săgeată)."""
    view.setMouseTracking(True)
    view.viewport().setAttribute(Qt.WA_Hover, True)


def make_combo(parent, items, editable=False, delegate=None):
    """Lista din celulă: se deschide singură; alegerea se salvează imediat; Esc / click afară = fără schimbări."""
    cb = QComboBox(parent)
    cb.setEditable(editable)
    cb.addItems(items)
    cb.setMaxVisibleItems(20)
    if delegate is not None:
        cb.activated.connect(lambda _i=0: (delegate.commitData.emit(cb), delegate.closeEditor.emit(cb)))
    QTimer.singleShot(0, lambda: cb.showPopup() if cb.isVisible() else None)
    return cb


# --- meniuri cu bife care rămân deschise ------------------------------------------------
class StayOpenMenu(QMenu):
    """Click pe o bifă nu închide meniul (doar click în afară sau Esc)."""

    def mouseReleaseEvent(self, e):
        a = self.actionAt(e.position().toPoint())
        if a is not None and a.isCheckable() and a.isEnabled():
            a.trigger()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        a = self.activeAction()
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and a is not None and a.isCheckable():
            a.trigger()
            return
        super().keyPressEvent(e)


def add_search(menu: QMenu, placeholder="Caută…"):
    """Câmp de căutare sus, care ascunde opțiunile care nu se potrivesc (pentru liste lungi)."""
    edit = QLineEdit(menu)
    edit.setPlaceholderText(placeholder)
    edit.setClearButtonEnabled(True)
    wa = QWidgetAction(menu)
    wa.setDefaultWidget(edit)
    first = menu.actions()[0] if menu.actions() else None
    menu.insertAction(first, wa)

    def filt(text):
        t = text.lower().strip()
        for a in menu.actions():
            if a is wa or not a.isCheckable():
                continue
            a.setVisible(not t or t in a.text().lower())
    edit.textChanged.connect(filt)
    QTimer.singleShot(0, edit.setFocus)
    return edit
