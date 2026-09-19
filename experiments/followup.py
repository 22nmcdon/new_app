"""Two follow-ups:
(1) Why did the -12dB/oct 'dull mic' case bias by +16.7c, and does a DC-block fix it?
(2) Onset timing under degradation -- the part I expect to actually break.
"""
import numpy as np
exec(open('mic_robustness.py').read().split('# ---------- run ----------')[0])

print("=" * 78)
print("(1) THE TILT OUTLIER: is it low-frequency junk, and does a DC-block fix it?")
print("=" * 78)
f0 = 146.83
variants = [
    ("tilt -12dB/oct (as tested)",        lambda x: tilt_db_per_oct(x, -12)),
    ("  ^ same, HPF 40Hz before YIN",     lambda x: highpass(tilt_db_per_oct(x, -12), 40)),
    ("dull mic as LPF 2kHz (realistic)",  lambda x: lowpass(x, 2000)),
    ("dull mic as LPF 800Hz (very dull)", lambda x: lowpass(x, 800)),
]
for label, fn in variants:
    c = track(fn(tone(f0)), f0)
    print(f"  {label:<36} median {np.median(c):+6.1f}c   IQR {np.percentile(c,75)-np.percentile(c,25):.1f}c")

print()
print("=" * 78)
print("(2) ONSET TIMING: deviation from known onset times, in milliseconds")
print("=" * 78)

def note_sequence(bpm=90, n=8, f0=293.66):
    """n notes on a strict grid; returns signal + true onset sample indices."""
    spb = 60.0 / bpm
    total = int((n + 1) * spb * SR)
    x = np.zeros(total)
    onsets = []
    for i in range(n):
        s = int(i * spb * SR)
        note = tone(f0 * (2 ** ((i % 5) / 12)), secs=spb * 0.9)
        # sharpen the attack -- a real articulated note, not a fade-in
        a = int(0.005 * SR)
        note[:a] *= np.linspace(0, 1, a) ** 0.3
        x[s:s + len(note)] += note
        onsets.append(s)
    return x / np.max(np.abs(x)) * 0.5, np.array(onsets)

def detect_onsets(x, win=1024, hop=256):
    """Spectral flux + adaptive median threshold + peak pick."""
    frames = [np.abs(np.fft.rfft(x[i:i+win] * np.hanning(win)))
              for i in range(0, len(x) - win, hop)]
    S = np.array(frames)
    flux = np.sum(np.maximum(np.diff(S, axis=0), 0), axis=1)
    if flux.max() > 0: flux = flux / flux.max()
    # adaptive threshold over a ~200ms median window
    w = max(3, int(0.2 * SR / hop))
    pad = np.pad(flux, w, mode='edge')
    thr = np.array([np.median(pad[i:i+2*w+1]) for i in range(len(flux))]) + 0.08
    peaks = []
    for i in range(1, len(flux) - 1):
        if flux[i] > thr[i] and flux[i] >= flux[i-1] and flux[i] > flux[i+1]:
            if not peaks or (i - peaks[-1]) * hop > 0.08 * SR:
                peaks.append(i)
    return np.array(peaks) * hop + win // 2

def onset_error_ms(x, true_onsets):
    det = detect_onsets(x)
    if len(det) == 0:
        return None, 0
    errs = []
    for t in true_onsets:
        errs.append((det[np.argmin(np.abs(det - t))] - t) / SR * 1000)
    errs = np.array(errs)
    # a constant offset is calibratable; jitter around it is not
    return errs, len(det)

CASES = [
    ("clean reference",             lambda x: x),
    ("laptop mic HPF 150Hz",        lambda x: highpass(x, 150)),
    ("noisy room SNR 10dB",         lambda x: add_noise(x, 10)),
    ("clipping / overdriven",       lambda x: soft_clip(x, 8)),
    ("small room RT60 0.4s",        lambda x: reverb(x, 0.4, 0.35)),
    ("live room RT60 1.2s",         lambda x: reverb(x, 1.2, 0.5)),
    ("very live RT60 2.0s",         lambda x: reverb(x, 2.0, 0.6)),
    ("Bluetooth HFP narrowband",    to_narrowband),
    ("worst laptop combo",          lambda x: add_noise(soft_clip(highpass(x,150),4), 14)),
]
sig, true_on = note_sequence()
print(f"  {'condition':<30}{'median offset':>15}{'jitter (IQR)':>15}{'notes found':>14}")
print("  " + "-" * 74)
for label, fn in CASES:
    errs, n = onset_error_ms(fn(sig), true_on)
    if errs is None:
        print(f"  {label:<30}{'NO ONSETS':>15}"); continue
    med = np.median(errs)
    iqr = np.percentile(errs, 75) - np.percentile(errs, 25)
    flag = "  <-- fails 40ms tolerance" if iqr > 40 else ""
    print(f"  {label:<30}{med:>+13.1f}ms{iqr:>13.1f}ms{n:>10}/{len(true_on)}{flag}")
