import numpy as np, wave, sys
def make(path, bpm=128, key_notes=(57,60,64), bass_root=45, dur=150, brk=(60,90), sr=22050):
    t = np.arange(int(dur*sr))/sr
    y = np.zeros_like(t)
    beat = 60/bpm
    def hz(m): return 440*2**((m-69)/12)
    # pad chord always
    for m in key_notes:
        y += 0.08*np.sin(2*np.pi*hz(m)*t) + 0.03*np.sin(2*np.pi*hz(m+12)*t)
    inbrk = (t>=brk[0])&(t<brk[1])
    # kick + bass + hats outside break
    nb = int(dur/beat)
    for i in range(nb):
        s = i*beat
        if brk[0] <= s < brk[1]: continue
        i0 = int(s*sr); L = int(0.25*sr)
        tt = np.arange(L)/sr
        kick = 0.9*np.sin(2*np.pi*(50+80*np.exp(-tt*30))*tt)*np.exp(-tt*12)
        y[i0:i0+L] += kick[:len(y[i0:i0+L])]
        # bass on offbeat
        j0 = int((s+beat/2)*sr); L2=int(beat/2*sr*0.9); tt2=np.arange(L2)/sr
        note = bass_root if (i//4)%2==0 else bass_root+7
        b = 0.35*np.sin(2*np.pi*hz(note)*tt2)*np.exp(-tt2*3)
        y[j0:j0+L2] += b[:len(y[j0:j0+L2])]
        # hat
        h0 = int((s+beat/2)*sr); Lh=int(0.03*sr)
        y[h0:h0+Lh] += 0.1*np.random.randn(len(y[h0:h0+Lh]))
    y /= np.abs(y).max()*1.1
    with wave.open(path,'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((y*32767).astype(np.int16).tobytes())
