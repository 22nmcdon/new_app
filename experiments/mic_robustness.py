"""Does mic variability actually corrupt f0 estimation?

Synthesize harmonic-rich instrument-like tones at known f0, push them through
degradations that model real microphone chains, run YIN, measure cents error.
Ground truth is exact, so any error is the pipeline's fault.
"""
import numpy as np

SR = 44100
rng = np.random.default_rng(7)

# ---------- signal ----------
def tone(f0, secs=2.0, tilt=1.2, sr=SR):
    """Harmonic series with 1/n^tilt rolloff -- stands in for a bowed/blown note."""
    t = np.arange(int(secs * sr)) / sr
    x = np.zeros_like(t)
    n = 1
    while f0 * n < sr / 2 * 0.95:
        x += (1.0 / n**tilt) * np.sin(2 * np.pi * f0 * n * t + rng.uniform(0, 2*np.pi))
        n += 1
    # gentle attack/release so frames aren't all identical
    env = np.ones_like(x)
    a = int(0.03 * sr)
    env[:a] = np.linspace(0, 1, a); env[-a:] = np.linspace(1, 0, a)
    return x / np.max(np.abs(x)) * 0.5 * env

# ---------- degradations (FFT-domain filters; no scipy needed) ----------
def _spec_filter(x, gain_fn):
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return np.fft.irfft(X * gain_fn(f), n=len(x))

def highpass(x, fc, order=2):
    return _spec_filter(x, lambda f: 1 / np.sqrt(1 + (fc / np.maximum(f, 1e-9))**(2*order)))

def lowpass(x, fc, order=4):
    return _spec_filter(x, lambda f: 1 / np.sqrt(1 + (f / fc)**(2*order)))

def bandpass(x, lo, hi):
    return lowpass(highpass(x, lo), hi)

def tilt_db_per_oct(x, db):
    ref = 1000.0
    return _spec_filter(x, lambda f: 10 ** (db * np.log2(np.maximum(f, 20) / ref) / 20))

def add_noise(x, snr_db):
    p = np.mean(x**2)
    n = rng.normal(0, np.sqrt(p / (10 ** (snr_db / 10))), len(x))
    return x + n

def soft_clip(x, drive=6.0):
    return np.tanh(x * drive) / np.tanh(drive)

def agc_pump(x, depth=0.6, rate=3.0):
    t = np.arange(len(x)) / SR
    return x * (1 - depth * (0.5 + 0.5 * np.sin(2 * np.pi * rate * t)))

def reverb(x, rt60=1.2, wet=0.5):
    n = int(rt60 * SR)
    ir = rng.normal(0, 1, n) * np.exp(-6.9 * np.arange(n) / n)
    ir[0] = 1.0
    y = np.convolve(x, ir / np.max(np.abs(ir)))[:len(x)]
    return (1 - wet) * x + wet * y / np.max(np.abs(y)) * np.max(np.abs(x))

def to_narrowband(x):
    """Bluetooth HFP: band-limit to ~3.4k, decimate to 16k, come back up."""
    y = bandpass(x, 300, 3400)
    d = SR // 16000  # ~2.75 -> use integer-ish path
    idx = np.arange(0, len(y), SR / 16000)
    y16 = np.interp(idx, np.arange(len(y)), y)
    return np.interp(np.arange(len(y)), idx, y16)

# ---------- YIN ----------
def yin_f0(frame, sr=SR, fmin=50, fmax=1600, thresh=0.10):
    N = len(frame)
    tau_max = min(N // 2, int(sr / fmin))
    tau_min = max(2, int(sr / fmax))
    # difference function via autocorrelation
    x = frame - np.mean(frame)
    p2 = 1 << (2 * N - 1).bit_length()
    X = np.fft.rfft(x, p2)
    acf = np.fft.irfft(X * np.conj(X), p2)[:tau_max + 1]
    cs = np.concatenate(([0.0], np.cumsum(x**2)))
    power = cs[N] - cs[:tau_max + 1][::-1] * 0  # placeholder, computed below
    d = np.empty(tau_max + 1)
    for tau in range(tau_max + 1):
        d[tau] = (cs[N - tau] - cs[0]) + (cs[N] - cs[tau]) - 2 * acf[tau]
    # cumulative mean normalized difference
    dp = np.ones_like(d)
    running = np.cumsum(d[1:])
    dp[1:] = d[1:] * np.arange(1, tau_max + 1) / np.maximum(running, 1e-12)
    # first local minimum below threshold, else global min
    tau = -1
    for t in range(tau_min, tau_max):
        if dp[t] < thresh and dp[t] <= dp[t + 1] and dp[t] <= dp[t - 1]:
            tau = t
            break
    if tau < 0:
        tau = int(np.argmin(dp[tau_min:tau_max])) + tau_min
    # parabolic interpolation
    if 1 <= tau < tau_max - 1:
        a, b, c = dp[tau - 1], dp[tau], dp[tau + 1]
        denom = 2 * (2 * b - a - c)
        if abs(denom) > 1e-12:
            tau = tau + (c - a) / denom
    return sr / tau if tau > 0 else 0.0

def track(x, f0_true, win=2048, hop=512):
    if f0_true < 120:
        win = 8192          # long window for low notes, as the plan specifies
    cents = []
    for i in range(0, len(x) - win, hop):
        f = yin_f0(x[i:i + win] * np.hanning(win))
        if f > 0:
            cents.append(1200 * np.log2(f / f0_true))
    return np.array(cents)

# ---------- run ----------
NOTES = [("C2 cello/bass low", 65.41), ("D3 viola/tenor", 146.83),
         ("D4 mid", 293.66), ("A5 upper", 880.00)]

CASES = [
    ("clean reference",              lambda x: x),
    ("laptop mic HPF 150Hz",         lambda x: highpass(x, 150)),
    ("aggressive HPF 300Hz",         lambda x: highpass(x, 300)),
    ("dull mic, -12dB/oct tilt",     lambda x: tilt_db_per_oct(x, -12)),
    ("bright mic, +9dB/oct tilt",    lambda x: tilt_db_per_oct(x, 9)),
    ("noisy room SNR 20dB",          lambda x: add_noise(x, 20)),
    ("noisy room SNR 10dB",          lambda x: add_noise(x, 10)),
    ("very noisy SNR 6dB",           lambda x: add_noise(x, 6)),
    ("clipping / overdriven input",  lambda x: soft_clip(x, 8)),
    ("AGC pumping",                  lambda x: agc_pump(x)),
    ("live room reverb RT60 1.2s",   lambda x: reverb(x)),
    ("Bluetooth HFP narrowband",     to_narrowband),
    ("worst realistic laptop combo", lambda x: add_noise(soft_clip(highpass(tilt_db_per_oct(x, -9), 150), 4), 14)),
]

print(f"{'condition':<32}" + "".join(f"{n:>20}" for n, _ in NOTES))
print("-" * (32 + 20 * len(NOTES)))
for label, fn in CASES:
    row = f"{label:<32}"
    for _, f0 in NOTES:
        c = track(fn(tone(f0)), f0)
        if len(c) == 0:
            row += f"{'no track':>20}"; continue
        oct_err = np.mean(np.abs(c) > 600) * 100
        good = c[np.abs(c) < 600]
        med = np.median(good) if len(good) else float('nan')
        iqr = (np.percentile(good, 75) - np.percentile(good, 25)) if len(good) else float('nan')
        cell = f"{med:+.1f}c IQR{iqr:.1f}" + (f" !{oct_err:.0f}%" if oct_err > 1 else "")
        row += f"{cell:>20}"
    print(row)
