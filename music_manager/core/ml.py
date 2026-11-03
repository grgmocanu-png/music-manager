"""Analize cu modele neuronale, descărcate la cerere din Setări (nu sunt în installer).

- BPM și beatgrid: Beat This! (CPJKU, MIT), conversia ONNX tuneperfect/beat-this-onnx (logmel.onnx + beat_this.onnx).
- Genul / stilul din sunet: Discogs-EffNet (MTG, CC BY-NC-SA 4.0 — doar uz necomercial), 400 de stiluri Discogs.

Rulează cu onnxruntime (inclus în aplicație). Fără modele descărcate, totul merge ca înainte.
"""
from __future__ import annotations

import json
import os
import threading

import numpy as np

MODELS = {
    "beats": {
        "title": "BPM și beatgrid (Beat This!)",
        "files": {
            "logmel.onnx": "https://huggingface.co/tuneperfect/beat-this-onnx/resolve/main/logmel.onnx",
            "beat_this.onnx": "https://huggingface.co/tuneperfect/beat-this-onnx/resolve/main/beat_this.onnx",
        },
        "license": "MIT (Beat This!, Institute of Computational Perception, JKU Linz)",
    },
    "genre": {
        "title": "Gen și stil din sunet (Discogs-EffNet)",
        "files": {
            "discogs-effnet-bsdynamic-1.onnx": [
                "https://essentia.upf.edu/models/feature-extractors/discogs-effnet/discogs-effnet-bsdynamic-1.onnx",
                "https://huggingface.co/Heyian/discogs-effnet-onnx/resolve/main/discogs-effnet-bsdynamic-1.onnx"],
            "genre_discogs400-discogs-effnet-1.json": [
                "https://essentia.upf.edu/models/classification-heads/genre_discogs400/"
                "genre_discogs400-discogs-effnet-1.json",
                "https://huggingface.co/Heyian/discogs-effnet-onnx/resolve/main/genre_discogs400-discogs-effnet-1.json"],
        },
        "license": "CC BY-NC-SA 4.0 (Music Technology Group, UPF) — doar pentru uz personal, necomercial",
    },
}

_lock = threading.Lock()
_sessions: dict = {}


def models_dir() -> str:
    try:
        from PySide6.QtCore import QStandardPaths
        base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    except Exception:  # noqa: BLE001
        base = os.path.expanduser("~/.music-manager")
    return os.path.join(base, "modele")


def onnx_ok() -> bool:
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def installed(name: str, folder: str = "") -> bool:
    folder = folder or models_dir()
    return onnx_ok() and all(os.path.isfile(os.path.join(folder, f)) and os.path.getsize(os.path.join(folder, f)) > 0
                             for f in MODELS[name]["files"])


def download(name: str, folder: str = "", progress=None, http_open=None) -> None:
    """Descarcă fișierele unui model (cu rezervă dacă prima sursă nu merge). progress(octeți, total, fișier)."""
    import ssl
    import urllib.request
    folder = folder or models_dir()
    os.makedirs(folder, exist_ok=True)
    if http_open is None:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())

        def http_open(url):
            return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "MusicManager"}),
                                          timeout=60, context=ctx)
    for fname, urls in MODELS[name]["files"].items():
        dest = os.path.join(folder, fname)
        if os.path.isfile(dest) and os.path.getsize(dest) > 0:
            continue
        last = None
        for url in ([urls] if isinstance(urls, str) else urls):
            try:
                with http_open(url) as r:
                    total = int(r.headers.get("Content-Length") or 0)
                    tmp = dest + ".partial"
                    got = 0
                    with open(tmp, "wb") as f:
                        while True:
                            b = r.read(1 << 20)
                            if not b:
                                break
                            f.write(b)
                            got += len(b)
                            if progress:
                                progress(got, total, fname)
                os.replace(tmp, dest)
                last = None
                break
            except Exception as e:  # noqa: BLE001
                last = e
        if last is not None:
            raise RuntimeError(f"Nu am putut descărca {fname}: {last}")


def remove(name: str, folder: str = "") -> None:
    folder = folder or models_dir()
    for f in MODELS[name]["files"]:
        try:
            os.remove(os.path.join(folder, f))
        except OSError:
            pass


def _session(path: str):
    with _lock:
        s = _sessions.get(path)
        if s is None:
            import onnxruntime as ort
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            opts.inter_op_num_threads = 1
            s = ort.InferenceSession(path, opts, providers=["CPUExecutionProvider"])
            _sessions[path] = s
        return s


# --- Beat This! -------------------------------------------------------------------------------------
BEAT_SR = 22050
BEAT_FPS = 50
CHUNK, BORDER = 1500, 6


def beat_logits(x: np.ndarray, folder: str = "") -> tuple[np.ndarray, np.ndarray]:
    """x: mono la 22 050 Hz. Întoarce (logits bătăi, logits downbeat), câte unul pe cadru (50 pe secundă)."""
    folder = folder or models_dir()
    mel = _session(os.path.join(folder, "logmel.onnx"))
    spect = mel.run(None, {mel.get_inputs()[0].name: x.astype(np.float32)})[0]
    if spect.ndim == 3:
        spect = spect[0]
    model = _session(os.path.join(folder, "beat_this.onnx"))
    names = [o.name for o in model.get_outputs()]
    n = len(spect)
    # ca în Beat This! (split_predict_aggregate): bucăți de 1500 cadre, margini de 6, „keep first”
    step = CHUNK - 2 * BORDER
    starts = list(range(-BORDER, max(1, n - BORDER), step))
    if n > step:
        starts[-1] = n - (CHUNK - BORDER)
    beat = np.full(n, -1000.0, np.float32)
    down = np.full(n, -1000.0, np.float32)
    for start in reversed(starts):
        a0, b0 = max(start, 0), min(start + CHUNK, n)
        piece = np.pad(spect[a0:b0], ((max(0, -start), max(0, min(BORDER, start + CHUNK - n))), (0, 0)))
        out = model.run(None, {model.get_inputs()[0].name: piece[None].astype(np.float32)})
        res = dict(zip(names, out))
        bl = np.asarray(res.get("beat", out[0])).reshape(-1)
        dl = np.asarray(res.get("downbeat", out[1])).reshape(-1)
        lo, hi = start + BORDER, min(n, start + len(bl) - BORDER)
        if hi > lo:
            beat[lo:hi] = bl[BORDER:BORDER + hi - lo]
            down[lo:hi] = dl[BORDER:BORDER + hi - lo]
    return beat, down


def _peaks(logits: np.ndarray) -> np.ndarray:
    """Cadrele unde logit > 0 și e maximul pe ±3 cadre; vârfurile lipite se unesc (media)."""
    n = len(logits)
    if n == 0:
        return np.array([])
    pad = np.pad(logits, 3, constant_values=-1e9)
    win = np.lib.stride_tricks.sliding_window_view(pad, 7).max(axis=1)
    idx = np.flatnonzero((logits > 0) & (logits >= win))
    if len(idx) == 0:
        return np.array([])
    groups, cur = [], [idx[0]]
    for i in idx[1:]:
        if i - cur[-1] <= 1:
            cur.append(i)
        else:
            groups.append(np.mean(cur))
            cur = [i]
    groups.append(np.mean(cur))
    return np.array(groups) / BEAT_FPS


def beats(x: np.ndarray, folder: str = "") -> tuple[np.ndarray, np.ndarray]:
    """(bătăi în secunde, downbeat-uri în secunde, lipite de cea mai apropiată bătaie)."""
    bl, dl = beat_logits(x, folder)
    b = _peaks(bl)
    d = _peaks(dl)
    if len(b) and len(d):
        d = np.unique(b[np.abs(b[None, :] - d[:, None]).argmin(axis=1)])
    return b, d


def grid(x: np.ndarray, folder: str = "", lo: float = 70, hi: float = 180) -> dict | None:
    """Grilă constantă din bătăile modelului: {bpm, first_ms, in_bar, confidence}. None dacă nu găsește bătăi."""
    b, d = beats(x, folder)
    if len(b) < 16:
        return None
    iv = np.diff(b)
    med = float(np.median(iv))
    if med <= 0:
        return None
    # potrivire liniară: timpul bătăii k = t0 + k·perioadă (pe bătăile care respectă tempo-ul median)
    k = np.concatenate([[0], np.cumsum(np.maximum(1, np.round(iv / med)))]).astype(float)
    period, t0 = np.polyfit(k, b, 1)
    bpm = 60.0 / period
    while bpm < lo:
        bpm *= 2
    while bpm > hi:
        bpm /= 2
    if abs(bpm - round(bpm)) < 0.05:
        bpm = float(round(bpm))
    period = 60.0 / bpm
    resid = b - (t0 + np.round((b - t0) / period) * period)
    t0 = t0 + float(np.median(resid))
    first = t0 % period
    in_bar = 1
    if len(d):
        # cea mai frecventă poziție a downbeat-urilor în grila de 4
        pos = np.round((d - first) / period).astype(int) % 4
        j = int(np.bincount(pos, minlength=4).argmax())
        in_bar = ((-j) % 4) + 1
    conf = float(np.mean(np.abs(resid) < 0.03))
    return {"bpm": round(bpm, 2), "first_ms": round(first * 1000.0, 1), "in_bar": in_bar, "confidence": conf}


# --- gen / stil (Discogs-EffNet) ---------------------------------------------------------------------
GENRE_SR = 16000


def _mel_filters(sr=GENRE_SR, n_fft=512, n_mels=96, fmin=0.0, fmax=8000.0) -> np.ndarray:
    """Filtre mel „slaney”, normalizate pe arie (ca MelBands din Essentia: slaneyMel, unit_tri)."""
    def hz2mel(f):
        f = np.asarray(f, float)
        lin = f / (200.0 / 3)
        log = 15.0 + np.log(np.maximum(f, 1e-9) / 1000.0) / (np.log(6.4) / 27.0)
        return np.where(f >= 1000.0, log, lin)

    def mel2hz(m):
        m = np.asarray(m, float)
        lin = m * (200.0 / 3)
        log = 1000.0 * np.exp((np.log(6.4) / 27.0) * (m - 15.0))
        return np.where(m >= 15.0, log, lin)

    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    pts = mel2hz(np.linspace(hz2mel(fmin), hz2mel(fmax), n_mels + 2))
    fb = np.zeros((n_mels, len(freqs)))
    for i in range(n_mels):
        l, c, r = pts[i], pts[i + 1], pts[i + 2]
        up = (freqs - l) / max(c - l, 1e-9)
        down = (r - freqs) / max(r - c, 1e-9)
        fb[i] = np.maximum(0, np.minimum(up, down))
        fb[i] *= 2.0 / max(r - l, 1e-9)            # arie egală (unit_tri)
    return fb.astype(np.float32)


def genre_input(x16: np.ndarray) -> np.ndarray:
    """Spectrograma mel pentru EffNet: 16 kHz, fereastră 512, pas 256, 96 benzi, log10(1 + 10000·x),
    în bucăți de 128 cadre cu pasul 62 → [n, 128, 96]."""
    n_fft, hop = 512, 256
    win = np.hanning(n_fft + 1)[:-1].astype(np.float32)
    n = 1 + max(0, (len(x16) - n_fft) // hop)
    fb = _mel_filters()
    out = np.empty((n, 96), np.float32)
    for s in range(0, n, 8192):
        e = min(n, s + 8192)
        idx = np.arange(s, e)[:, None] * hop + np.arange(n_fft)[None, :]
        power = np.abs(np.fft.rfft(x16[idx] * win, axis=1)) ** 2
        out[s:e] = power @ fb.T
    mel = np.log10(1.0 + 10000.0 * out)
    if len(mel) < 128:
        mel = np.pad(mel, ((0, 128 - len(mel)), (0, 0)))
    starts = range(0, len(mel) - 128 + 1, 62)
    return np.stack([mel[s:s + 128] for s in starts]).astype(np.float32)


def genre_labels(folder: str = "") -> list[str]:
    folder = folder or models_dir()
    with open(os.path.join(folder, "genre_discogs400-discogs-effnet-1.json"), encoding="utf-8") as f:
        return json.load(f)["classes"]


def genres(x16: np.ndarray, folder: str = "", top: int = 5, max_patches: int = 64) -> list[tuple[str, str, float]]:
    """[(gen, stil, probabilitate)] — media pe toată piesa. Ex. („Electronic”, „Deep House”, 0,62)."""
    folder = folder or models_dir()
    patches = genre_input(x16)
    if len(patches) > max_patches:                    # piese lungi: bucăți răspândite uniform
        patches = patches[np.linspace(0, len(patches) - 1, max_patches).astype(int)]
    s = _session(os.path.join(folder, "discogs-effnet-bsdynamic-1.onnx"))
    outs = s.run(None, {s.get_inputs()[0].name: patches})
    names = [o.name for o in s.get_outputs()]
    act = None
    for name, o in zip(names, outs):
        if o.ndim == 2 and o.shape[1] == 400:
            act = o
            break
    if act is None:
        raise RuntimeError(f"modelul de gen nu are ieșirea așteptată ({[o.shape for o in outs]})")
    mean = act.mean(axis=0)
    labels = genre_labels(folder)
    order = np.argsort(mean)[::-1][:top]
    res = []
    for i in order:
        g, _sep, st = labels[i].partition("---")
        res.append((g, st or g, float(mean[i])))
    return res
