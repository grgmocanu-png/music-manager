"""Activitățile din fundal: o listă comună, vizibilă în bara de jos.

Operațiile care MODIFICĂ fișiere (scriere de taguri, redenumiri, conversii,
download-uri, mutări) rulează pe rând, una câte una, ca să nu lucreze două
pe același fișier în același timp. Operațiile care doar citesc (scanări,
căutări) pornesc imediat.

Un Job se folosește la fel ca Task: are semnalele progress / succeeded / failed,
start(), cancel() și isRunning().
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QHeaderView, QLabel, QProgressBar, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

QUEUED, RUNNING, PAUSED, DONE, FAILED, CANCELLED = "queued", "running", "paused", "done", "failed", "cancelled"
STATUS_TEXT = {QUEUED: "În așteptare", RUNNING: "Rulează", PAUSED: "Pe pauză", DONE: "Gata",
               FAILED: "Eroare", CANCELLED: "Oprită"}


class _Thread(QThread):
    def __init__(self, job):
        super().__init__()
        self.job = job

    def run(self):
        j = self.job
        try:
            res = j.fn(j._progress, j._cancelled)
            j._result = (True, res)
        except Exception as e:  # noqa: BLE001
            j._result = (False, f"{e.__class__.__name__}: {e}")


class Job(QObject):
    progress = Signal(int, int, str)
    succeeded = Signal(object)
    failed = Signal(str)
    changed = Signal()

    def __init__(self, name: str, fn, writes: bool = True, pausable: bool = True, parent=None):
        super().__init__(parent)
        self.name = name
        self.fn = fn
        self.writes = writes
        self.pausable = pausable
        self.status = QUEUED
        self.done = 0
        self.total = 0
        self.text = ""
        self.error = ""
        self.started_at = 0.0
        self._cancel = False
        self._resume = threading.Event()
        self._resume.set()
        self._thread: _Thread | None = None
        self._result = None

    # --- API ca la Task ---
    def start(self):
        manager().submit(self)

    def isRunning(self) -> bool:  # noqa: N802
        return self.status in (QUEUED, RUNNING, PAUSED)

    def cancel(self, mode=True):
        self._cancel = mode
        self._resume.set()
        if self.status == QUEUED:
            self.status = CANCELLED
            self.changed.emit()
            manager()._job_done(self)
            self.failed.emit("Oprită înainte să înceapă.")

    def pause(self):
        if self.status == RUNNING and self.pausable:
            self._resume.clear()
            self.status = PAUSED
            self.changed.emit()

    def resume(self):
        if self.status == PAUSED:
            self._resume.set()
            self.status = RUNNING
            self.changed.emit()

    def wait(self, ms: int = 0):
        if self._thread:
            self._thread.wait(ms) if ms else self._thread.wait()

    # --- folosite din firul de lucru ---
    def _progress(self, done, total, text=""):
        self.done, self.total = done, total
        if text:
            self.text = text
        self.progress.emit(done, total, text)

    def _cancelled(self):
        # pauza: firul de lucru așteaptă aici până la „Continuă” sau „Oprește”
        while not self._resume.wait(0.2):
            if self._cancel:
                break
        return self._cancel

    # --- pornire / terminare (din firul principal) ---
    def _run(self):
        self.status = RUNNING
        self.started_at = time.time()
        self._thread = _Thread(self)
        self._thread.finished.connect(self._finished)
        self._thread.start()
        self.changed.emit()

    def _finished(self):
        ok, res = self._result or (False, "oprit")
        if self._cancel and self.status != DONE:
            self.status = CANCELLED
        else:
            self.status = DONE if ok else FAILED
        if not ok:
            self.error = str(res)
        self.changed.emit()
        manager()._job_done(self)
        if ok:
            self.succeeded.emit(res)
        else:
            self.failed.emit(str(res))


class JobManager(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.jobs: list[Job] = []

    def submit(self, job: Job):
        self.jobs.append(job)
        job.changed.connect(self.changed.emit)
        if not job.writes:
            job._run()
        self._pump()
        self.changed.emit()

    def _pump(self):
        if any(j.writes and j.status in (RUNNING, PAUSED) for j in self.jobs):
            return
        nxt = next((j for j in self.jobs if j.writes and j.status == QUEUED), None)
        if nxt:
            nxt._run()

    def blocker(self, job: Job) -> Job | None:
        """Activitatea după care așteaptă una aflată „În așteptare”."""
        if job.status != QUEUED:
            return None
        return next((j for j in self.jobs if j is not job and j.writes and j.status in (RUNNING, PAUSED)), None) \
            or next((j for j in self.jobs if j is not job and j.writes and j.status == QUEUED
                     and self.jobs.index(j) < self.jobs.index(job)), None)

    def start_now(self, job: Job):
        """Pornește imediat o activitate din așteptare, fără să mai aștepte după celelalte."""
        if job.status == QUEUED:
            job._run()
            self.changed.emit()

    def _job_done(self, job):
        self._pump()
        # păstrăm doar ultimele 40 de activități terminate
        finished = [j for j in self.jobs if not j.isRunning()]
        for j in finished[:-40]:
            self.jobs.remove(j)
        self.changed.emit()

    def active(self) -> list[Job]:
        return [j for j in self.jobs if j.isRunning()]

    def clear_finished(self):
        self.jobs = [j for j in self.jobs if j.isRunning()]
        self.changed.emit()

    def shutdown(self, timeout_ms=5000):
        for j in self.jobs:
            if j.isRunning():
                j.cancel()
        for j in self.jobs:
            j.wait(timeout_ms)


_manager: JobManager | None = None


def manager() -> JobManager:
    global _manager
    if _manager is None:
        _manager = JobManager()
    return _manager


# --- interfață ----------------------------------------------------------------
class JobsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Activități")
        # se deschide sub butonul din bara de sus; click pe lângă sau „—” o strânge la loc
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("jobsPopup")
        self.resize(1000, 420)
        head = QHBoxLayout()
        ttl = QLabel("Activități")
        ttl.setObjectName("pageTitle")
        head.addWidget(ttl)
        head.addStretch()
        b_min = QPushButton("—")
        b_min.setToolTip("Strânge înapoi în butonul Activități")
        b_min.setFixedWidth(36)
        b_min.clicked.connect(self.hide)
        head.addWidget(b_min)
        info = QLabel("Operațiile care modifică fișiere rulează pe rând, ca să nu lucreze două pe același "
                      "fișier. Poți închide fereastra asta și continua lucrul; activitățile merg mai departe.")
        info.setWordWrap(True)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Activitate", "Stare", "Progres", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 220)
        self.table.setColumnWidth(2, 220)
        self.table.setColumnWidth(3, 260)
        b_clear = QPushButton("Șterge din listă cele terminate")
        b_clear.clicked.connect(manager().clear_finished)
        row = QHBoxLayout()
        row.addWidget(b_clear)
        row.addStretch()
        lay = QVBoxLayout(self)
        lay.addLayout(head)
        lay.addWidget(info)
        lay.addWidget(self.table, 1)
        lay.addLayout(row)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(500)
        manager().changed.connect(self.refresh)
        from . import windows as wins
        wins.hub.changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        if not self.isVisible() and self.table.rowCount():
            return
        from . import windows as wins
        wl = wins.windows()
        jobs = list(reversed(manager().jobs))
        if self.table.rowCount() != len(jobs) + len(wl):
            self.table.setRowCount(len(jobs) + len(wl))
        # ferestrele de lucru (Taguri online, Dubluri…): le redeschizi de aici, cu tot progresul
        for i, w in enumerate(wl):
            it = QTableWidgetItem("🪟 " + wins.describe(w))
            self.table.setItem(i, 0, it)
            self.table.setItem(i, 1, QTableWidgetItem("Fereastră deschisă" if w.isVisible() and not w.isMinimized()
                                                      else "Fereastră strânsă"))
            self.table.removeCellWidget(i, 2)
            self.table.setItem(i, 2, QTableWidgetItem(""))
            cw = QWidget()
            h = QHBoxLayout(cw)
            h.setContentsMargins(2, 0, 2, 0)
            b = QPushButton("Deschide")
            b.clicked.connect(lambda _=False, ww=w: (self.hide(), wins.reopen(ww)))
            h.addWidget(b)
            self.table.setCellWidget(i, 3, cw)
        off = len(wl)
        for i, j in enumerate(jobs, start=off):
            name = QTableWidgetItem(j.name + (f" — {j.text}" if j.text and j.isRunning() else ""))
            name.setToolTip(j.error or j.text or j.name)
            self.table.setItem(i, 0, name)
            st = STATUS_TEXT[j.status] + (f": {j.error[:60]}" if j.status == FAILED else "")
            tip = st
            blk = manager().blocker(j)
            if blk is not None:
                why = " (e pe pauză)" if blk.status == PAUSED else ""
                st = f"Așteaptă după: {blk.name}{why}"
                tip = (f"Așteaptă să se termine „{blk.name}”{why}, ca să nu lucreze două activități pe "
                       "aceleași fișiere. Poți apăsa „Pornește acum” dacă știi că sunt fișiere diferite.")
            st_item = QTableWidgetItem(st)
            st_item.setToolTip(tip)
            self.table.setItem(i, 1, st_item)
            bar = self.table.cellWidget(i, 2)
            if not isinstance(bar, QProgressBar):
                bar = QProgressBar()
                self.table.setCellWidget(i, 2, bar)
            bar.setMaximum(j.total if (j.total and j.status == RUNNING) else (0 if j.status == RUNNING else 1))
            bar.setValue(j.done if j.total else (1 if j.status == DONE else 0))
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(2, 0, 2, 0)
            if j.status == RUNNING and j.pausable:
                b = QPushButton("Pauză")
                b.clicked.connect(j.pause)
                h.addWidget(b)
            elif j.status == PAUSED:
                b = QPushButton("Continuă")
                b.clicked.connect(j.resume)
                h.addWidget(b)
            if j.status == QUEUED:
                b = QPushButton("Pornește acum")
                b.clicked.connect(lambda _=False, jj=j: self._start_now(jj))
                h.addWidget(b)
            if j.isRunning():
                c = QPushButton("Oprește")
                c.clicked.connect(lambda _=False, jj=j: jj.cancel())
                h.addWidget(c)
            self.table.setCellWidget(i, 3, w)


    def _start_now(self, job):
        from PySide6.QtWidgets import QMessageBox
        blk = manager().blocker(job)
        if blk is not None and QMessageBox.question(
                self, "Pornește acum",
                f"„{job.name}” așteaptă după „{blk.name}”.\n\nO pornesc acum, în paralel? E sigur doar dacă "
                "cele două nu lucrează pe aceleași fișiere.") != QMessageBox.Yes:
            return
        manager().start_now(job)


class JobsButton(QPushButton):
    """Butonul din bara de jos: „⏳ 2 activități”."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlat(True)
        self.dialog: JobsDialog | None = None
        self.clicked.connect(self.open)
        manager().changed.connect(self.refresh)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(1000)
        self.refresh()

    def refresh(self):
        act = manager().active()
        from . import windows as wins
        hidden = [w for w in wins.windows() if not w.isVisible() or w.isMinimized()]
        if hidden and not act:
            self.setText(f"🪟 {len(hidden)} {'fereastră strânsă' if len(hidden) == 1 else 'ferestre strânse'}")
            self.setToolTip("Click: redeschide ferestrele de lucru")
            return
        if not act:
            self.setText("✓ Nicio activitate")
            self.setToolTip("Click: lista activităților")
            return
        running = [j for j in act if j.status in (RUNNING, PAUSED)]
        cur = running[0] if running else act[0]
        pct = f" {int(cur.done * 100 / cur.total)}%" if cur.total else ""
        self.setText(f"⏳ {len(act)} {'activitate' if len(act) == 1 else 'activități'} · {cur.name}{pct}")
        self.setToolTip("Click: vezi, pune pe pauză sau oprește activitățile")

    def open(self):
        if self.dialog is None:
            self.dialog = JobsDialog(self.window())
        d = self.dialog
        if d.isVisible():
            d.hide()
            return
        # sub buton, aliniat la dreapta, fără să iasă din ecran
        g = self.mapToGlobal(self.rect().bottomRight())
        scr = self.screen().availableGeometry() if self.screen() else None
        w = min(1000, scr.width() - 20) if scr else 1000
        x = g.x() - w
        if scr:
            x = max(scr.left() + 10, x)
        d.resize(w, d.height())
        d.move(x, g.y() + 4)
        d.show()
        d.refresh()
