"""Playerul din Bibliotecă: waveform colorat (bas / mediu / înalt), drop-uri, play/pauză."""
from __future__ import annotations

import os

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSlider, QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from .common import fmt_duration, settings

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    HAVE_MULTIMEDIA = True
except Exception:   # pragma: no cover
    HAVE_MULTIMEDIA = False


class WaveformWidget(QWidget):
    """Desenează piesa: înălțimea = volumul, culorile = unde stă energia.

    Basul (kick, bass) e albastru închis, mediile portocalii, înaltele albe —
    ca în Rekordbox / Traktor. Liniile roșii marchează un posibil drop.
    """
    seek = Signal(float)     # 0..1

    BASS = QColor(30, 110, 255)
    MID = QColor(255, 150, 40)
    HIGH = QColor(235, 235, 235)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(90)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.peak = self.low = self.mid = self.high = []
        self.drops: list[float] = []
        self.duration = 0.0
        self.position = 0.0      # 0..1
        self.message = "Alege o piesă ca să-i vezi forma."
        self.hover = -1.0

    def set_data(self, analysis, duration: float):
        if analysis is None or not analysis.has_waveform:
            self.peak = self.low = self.mid = self.high = []
            self.message = "Se analizează piesa…" if analysis is None else (analysis.error or "Fără date")
        else:
            self.peak, self.low = analysis.peak, analysis.low
            self.mid, self.high = analysis.mid, analysis.high
            self.message = ""
        self.drops = list(getattr(analysis, "drops", []) or [])
        self.duration = duration or getattr(analysis, "duration", 0) or 0
        self.position = 0.0
        self.update()

    def set_message(self, text: str):
        self.peak = []
        self.message = text
        self.update()

    def set_position(self, frac: float):
        self.position = max(0.0, min(1.0, frac))
        self.update()

    def mousePressEvent(self, e):
        if self.peak and self.width() > 0:
            self.seek.emit(max(0.0, min(1.0, e.position().x() / self.width())))

    def mouseMoveEvent(self, e):
        self.hover = e.position().x() / max(1, self.width())
        self.update()

    def leaveEvent(self, e):
        self.hover = -1
        self.update()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(22, 22, 26))
        if not self.peak:
            p.setPen(QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignCenter, self.message)
            return
        n = len(self.peak)
        mid_y = h / 2
        step = w / n

        def band(values, scale, color, alpha=255):
            path = QPainterPath()
            path.moveTo(0, mid_y)
            for i, v in enumerate(values):
                path.lineTo(i * step, mid_y - (v / 255) * scale * (h / 2 - 2))
            for i in range(n - 1, -1, -1):
                path.lineTo(i * step, mid_y + (values[i] / 255) * scale * (h / 2 - 2))
            path.closeSubpath()
            c = QColor(color)
            c.setAlpha(alpha)
            p.fillPath(path, c)

        # conturul total, apoi benzile de jos în sus
        band(self.peak, 1.0, QColor(60, 60, 70))
        band(self.low, 0.95, self.BASS, 230)
        band(self.mid, 0.6, self.MID, 220)
        band(self.high, 0.35, self.HIGH, 200)

        # porțiunea deja cântată, întunecată
        if self.position > 0:
            p.fillRect(QRectF(0, 0, self.position * w, h), QColor(0, 0, 0, 90))

        # drop-uri
        if self.duration:
            f = QFont(self.font())
            f.setPointSize(max(7, f.pointSize() - 2))
            p.setFont(f)
            for d in self.drops:
                x = d / self.duration * w
                p.setPen(QPen(QColor(255, 60, 60), 2))
                p.drawLine(QPointF(x, 0), QPointF(x, h))
                p.drawText(QPointF(x + 3, 11), "drop?")

        # cursorul de redare
        x = self.position * w
        p.setPen(QPen(QColor(255, 255, 255), 2))
        p.drawLine(QPointF(x, 0), QPointF(x, h))

        # timpul sub mouse
        if self.hover >= 0 and self.duration:
            hx = self.hover * w
            p.setPen(QPen(QColor(255, 255, 255, 120), 1, Qt.DashLine))
            p.drawLine(QPointF(hx, 0), QPointF(hx, h))
            p.setPen(QColor(255, 255, 255))
            p.drawText(QPointF(min(hx + 4, w - 40), h - 4), fmt_duration(self.hover * self.duration))


class PlayerPanel(QWidget):
    """Bara de jos din Bibliotecă."""
    request_next = Signal(int)          # +1 / -1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = ""
        self.duration = 0.0
        self.wave = WaveformWidget()
        self.title = QLabel("—")
        self.title.setStyleSheet("font-weight: bold;")
        self.info = QLabel("")
        self.info.setStyleSheet("color: gray;")
        self.time = QLabel("0:00 / 0:00")
        self.b_prev = QToolButton()
        self.b_prev.setText("⏮")
        self.b_play = QToolButton()
        self.b_play.setText("▶")
        self.b_next = QToolButton()
        self.b_next.setText("⏭")
        for b in (self.b_prev, self.b_play, self.b_next):
            b.setMinimumSize(34, 30)
        self.b_drop = QPushButton("Sari la drop")
        self.b_drop.setToolTip("Sare la următorul loc marcat „drop?”")
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setMaximumWidth(110)
        self.vol.setValue(int(settings().value("player/volume", 80) or 80))

        top = QHBoxLayout()
        top.addWidget(self.b_prev)
        top.addWidget(self.b_play)
        top.addWidget(self.b_next)
        top.addSpacing(8)
        self.cover = QLabel()
        self.cover.setFixedSize(46, 46)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setStyleSheet("border: 1px solid #ccc; color: #aaa; font-size: 18px;")
        self.cover.setText("♪")
        top.addWidget(self.cover)
        top.addSpacing(6)
        tl = QVBoxLayout()
        tl.setSpacing(0)
        tl.addWidget(self.title)
        tl.addWidget(self.info)
        top.addLayout(tl, 1)
        top.addWidget(self.b_drop)
        top.addWidget(self.time)
        top.addWidget(QLabel("🔊"))
        top.addWidget(self.vol)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        lay.addLayout(top)
        lay.addWidget(self.wave)

        self.player = None
        if HAVE_MULTIMEDIA:
            self.player = QMediaPlayer(self)
            self.audio = QAudioOutput(self)
            self.player.setAudioOutput(self.audio)
            self.audio.setVolume(self.vol.value() / 100)
            self.player.positionChanged.connect(self._pos)
            self.player.durationChanged.connect(lambda d: setattr(self, "duration", d / 1000 or self.duration))
            self.player.playbackStateChanged.connect(self._state)
            self.player.errorOccurred.connect(lambda *_: self.info.setText("Nu pot reda fișierul: " +
                                                                            self.player.errorString()))
        else:
            self.b_play.setEnabled(False)
            self.info.setText("Redarea nu e disponibilă pe acest sistem.")
        self.b_play.clicked.connect(self.toggle)
        self.b_prev.clicked.connect(lambda: self.request_next.emit(-1))
        self.b_next.clicked.connect(lambda: self.request_next.emit(1))
        self.b_drop.clicked.connect(self.jump_drop)
        self.vol.valueChanged.connect(self._volume)
        self.wave.seek.connect(self.seek_frac)

    def _volume(self, v):
        settings().setValue("player/volume", v)
        if self.player:
            self.audio.setVolume(v / 100)

    def load(self, path: str, title: str, info: str, duration: float, analysis=None, autoplay=True):
        self.path = path
        self.duration = duration
        self.title.setText(title)
        self.info.setText(info)
        self.wave.set_data(analysis, duration)
        self.b_drop.setEnabled(bool(analysis and getattr(analysis, "drops", None)))
        self._show_cover(path)
        if self.player:
            self.player.setSource(QUrl.fromLocalFile(path))
            if autoplay:
                self.player.play()

    def _show_cover(self, path: str):
        """Coperta mică din tagurile fișierului (sau o notă muzicală dacă nu are)."""
        pix = None
        try:
            from ..core import tags as tagmod
            data = tagmod.read_tags(path, want_cover=True)
            if data.cover:
                img = QImage.fromData(data.cover[0])
                if not img.isNull():
                    pix = QPixmap.fromImage(img).scaled(44, 44, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception:  # noqa: BLE001
            pix = None
        if pix is not None:
            self.cover.setPixmap(pix)
            self.cover.setToolTip("")
        else:
            self.cover.setPixmap(QPixmap())
            self.cover.setText("♪")
            self.cover.setToolTip("Piesa nu are copertă")

    def set_analysis(self, analysis):
        self.wave.set_data(analysis, self.duration or getattr(analysis, "duration", 0))
        self.b_drop.setEnabled(bool(analysis and analysis.drops))
        if self.player and self.duration:
            self.wave.set_position(self.player.position() / 1000 / self.duration)

    def toggle(self):
        if not self.player or not self.path:
            return
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def stop(self):
        if self.player:
            self.player.stop()

    def seek_frac(self, frac: float):
        if self.player and self.duration:
            self.player.setPosition(int(frac * self.duration * 1000))
            if self.player.playbackState() != QMediaPlayer.PlayingState:
                self.player.play()

    def jump_drop(self):
        if not self.player or not self.wave.drops:
            return
        now = self.player.position() / 1000
        nxt = [d for d in self.wave.drops if d > now + 1] or self.wave.drops
        self.player.setPosition(int(max(0, nxt[0] - 4) * 1000))   # câteva secunde înainte
        self.player.play()

    def _pos(self, ms):
        if self.duration:
            self.wave.set_position(ms / 1000 / self.duration)
        self.time.setText(f"{fmt_duration(ms / 1000)} / {fmt_duration(self.duration)}")

    def _state(self, st):
        self.b_play.setText("⏸" if st == QMediaPlayer.PlayingState else "▶")
        if st == QMediaPlayer.StoppedState and self.player.mediaStatus() == QMediaPlayer.EndOfMedia:
            self.request_next.emit(1)
