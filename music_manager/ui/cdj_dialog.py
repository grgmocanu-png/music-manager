"""Fereastra „Stick CDJ”: playlisturile bifate în Export, scrise pe stick în formatul Pioneer (fără Rekordbox).

1. alegi stickul (în listă apar doar stickuri și discuri externe);
2. „Previzualizează” arată ce s-ar copia / actualiza / șterge și cât loc rămâne — nu scrie nimic;
3. „Scrie pe stick” copiază doar ce e nou sau schimbat; șterge doar piese puse de Manager, și doar cu bifă;
4. „Formatează…” face stickul FAT32, după ce tastezi numele lui.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout,
)

from ..core.cdj import drives as drv
from ..core.cdj import stick
from ..core.djcollections import norm_path
from .common import data_dir, settings


def _int(v) -> int:
    try:
        return int(str(v)[:4])
    except (TypeError, ValueError):
        return 0


def stick_tracks(library, playlists) -> list[tuple[str, list[stick.StickTrack]]]:
    """Playlisturile din Export → piese pentru stick, cu gridul din Traktor / Rekordbox când există."""
    grids = {}
    for c in getattr(library, "collections", []) or []:
        if c.error:
            continue
        for k, info in c.tracks.items():
            if getattr(info, "grid_ms", -1) >= 0 and k not in grids:
                grids[k] = info
    out = []
    for name, items in playlists:
        tracks = []
        for it in items:
            t = it.track
            g = grids.get(norm_path(t.path))
            tracks.append(stick.StickTrack(
                src=t.path, usb="", artist=t.artist, title=t.display_title, album=t.album, genre=t.genre,
                year=_int(t.year), duration=t.duration, bitrate=t.bitrate, bpm=float(it.bpm or 0),
                key=it.key or t.key, rating=int(it.rating or 0),
                grid_ms=g.grid_ms if g else -1.0, grid_bpm=g.grid_bpm if g else 0.0,
                grid_beat=g.grid_beat if g else 1))
        out.append((name, tracks))
    return out


class CdjStickDialog(QDialog):
    def __init__(self, playlists, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Stick CDJ")
        self.resize(900, 620)
        self.playlists = playlists
        self.plan = None
        n = sum(len(i) for _n, i in playlists)
        intro = QLabel(f"<b>{len(playlists)} playlisturi</b> ({n} piese) scrise direct în formatul citit de CDJ-uri "
                       "(export.pdb, ca un export Rekordbox): playlisturi, BPM, Key, beatgrid, waveform, coperți. "
                       "Merge pe CDJ-350 … CDJ-3000 și XDJ. Fără hot cues automate. Încearcă stickul pe CDJ înainte "
                       "de o seară în club.")
        intro.setWordWrap(True)
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._check)
        b_ref = QPushButton("↻")
        b_ref.setToolTip("Caută din nou stickurile")
        b_ref.clicked.connect(self.refresh)
        row = QHBoxLayout()
        row.addWidget(QLabel("Stick:"))
        row.addWidget(self.combo, 1)
        row.addWidget(b_ref)
        self.state = QLabel()
        self.state.setWordWrap(True)
        self.prune = QCheckBox("Șterge de pe stick piesele puse de Manager care nu mai sunt în playlisturile bifate")
        self.prune.setChecked(settings().value("cdj/prune", False, type=bool))
        self.prune.toggled.connect(lambda on: (settings().setValue("cdj/prune", on), self._clear_plan()))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Ce", "Detalii"])
        self.tree.setColumnWidth(0, 330)
        self.summary = QLabel("Apasă „Previzualizează” ca să vezi ce s-ar schimba. Nu se scrie nimic până nu apeși "
                              "„Scrie pe stick”.")
        self.summary.setWordWrap(True)
        self.summary.setObjectName("muted")
        self.b_format = QPushButton("Formatează…")
        self.b_format.clicked.connect(self.format_stick)
        self.b_preview = QPushButton("Previzualizează")
        self.b_preview.clicked.connect(self.preview)
        self.b_write = QPushButton("Scrie pe stick")
        self.b_write.setObjectName("primary")
        self.b_write.clicked.connect(self.write)
        close = QPushButton("Închide")
        close.clicked.connect(self.reject)
        br = QHBoxLayout()
        br.addWidget(self.b_format)
        br.addStretch()
        br.addWidget(close)
        br.addWidget(self.b_preview)
        br.addWidget(self.b_write)
        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(row)
        lay.addWidget(self.state)
        lay.addWidget(self.prune)
        lay.addWidget(self.tree, 1)
        lay.addWidget(self.summary)
        lay.addLayout(br)
        self.refresh()

    # --- stickul -----------------------------------------------------------------------------
    def refresh(self):
        self.drives = drv.list_drives()
        self.combo.blockSignals(True)
        self.combo.clear()
        last = settings().value("cdj/drive", "") or ""
        for i, d in enumerate(self.drives):
            self.combo.addItem(d.title, i)
            if d.root == last:
                self.combo.setCurrentIndex(i)
        self.combo.blockSignals(False)
        self._check()

    def drive(self) -> drv.Drive | None:
        i = self.combo.currentData()
        return self.drives[i] if i is not None and 0 <= i < len(self.drives) else None

    def _check(self, *_):
        self._clear_plan()
        d = self.drive()
        if d is None:
            self.state.setText("<b>Nu văd niciun stick.</b> Bagă stickul și apasă ↻. (Discul calculatorului nu apare "
                               "aici, ca să nu scrii pe el din greșeală.)")
            self.ok = False
        else:
            self.ok, why = drv.check(d)
            color = "#5fb3a6" if self.ok else "#e0a458"
            self.state.setText(f"<span style='color:{color}'>{'✓' if self.ok else '⚠'}</span> {why}")
            settings().setValue("cdj/drive", d.root)
        self.b_preview.setEnabled(self.ok)
        self.b_write.setEnabled(self.ok)
        self.b_format.setEnabled(d is not None)

    def _clear_plan(self):
        self.plan = None
        self.tree.clear()

    # --- previzualizarea --------------------------------------------------------------------------
    def preview(self) -> bool:
        d = self.drive()
        if d is None or not self.ok:
            return False
        self.plan = p = stick.plan(d.root, self.playlists, prune=self.prune.isChecked())
        self.tree.clear()

        def group(title, rows, detail=lambda r: ""):
            g = QTreeWidgetItem([f"{title} ({len(rows)})", ""])
            f = g.font(0)
            f.setBold(True)
            g.setFont(0, f)
            self.tree.addTopLevelItem(g)
            for r in rows[:500]:
                g.addChild(QTreeWidgetItem([os.path.basename(r if isinstance(r, str) else r.src), detail(r)]))
            if len(rows) > 500:
                g.addChild(QTreeWidgetItem([f"… și încă {len(rows) - 500}", ""]))
            return g

        g = group("Se copiază (noi sau schimbate)", p.copy, lambda t: t.usb)
        g.setExpanded(len(p.copy) <= 40)
        group("Deja pe stick, la zi", [t for t in p.tracks if t not in p.copy], lambda t: t.usb)
        if p.delete:
            group("Se șterg de pe stick (puse de Manager, scoase din playlisturi)", p.delete).setExpanded(True)
        if p.missing:
            group("Lipsesc de pe hard (sar peste ele)", p.missing).setExpanded(True)
        pl = QTreeWidgetItem([f"Playlisturi pe stick ({len(p.playlists)})", ""])
        self.tree.addTopLevelItem(pl)
        for name, usbs in p.playlists:
            pl.addChild(QTreeWidgetItem([name, f"{len(usbs)} piese"]))
        mb = p.bytes_to_copy / 1024 ** 2
        txt = (f"<b>{len(p.copy)}</b> de copiat ({mb:,.0f} MB) · {p.unchanged} la zi · {len(p.delete)} de șters · "
               f"liber pe stick: {p.free / 1024 ** 3:.1f} GB")
        if not p.fits:
            txt += " · <span style='color:#e0a458'><b>nu încape</b></span>"
        if p.flac:
            txt += f"<br>🏷 {p.flac} piese sunt FLAC: {stick.OLD_PLAYERS_NO_FLAC} (le poți converti din Curățenie)."
        self.summary.setText(txt)
        return True

    # --- scrierea ------------------------------------------------------------------------------
    def write(self):
        if self.plan is None and not self.preview():
            return
        p = self.plan
        if not p.fits:
            QMessageBox.warning(self, "Stick CDJ", "Nu încape pe stick. Debifează câteva playlisturi sau alege un stick "
                                "mai mare.")
            return
        msg = f"Scriu pe {self.drive().root}: {len(p.copy)} piese de copiat, {len(p.tracks)} în total."
        if p.delete:
            msg += f"\n\nȘterg de pe stick {len(p.delete)} piese puse de Manager care nu mai sunt în playlisturi."
        if QMessageBox.question(self, "Stick CDJ", msg + "\n\nContinui?") != QMessageBox.Yes:
            return
        from .jobs import Job
        cache = os.path.join(data_dir(), "cdj")

        def work(progress, cancelled):
            return stick.write(p, cache_dir=cache, progress=progress, cancelled=cancelled)

        job = Job(f"Stick CDJ: {len(p.tracks)} piese", work)
        job.progress.connect(lambda d, t, x: self.summary.setText(f"{x}… {d}/{t}"))

        def done(rep):
            self._clear_plan()
            text = (f"Gata: {rep.get('tracks', 0)} piese pe stick, în {len(p.playlists)} playlisturi.\n"
                    f"Copiate acum: {rep['copied']}" + (f" · șterse: {rep['deleted']}" if rep['deleted'] else ""))
            try:
                back = stick.read_back(p.root)
                text += f"\n\nVerificat: baza de date de pe stick are {len(back['tracks'])} piese și " \
                        f"{len(back['playlists'])} playlisturi."
            except Exception as e:  # noqa: BLE001
                text += f"\n\n⚠ Nu am putut verifica baza de date de pe stick: {e}"
            if rep["errors"]:
                text += f"\n\n{len(rep['errors'])} erori:\n" + "\n".join(rep["errors"][:12])
            text += "\n\nScoate stickul în siguranță (Eject) înainte să-l bagi în CDJ."
            self.summary.setText(text.replace("\n", "<br>"))
            QMessageBox.information(self, "Stick CDJ", text)

        job.succeeded.connect(done)
        job.failed.connect(lambda e: QMessageBox.warning(self, "Stick CDJ", f"Scrierea s-a oprit: {e}"))
        job.start()

    # --- formatarea ------------------------------------------------------------------------------
    def format_stick(self):
        d = self.drive()
        if d is None:
            return
        ok, why = drv.can_format(d)
        if not ok:
            QMessageBox.information(self, "Formatare", why)
            return
        name = d.label or "fără nume"
        text, go = QInputDialog.getText(
            self, "Formatează ca stick de DJ",
            f"Se ȘTERGE TOT de pe {d.root} ({name}, {d.size / 1024 ** 3:.1f} GB) și se face FAT32, citit de toate "
            f"CDJ-urile.\n\nCa să confirmi, scrie numele stickului: {name}")
        if not go:
            return
        if text.strip().lower() != name.lower():
            QMessageBox.information(self, "Formatare", "Numele nu se potrivește. N-am formatat nimic.")
            return
        label, go = QInputDialog.getText(self, "Formatează", "Numele nou al stickului (max. 11 litere):",
                                         text=drv.clean_label(d.label or "DJ"))
        if not go:
            return
        from . import player
        player.free_files([pp.path for pp in player._PANELS
                           if pp.path and os.path.normcase(pp.path).startswith(os.path.normcase(d.root))])
        self.summary.setText("Formatez… (pe Windows apare fereastra de confirmare a sistemului)")
        self.repaint()
        try:
            drv.format_fat32(d, label)
        except RuntimeError as e:
            QMessageBox.warning(self, "Formatare", str(e))
            self.summary.setText("")
            return
        self.summary.setText("Stickul e formatat FAT32.")
        self.refresh()
