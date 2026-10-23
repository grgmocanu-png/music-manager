"""Pagina „Export”: playlisturile tale ca foldere cu fișiere copiate (stick sau hard), plus fișierele de playlist
(M3U, Traktor NML, Rekordbox XML) ca să le poți importa oriunde.

- un folder pe playlist, cu numele playlistului; fișierele își păstrează numele;
- a doua oară copiază doar ce e nou sau schimbat; ce a ieșit din playlist se șterge doar cu bifă, după întrebare;
- playlisturile din „_Playlisturi” trimit la copii (implicit) sau la originalele de pe hard;
- folderul de export primește un fișier „.manager-export”: Manager nu-l scanează în Bibliotecă / Curățenie.
"""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton,
    QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core import libdb as ldb
from ..core import library as lib
from ..core.djcollections import norm_path
from ..core.playlist import write_m3u
from .common import settings

PL_DIR = "_Playlisturi"
INSTRUCTIONS = (
    "<b>Traktor</b>: în stânga, click dreapta pe <i>Playlists</i> → <i>Import Playlist</i> → alegi fișierul "
    "<code>.nml</code> din <code>_Playlisturi</code>. Traktor le analizează apoi (beatgrid, waveform).<br>"
    "<b>Rekordbox 6</b>, o singură dată: <i>Preferences → Advanced → Database → rekordbox xml → Imported Library</i> → "
    "alegi <code>_Playlisturi\\rekordbox.xml</code> (și la <i>Layout</i> bifezi „rekordbox xml”). Apoi, în stânga, "
    "nodul <i>rekordbox xml → Playlists</i> → click dreapta pe playlist → <i>Import Playlist</i>.<br>"
    "<b>Orice alt program</b> (VirtualDJ, Serato, Engine DJ, VLC): deschizi <code>.m3u8</code>-ul playlistului.")


def _same(src: str, dst: str) -> bool:
    try:
        a, b = os.stat(src), os.stat(dst)
    except OSError:
        return False
    return a.st_size == b.st_size and b.st_mtime >= a.st_mtime - 2


class ExportTab(QWidget):
    def __init__(self, state, library, parent=None):
        super().__init__(parent)
        self.state, self.library = state, library
        self.task = None
        title = QLabel("Export")
        title.setObjectName("pageTitle")
        sub = QLabel("Playlisturile ca foldere cu fișiere copiate (stick sau hard) + fișiere de playlist pentru "
                     "Traktor, Rekordbox și orice player. Originalele rămân neatinse.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)

        # --- stânga: playlisturile ---
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Playlist", "Piese"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setObjectName("sidebar")
        b_all, b_none = QPushButton("Bifează tot"), QPushButton("Debifează tot")
        b_all.clicked.connect(lambda: self._set_all(True))
        b_none.clicked.connect(lambda: self._set_all(False))
        b_ref = QPushButton("↻")
        b_ref.setToolTip("Reîmprospătează lista")
        b_ref.clicked.connect(self.refresh)
        lb = QHBoxLayout()
        lb.addWidget(b_all)
        lb.addWidget(b_none)
        lb.addStretch()
        lb.addWidget(b_ref)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addLayout(lb)
        ll.addWidget(self.tree, 1)

        # --- dreapta: unde și cum ---
        self.dest = QLineEdit(settings().value("export/dest", "") or "")
        self.dest.setPlaceholderText("Stick-ul sau un folder pe hard (ex. E:\\  sau  D:\\Export playlisturi)")
        b_pick = QPushButton("Alege…")
        b_pick.clicked.connect(self._pick)
        drow = QHBoxLayout()
        drow.addWidget(self.dest, 1)
        drow.addWidget(b_pick)
        self.rb_copies = QRadioButton("Fișierele de playlist trimit la copiile din export (merge pe orice stick / disc)")
        self.rb_orig = QRadioButton("…trimit la originalele de pe hard (import în Traktor / Rekordbox pe acest "
                                    "calculator, fără piese dublate în colecție)")
        (self.rb_orig if settings().value("export/refs", "copies") == "orig" else self.rb_copies).setChecked(True)
        g = QButtonGroup(self)
        g.addButton(self.rb_copies)
        g.addButton(self.rb_orig)
        self.c_files = QCheckBox("Copiază fișierele (un folder pe playlist)")
        self.c_files.setChecked(settings().value("export/files", True) not in (False, "false"))
        self.c_m3u = QCheckBox("M3U8 (orice player, VirtualDJ, Serato, Engine DJ)")
        self.c_nml = QCheckBox("Traktor (NML)")
        self.c_xml = QCheckBox("Rekordbox 6 (XML)")
        for c, k in ((self.c_m3u, "m3u"), (self.c_nml, "nml"), (self.c_xml, "xml")):
            c.setChecked(settings().value(f"export/{k}", True) not in (False, "false"))
        self.c_prune = QCheckBox("Șterge de la destinație piesele scoase între timp din playlist (te întreb înainte)")
        self.c_prune.setChecked(settings().value("export/prune", False) in (True, "true"))
        b_go = QPushButton("Exportă bifatele")
        b_go.setObjectName("primary")
        b_go.clicked.connect(lambda: self.export())
        self.b_again = QPushButton("↻ Sincronizează din nou ultimul export")
        self.b_again.setToolTip("Aceleași playlisturi, în același loc: copiază doar ce e nou sau schimbat")
        self.b_again.clicked.connect(self.export_again)
        brow = QHBoxLayout()
        brow.addWidget(b_go)
        brow.addWidget(self.b_again)
        brow.addStretch()
        self.last = QLabel()
        self.last.setObjectName("muted")
        self.last.setWordWrap(True)
        how = QLabel(INSTRUCTIONS)
        how.setWordWrap(True)
        how.setTextFormat(Qt.RichText)
        how.setObjectName("card")
        how.setStyleSheet("padding: 12px;")
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 0, 0, 0)
        rl.addWidget(QLabel("<b>Unde</b>"))
        rl.addLayout(drow)
        rl.addSpacing(8)
        rl.addWidget(QLabel("<b>Ce</b>"))
        for w in (self.c_files, self.c_m3u, self.c_nml, self.c_xml, self.c_prune):
            rl.addWidget(w)
        rl.addSpacing(8)
        rl.addWidget(QLabel("<b>Fișierele de playlist</b>"))
        rl.addWidget(self.rb_copies)
        rl.addWidget(self.rb_orig)
        rl.addSpacing(10)
        rl.addLayout(brow)
        rl.addWidget(self.last)
        rl.addSpacing(10)
        rl.addWidget(QLabel("<b>Cum le imporți</b>"))
        rl.addWidget(how)
        rl.addStretch()

        split = QSplitter()
        split.addWidget(left)
        split.addWidget(right)
        split.setSizes([420, 900])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 12)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addWidget(split, 1)
        self.refresh()
        self._show_last()

    # --- lista de playlisturi ------------------------------------------------------
    def refresh(self):
        checked = self._checked_keys() if self.tree.topLevelItemCount() else set(self._last().get("keys", []))
        self.tree.clear()
        lv = self.library

        def group(title):
            g = QTreeWidgetItem([title, ""])
            f = g.font(0)
            f.setBold(True)
            g.setFont(0, f)
            g.setFlags(g.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            g.setCheckState(0, Qt.Unchecked)
            self.tree.addTopLevelItem(g)
            return g

        def add(parent, name, key, n):
            it = QTreeWidgetItem([name, str(n)])
            it.setData(0, Qt.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(0, Qt.Checked if json.dumps(key) in checked else Qt.Unchecked)
            parent.addChild(it)

        mine = group("Playlisturile mele")
        for p in lv.db.playlists:
            key = ["smart" if p.kind == "smart" else "pl", p.id]
            n = len(lv.items_for(tuple(key))[1]) if p.kind == "smart" else len(p.tracks)
            add(mine, ("⚡ " if p.kind == "smart" else "") + p.name, key, n)
        for c in lv.collections:
            if c.playlists and not c.error:
                g = group(f"Din {c.source}")
                for name in sorted(c.playlists, key=str.lower):
                    add(g, name, ["ext", c.source, name], len(c.playlists[name]))
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setExpanded(True)

    def _set_all(self, on):
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, Qt.Checked if on else Qt.Unchecked)

    def _checked_keys(self) -> set[str]:
        out = set()
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            for j in range(g.childCount()):
                it = g.child(j)
                if it.checkState(0) == Qt.Checked:
                    out.add(json.dumps(list(it.data(0, Qt.UserRole))))
        return out

    def _pick(self):
        d = QFileDialog.getExistingDirectory(self, "Unde export? (stick sau folder pe hard)", self.dest.text())
        if d:
            self.dest.setText(os.path.normpath(d))

    # --- ultimul export -------------------------------------------------------------
    def _last(self) -> dict:
        try:
            return json.loads(settings().value("export/last", "") or "{}")
        except ValueError:
            return {}

    def _show_last(self):
        d = self._last()
        if d:
            self.last.setText(f"Ultimul export: {d.get('when', '')[:16].replace('T', ' ')} · {d.get('n', 0)} playlisturi "
                              f"în {d.get('dest', '')}")
        self.b_again.setEnabled(bool(d.get("keys")))

    def export_again(self):
        d = self._last()
        if not d.get("keys"):
            return
        self.dest.setText(d.get("dest", ""))
        self.export(set(d["keys"]))

    # --- exportul ---------------------------------------------------------------------
    def _playlists(self, keys) -> list[tuple[str, list]]:
        out = []
        for k in sorted(keys):
            key = tuple(json.loads(k))
            title, items = self.library.items_for(key)
            name = key[2] if key[0] == "ext" else title.lstrip("⚡ ").split(": ", 1)[-1]
            out.append((name, [i for i in items if not (i.missing or i.offline)]))
        return out

    def export(self, keys=None):
        keys = keys if keys is not None else self._checked_keys()
        dest = self.dest.text().strip()
        if not keys:
            QMessageBox.information(self, "Export", "Bifează playlisturile pe care le exporți.")
            return
        if not dest:
            QMessageBox.information(self, "Export", "Alege unde exporți (stick sau folder pe hard).")
            return
        dest = os.path.normpath(dest)
        # destinația în bibliotecă? → subfolder separat, ignorat
        for f in self.library.library_folders():
            nf = norm_path(f).rstrip("\\/")
            nd = norm_path(dest)
            if nd == nf or nd.startswith(nf + os.sep):
                if QMessageBox.question(self, "Export", f"„{dest}” e în biblioteca ta ({f}). Exportul îl pun în "
                                        "subfolderul „Export playlisturi”, pe care Manager îl ignoră la scanare. "
                                        "Continui?") != QMessageBox.Yes:
                    return
                dest = os.path.join(dest, "Export playlisturi")
                break
        self.dest.setText(dest)
        for c, k in ((self.c_files, "files"), (self.c_m3u, "m3u"), (self.c_nml, "nml"), (self.c_xml, "xml"),
                     (self.c_prune, "prune")):
            settings().setValue(f"export/{k}", c.isChecked())
        settings().setValue("export/dest", dest)
        settings().setValue("export/refs", "orig" if self.rb_orig.isChecked() else "copies")
        pls = self._playlists(keys)
        copy_files = self.c_files.isChecked()
        # plan: ce copiez, ce sar, ce ar trebui șters
        plan, stale = [], []
        for name, items in pls:
            folder = os.path.join(dest, lib.safe_name(name) or "Playlist")
            want = set()
            for it in items:
                tgt = os.path.join(folder, os.path.basename(it.path))
                want.add(os.path.normcase(tgt))
                if copy_files:
                    plan.append((it.path, tgt))
            if copy_files and self.c_prune.isChecked() and os.path.isdir(folder):
                for f in os.listdir(folder):
                    p = os.path.join(folder, f)
                    if os.path.isfile(p) and lib.tagmod.is_audio_file(p) and os.path.normcase(p) not in want:
                        stale.append(p)
        prune = []
        if stale and QMessageBox.question(self, "Export", f"{len(stale)} piese de la destinație nu mai sunt în "
                                          "playlisturi. Le șterg de acolo? (Originalele de pe hard rămân.)") \
                == QMessageBox.Yes:
            prune = stale
        todo = [(s, t) for s, t in plan if not _same(s, t)]
        n_flac = sum(1 for _n, items in pls for i in items if i.path.lower().endswith(".flac"))
        from .jobs import Job

        def work(progress, cancelled):
            errors, done = [], 0
            os.makedirs(dest, exist_ok=True)
            try:
                with open(os.path.join(dest, lib.EXPORT_MARKER), "w", encoding="utf-8") as f:
                    f.write("Export Music Manager — folderul ăsta nu se scanează în Bibliotecă.\n")
            except OSError:
                pass
            for i, (src, tgt) in enumerate(todo):
                if cancelled():
                    break
                try:
                    os.makedirs(os.path.dirname(tgt), exist_ok=True)
                    shutil.copy2(src, tgt)
                    done += 1
                except OSError as e:
                    errors.append(f"{os.path.basename(src)}: {e.strerror or e}")
                progress(i + 1, len(todo), os.path.basename(src))
            removed = 0
            for p in prune:
                try:
                    os.remove(p)
                    removed += 1
                except OSError as e:
                    errors.append(f"{os.path.basename(p)}: {e.strerror or e}")
            return done, removed, errors

        job = Job(f"Export: {len(pls)} playlisturi ({len(todo)} fișiere de copiat)", work)

        def finished(res):
            done, removed, errors = res
            try:
                written = self._write_playlists(dest, pls, copies=copy_files and self.rb_copies.isChecked())
            except OSError as e:
                written = []
                errors.append(f"fișierele de playlist: {e}")
            d = {"dest": dest, "keys": sorted(keys), "n": len(pls), "when": datetime.now().isoformat(timespec="minutes")}
            settings().setValue("export/last", json.dumps(d))
            self._show_last()
            msg = (f"Gata: {len(pls)} playlisturi în {dest}.\n\nCopiate acum: {done} fișiere · deja la zi: "
                   f"{len(plan) - len(todo)}" + (f" · șterse de acolo: {removed}" if removed else "") +
                   (f"\nFișiere de playlist: {', '.join(written)} (în „{PL_DIR}”)" if written else ""))
            if n_flac:
                msg += f"\n\n🏷 FLAC: {n_flac} piese sunt FLAC (unele CDJ-uri, ex. CDJ-350, nu le citesc)."
            if errors:
                msg += f"\n\n{len(errors)} erori:\n" + "\n".join(errors[:12])
            QMessageBox.information(self, "Export", msg)
        job.succeeded.connect(finished)
        job.start()
        self.task = job

    def _write_playlists(self, dest, pls, copies: bool) -> list[str]:
        out_dir = os.path.join(dest, PL_DIR)
        os.makedirs(out_dir, exist_ok=True)
        conv = []
        for name, items in pls:
            folder = os.path.join(dest, lib.safe_name(name) or "Playlist")
            if copies:
                items = [dataclasses.replace(i, track=dataclasses.replace(
                    i.track, path=os.path.join(folder, os.path.basename(i.path)))) for i in items]
            conv.append((name, items))
        written = []
        if self.c_m3u.isChecked():
            for name, items in conv:
                write_m3u(os.path.join(out_dir, (lib.safe_name(name) or "Playlist") + ".m3u8"),
                          [i.track for i in items], relative=True)
            written.append("M3U8")
        if self.c_nml.isChecked():
            for name, items in conv:
                ldb.export_traktor_nml(os.path.join(out_dir, (lib.safe_name(name) or "Playlist") + ".nml"), name, items)
            written.append("Traktor NML")
        if self.c_xml.isChecked():
            ldb.export_rekordbox_xml(os.path.join(out_dir, "rekordbox.xml"), conv)
            written.append("Rekordbox XML")
        return written
