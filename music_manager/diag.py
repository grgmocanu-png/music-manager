"""Diagnostic pentru „Analizează” din Explore (rulează doar cu MM_DIAG_ANALYZE=<folder>).
Încarcă piesele din folder în Explore, apasă Analizează și scrie în MM_DIAG_OUT ce s-a întâmplat:
câte piese s-au analizat, cât a stat fereastra fără să răspundă și unde."""
from __future__ import annotations

import collections
import json
import os
import sys
import threading
import time
import traceback


def run(app, win, folder: str, out: str, limit_s: float = 240) -> int:
    from PySide6.QtCore import QTimer
    from .core import library as lib
    from .core import mixability as mx
    from .ui import explore as exm
    from .ui.jobs import manager

    rep = {"version": __import__("music_manager").__version__, "frozen": bool(getattr(sys, "frozen", False)),
           "events": []}
    gaps, last = [], [time.perf_counter()]
    stacks = collections.Counter()
    main_id = threading.get_ident()
    stop = [False]

    def beat():
        now = time.perf_counter()
        gaps.append(now - last[0])
        last[0] = now

    def sampler():
        while not stop[0]:
            time.sleep(0.05)
            if time.perf_counter() - last[0] > 0.25:
                fr = sys._current_frames().get(main_id)
                st = traceback.extract_stack(fr)[-7:] if fr else []
                stacks[" <- ".join(f"{os.path.basename(x.filename)}:{x.lineno}:{x.name}" for x in reversed(st))] += 1

    def save():
        stop[0] = True
        rep["max_gap_ms"] = round(max(gaps) * 1000) if gaps else None
        rep["frozen_s"] = round(sum(g for g in gaps if g > 0.25), 1)
        rep["gaps_over_250ms"] = sum(g > 0.25 for g in gaps)
        rep["top_gaps_ms"] = [round(g * 1000) for g in sorted(gaps)[-6:]]
        rep["stacks"] = stacks.most_common(8)
        rep["pool_error"] = getattr(mx, "LAST_POOL_ERROR", "n/a")
        rep["jobs"] = [(j.name, j.status, j.done, j.total, j.error) for j in manager().jobs]
        with open(out, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)

    try:
        win._go_name("explore")
        ex = win.explore
        files = sorted(lib.find_audio_files([folder]))
        rows = [exm.Row(lib.track_from_file(p)) for p in files]
        for i in range(int(os.environ.get("MM_DIAG_PAD", "0"))):     # bibliotecă mare: rânduri în plus
            rows.append(exm.Row(lib.Track(path=os.path.join(folder, "_lipsa", f"{i:05d}.mp3"), artist=f"A{i % 400}",
                                          title=f"T{i}", genre="House", bpm=str(120 + i % 12)),
                                bpm=120.0 + i % 12, key="8A", missing=True))
        ex.model.set_rows(rows)
        app.processEvents()
        rep["rows"] = len(rows)
        rep["visible"] = ex.proxy.rowCount()
        hb = QTimer()
        hb.timeout.connect(beat)
        hb.start(20)
        threading.Thread(target=sampler, daemon=True).start()
        t0 = time.perf_counter()
        ex.analyze_visible()
        rep["events"].append(f"clicked; status={ex.status.text()}")
        while time.perf_counter() - t0 < limit_s:
            app.processEvents()
            time.sleep(0.005)
            if not [j for j in manager().jobs if j.isRunning()]:
                break
        for _ in range(100):
            app.processEvents()
            time.sleep(0.01)
        rep["seconds"] = round(time.perf_counter() - t0, 1)
        rep["analyzed"] = sum(1 for r in rows if r.mix is not None)
        rep["errors"] = [r.mix.error for r in rows if r.mix is not None and r.mix.error][:5]
        rep["cached"] = sum(1 for p in files if mx.load_cached(p, exm.mix_dir()) is not None)
        rep["status"] = ex.status.text()
    except Exception:  # noqa: BLE001
        rep["exception"] = traceback.format_exc()
    save()
    return 0
