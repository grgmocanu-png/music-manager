"""Piese de test: N fișiere mp3 de 4-7 minute (beat + bas + acorduri), făcute cu ffmpeg."""
import os, subprocess, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from synth import make
import imageio_ffmpeg
out, n = sys.argv[1], int(sys.argv[2])
os.makedirs(out, exist_ok=True)
ff = imageio_ffmpeg.get_ffmpeg_exe()
for i in range(n):
    w = os.path.join(out, f"tmp{i}.wav")
    dur = 240 + (i * 37) % 180
    make(w, bpm=118 + i % 14, dur=dur, brk=(dur * 0.4, dur * 0.5))
    subprocess.run([ff, "-v", "error", "-y", "-i", w, "-b:a", "320k", os.path.join(out, f"Artist {i} - Track {i}.mp3")], check=True)
    os.remove(w)
print("ok", n)
