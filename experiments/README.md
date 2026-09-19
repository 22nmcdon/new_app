# Experiments

Throwaway scripts that produced evidence cited in [../PLAN.md](../PLAN.md).
Not part of the app; kept so the numbers in the plan can be re-derived and
challenged.

- `mic_robustness.py` — synthesizes harmonic tones at known f0, pushes them
  through models of real microphone chains (high-pass rolloff, spectral tilt,
  noise, clipping, AGC pumping, reverb, Bluetooth HFP narrowband), runs YIN,
  and reports cents error against exact ground truth. Source of the table in
  §4.4.
- `followup.py` — (1) probes the spectral-tilt bias outlier, (2) measures
  onset-detection behaviour under reverb and noise. Source of the phantom-onset
  finding that moved segmentation from energy-driven to pitch-driven.

Run: `pip install numpy && python3 mic_robustness.py`

## Caveats on these numbers

- The YIN here is a quick reference implementation with ~1¢ of baseline bias on
  clean signal, so the reported errors are **upper bounds**, not precise figures.
- Degradations are analytic models, not recordings. They establish that pitch
  is robust *in principle* to the things mics do; they do not substitute for the
  M0 spike on real hardware in a real room.
- The onset section's jitter column was a poorly designed metric (each true
  onset matched to its nearest detection, which flatters a detector that fires
  constantly). The detection *count* is the meaningful result there.
