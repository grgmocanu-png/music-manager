"""Verificare pe GitHub: modelele se descarcă, au intrările așteptate și dau rezultate plauzibile."""
import glob, os, sys, tempfile, time, json
sys.path.insert(0, os.getcwd()); sys.path.insert(0, os.path.join(os.getcwd(), "tests"))
import numpy as np
from music_manager.core import ml, analysis as an
out = []
def say(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); out.append(s)
folder = os.path.join(tempfile.gettempdir(), "modele")
for name in ("beats", "genre"):
    t = time.time(); ml.download(name, folder); say(name, "descărcat în", round(time.time() - t, 1), "s:",
        {f: os.path.getsize(os.path.join(folder, f)) for f in ml.MODELS[name]["files"]})
import onnxruntime as ort
for f in ("logmel.onnx", "beat_this.onnx", "discogs-effnet-bsdynamic-1.onnx"):
    s = ort.InferenceSession(os.path.join(folder, f), providers=["CPUExecutionProvider"])
    say("IO", f, [(i.name, i.shape) for i in s.get_inputs()], "->", [(o.name, o.shape) for o in s.get_outputs()])
from synth import make
d = tempfile.mkdtemp()
for bpm, pad in ((124, 0.337), (128, 1.0), (174, 0.2)):
    p = os.path.join(d, "s.wav"); make(p, bpm=bpm, dur=120, brk=(50, 60))
    x = an.decode(p, sr=22050); x = np.concatenate([np.zeros(int(pad * 22050), np.float32), x])
    t = time.time(); g = ml.grid(x, folder)
    true = (pad % (60 / bpm)) * 1000
    say("SYNTH", bpm, "pad", pad, "->", g, "adevărat first_ms %.1f" % true, round(time.time() - t, 1), "s")
for p in sorted(glob.glob(sys.argv[1] + "/*.hq.ogg")):
    name = os.path.basename(p)[:40]
    x = an.decode(p, sr=22050)
    if len(x) < 22050 * 20:
        continue
    t = time.time(); g = ml.grid(x, folder); t1 = time.time()
    ours = an.estimate_bpm(an.decode(p))[0]
    x16 = an.decode(p, sr=16000); gs = ml.genres(x16, folder, top=3); t2 = time.time()
    say("REAL", name, "| beat_this:", g, "| bpm vechi:", ours, "|", round(t1 - t, 1), "s | gen:",
        [(a, b, round(c, 2)) for a, b, c in gs], round(t2 - t1, 1), "s")
json.dump(out, open("ml_report.json", "w"))
