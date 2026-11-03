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
say("skey.json", open(os.path.join(folder, "skey.json"), encoding="utf-8").read()[:700])
for f in ("logmel.onnx", "beat_this.onnx", "skey.onnx", "discogs-effnet-bsdynamic-1.onnx"):
    s = ort.InferenceSession(os.path.join(folder, f), providers=["CPUExecutionProvider"])
    say("IO", f, [(i.name, i.shape) for i in s.get_inputs()], "->", [(o.name, o.shape) for o in s.get_outputs()])
from synth import make
from music_manager.core.cdj import audio as ca
d = tempfile.mkdtemp()
# pistă „click”: kick pe fiecare bătaie de la 500 ms, accent pe 1, bas încet (ca în testele cratedrop)
sr = 22050; n = sr * 120; t = np.arange(n) / sr; y = 0.05 * np.sin(2 * np.pi * 55 * t)
for k in range(int((120 - 0.5) / 0.5)):
    s0 = int((0.5 + k * 0.5) * sr); L = int(0.3 * sr); tt = np.arange(L) / sr
    y[s0:s0 + L] += np.sin(2 * np.pi * np.cumsum(120 * np.exp(-tt * 8) + 45) / sr) * np.exp(-tt * 18)
    if k % 4 == 0:
        y[s0:s0 + L] += 0.3 * np.sin(2 * np.pi * 2500 * tt) * np.exp(-tt * 120)
y = (y / np.abs(y).max() * 0.9).astype(np.float32)
say("CLICK 120 BPM, prima la 500 ms, downbeat pe ea -> beat_this:", ml.grid(y, folder),
    "| al nostru:", ca.first_beat(y, 120.0))
for bpm, pad in ((124, 0.337), (128, 1.0), (174, 0.2)):
    p = os.path.join(d, "s.wav"); make(p, bpm=bpm, dur=120, brk=(50, 60))
    x = an.decode(p, sr=22050); x = np.concatenate([np.zeros(int(pad * 22050), np.float32), x])
    t = time.time(); g = ml.grid(x, folder)
    say("SYNTH key (Am așteptat)", ml.key(x, folder), an.estimate_key(an.decode(p))[0])
    true = (pad % (60 / bpm)) * 1000
    say("SYNTH", bpm, "pad", pad, "->", g, "adevărat first_ms %.1f" % true, "| al nostru:", ca.first_beat(x, bpm),
        round(time.time() - t, 1), "s")
for p in sorted(glob.glob(sys.argv[1] + "/*.hq.ogg")):
    name = os.path.basename(p)[:40]
    x = an.decode(p, sr=22050)
    if len(x) < 22050 * 20:
        continue
    t = time.time(); g = ml.grid(x, folder); t1 = time.time()
    mine = ca.first_beat(x, g["bpm"]) if g else None
    ours = an.estimate_bpm(an.decode(p))[0]
    k_new, k_old = ml.key(x, folder), an.estimate_key(an.decode(p))[0]
    x16 = an.decode(p, sr=16000); gs = ml.genres(x16, folder, top=3); t2 = time.time()
    say("REAL", name, "| beat_this:", g, "| bpm vechi:", ours, "| first al nostru:", mine, "| key:", k_new, "vechi", k_old, "|", round(t1 - t, 1), "s | gen:",
        [(a, b, round(c, 2)) for a, b, c in gs], round(t2 - t1, 1), "s")
json.dump(out, open("ml_report.json", "w"))
# GitHub arată doar 10 adnotări pe pas: le grupăm
text = "\n".join(out)
chunks = [text[i:i + 3500] for i in range(0, len(text), 3500)][:9]
for i, c in enumerate(chunks):
    print(f"::warning title=R{i}::" + c.replace("\n", "%0A"))
