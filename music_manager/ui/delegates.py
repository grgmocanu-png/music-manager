"""Celule desenate ca în machetă: Family ca etichetă colorată, Key cu punct colorat, Mix ca insignă."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter
from PySide6.QtWidgets import QStyle, QStyledItemDelegate


class PillDelegate(QStyledItemDelegate):
    """kind: "pill" (Family, Folosire), "dot" (Key), "badge" (Mix)."""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind

    def paint(self, p: QPainter, opt, index):
        text = str(index.data(Qt.DisplayRole) or "")
        fg = index.data(Qt.ForegroundRole)
        color = fg.color() if isinstance(fg, QBrush) else QColor("#c9ccd1")
        if not text:
            return QStyledItemDelegate.paint(self, p, opt, index)
        p.save()
        if opt.state & QStyle.State_Selected:
            p.fillRect(opt.rect, opt.palette.highlight())
        elif index.row() % 2:
            p.fillRect(opt.rect, opt.palette.alternateBase())
        else:
            p.fillRect(opt.rect, opt.palette.base())
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(opt.rect).adjusted(6, 4, -4, -4)
        fm = opt.fontMetrics
        if self.kind == "dot":
            d = 9
            p.setPen(Qt.NoPen)
            p.setBrush(color)
            p.drawEllipse(QRectF(r.left(), r.center().y() - d / 2, d, d))
            p.setPen(QColor("#e8e6e3"))
            p.drawText(r.adjusted(d + 7, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
        else:
            w = min(r.width(), fm.horizontalAdvance(text) + 16)
            if self.kind == "badge":
                w = max(26, fm.horizontalAdvance(text) + 12)
                box = QRectF(r.center().x() - w / 2, r.top(), w, r.height())
            else:
                box = QRectF(r.left(), r.top(), w, r.height())
            bg = QColor(color)
            bg.setAlpha(46)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(box, box.height() / 2 if self.kind == "pill" else 5, box.height() / 2 if self.kind == "pill" else 5)
            p.setPen(color)
            f = p.font()
            if self.kind == "badge":
                f.setBold(True)
                p.setFont(f)
            p.drawText(box, Qt.AlignCenter, fm.elidedText(text, Qt.ElideRight, int(box.width() - 10)))
        p.restore()
