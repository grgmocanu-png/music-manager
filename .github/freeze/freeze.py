"""Măsoară cât stă interfața fără să răspundă când apeși Analizează în Explore."""
import os, sys, time, tempfile, json
repo, n_rows, n_real = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
sys.path.insert(0, repo); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["QT_QPA_PLATFORM"] = "offscreen"
if __name__ == "__main__":
    from PySide6.QtCore import QCoreApplication, QStandardPaths, QTimer, Qt
    from PySide6.QtWidgets import QApplication, QMessageBox
    QStandardPaths.setTestModeEnabled(True)
    QCoreApplication.setOrganizationName("MMFreeze"); QCoreApplication.setApplicationName("MMFreeze" + str(os.getpid()))
    app = QApplication([])
    QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
    from synth import make
    from music_manager.core.library import Track
    from music_manager.ui.main_window import MainWindow
    from music_manager.ui import explore as exm
    tmp = tempfile.mkdtemp()
    win = MainWindow(); win.show(); win._go_name("explore")
    t_end = time.time() + 1
    while time.time() < t_end: app.processEvents(); time.sleep(0.01)
    ex = win.explore
    rows = []
    for i in range(n_rows):
        p = os.path.join(tmp, f"{i:05d}.wav")
        if i < n_real:
            make(p, bpm=120 + i, dur=120, brk=(50, 70))
        rows.append(exm.Row(Track(path=p, artist=f"Artist {i%300}", title=f"Track {i}", genre="House",
                                  bpm=str(120 + i % 10)), family="House", bpm=120.0 + i % 10, key="8A",
                            missing=i >= n_real))
    ex.model.set_rows(rows)
    ex.table.sortByColumn(next(i for i, c in enumerate(exm.COLS) if c[1] == "mix"), Qt.DescendingOrder)
    app.processEvents()
    import threading, traceback, collections
    timing = collections.defaultdict(list)
    def wrap(name):
        f = getattr(ex, name, None)
        if f is None: return
        def w(*a):
            t = time.perf_counter(); f(*a); timing[name].append(time.perf_counter() - t)
        setattr(ex, name, w)
    for nm in ("_analyzed_one", "_flush_mix", "_analysis_done"): wrap(nm)
    main_id = threading.get_ident()
    stacks = collections.Counter()
    stop = [False]
    def sampler():
        while not stop[0]:
            time.sleep(0.05)
            if time.perf_counter() - last[0] > 0.2:
                fr = sys._current_frames().get(main_id)
                st = traceback.extract_stack(fr)[-6:] if fr else []
                key = " <- ".join(f"{os.path.basename(x.filename)}:{x.name}" for x in reversed(st) if "freeze.py" not in x.filename)
                stacks[key] += 1
    gaps, last = [], [time.perf_counter()]
    threading.Thread(target=sampler, daemon=True).start()
    def beat():
        now = time.perf_counter(); gaps.append(now - last[0]); last[0] = now
    hb = QTimer(); hb.timeout.connect(beat); hb.start(20)
    t0 = time.perf_counter()
    ex.analyze_rows([r for r in rows if not r.missing])
    from music_manager.ui.jobs import manager
    while True:
        app.processEvents(); time.sleep(0.005)
        if all(r.mix is not None for r in rows[:n_real]) and not [j for j in manager().jobs if j.status in ("running", "queued")]:
            break
        if time.perf_counter() - t0 > 900: print("TIMEOUT"); break
    for _ in range(50): app.processEvents(); time.sleep(0.01)
    dur = time.perf_counter() - t0
    big = sorted(gaps)[-5:]
    stop[0] = True
    print("TIMING", {k: (len(v), round(max(v)*1000), round(sum(v), 2)) for k, v in timing.items()})
    for k, c in stacks.most_common(6): print("STACK", c, k)
    print(json.dumps({"total_s": round(dur, 1), "max_gap_ms": round(max(gaps)*1000), "top5_ms": [round(g*1000) for g in big],
                      "gaps_over_250ms": sum(g > 0.25 for g in gaps), "frozen_s": round(sum(g for g in gaps if g > 0.25), 1)}))
    sys.stdout.flush(); sys.stderr.flush()
    os._exit(0)
