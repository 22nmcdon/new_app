# Woodshed — Project Plan

*A browser-based practice companion that listens to you play, scores intonation and
timing note-by-note, and keeps a memory of what you're bad at.*

Status: **proposal / not yet started** · Drafted 2026-09-19

---

## 1. The idea

Musicians practicing alone get almost no feedback. A metronome tells you nothing
about whether you were actually with it. A tuner tells you about one note at a
time, in isolation, not the note you fluffed in bar 3 of the arpeggio. Practice
apps that do exist are mostly gamified sight-reading (Yousician et al.) aimed at
beginners, and they grade you pass/fail rather than telling you *how* you were
wrong.

Woodshed is the tool for the intermediate-to-advanced player who already knows
what to practice and wants a machine that pays attention. You pick (or are
handed) an exercise, it counts you in, you play, and you get back:

> 2-octave G major, ♩=88 — **84/100**
> Ascending was clean. Descending you were flat from the 5th down
> (median −19¢), and every E♭→D transition rushed by ~35 ms.
> B in the upper octave is your weakest note this week: 7 of 9 takes flat.

That last line is the actual product. Everything else is plumbing to get there:
**longitudinal, per-note statistics that find the specific thing you're getting
wrong and keep putting it in front of you.**

### Who it's for
- Wind and string players and singers — monophonic instruments where intonation
  is a real, daily, unsolved problem.
- Practicing 20–60 minutes a day, mostly alone, mostly on fundamentals.
- Comfortable with a laptop/tablet on the music stand.

### Why it can work as a side project
Everything in v1 runs client-side in the browser. No backend, no accounts, no
audio uploads, no per-user cost. It deploys as static files and works offline as
a PWA. The interesting engineering is all local DSP and data modelling, which is
the fun part and also the defensible part.

---

## 2. Scope

### In scope for v1
- Monophonic pitch tracking from the microphone, live, at ~86 frames/sec.
- Note segmentation and alignment against an expected exercise.
- Scoring: pitch accuracy, intonation stability, timing, completion.
- Generated exercise library: scales, modes, arpeggios, interval drills,
  long tones, chromatic patterns — parameterised, not hand-authored.
- Metronome with count-in, plus input-latency calibration so timing scores mean
  something.
- Local history, progress charts, and a spaced-repetition scheduler that builds
  a daily session for you.
- Weak-spot mining and targeted drill generation.
- Instrument presets (transposition, practical range, tuning reference).
- PWA: installable, fully offline.

### Explicit non-goals for v1
- **Polyphony.** No piano, no strummed guitar, no chord detection. Monophonic
  only. This keeps the DSP tractable and honest. (Phase 5 candidate.)
- **Sheet music.** No MusicXML/MIDI import, no notation rendering beyond a
  simple piano-roll/staff strip. (Phase 4 candidate.)
- **Accounts, sync, social, leaderboards.** Local-first. Export/import a JSON
  blob if you want your data elsewhere.
- **Native apps.** PWA only.
- **Generated coaching prose from an LLM.** The insights are computed from
  statistics and rendered from templates. Cheaper, faster, offline, and it
  can't make things up about your playing.

### Non-negotiable constraints
- Audio never leaves the device. No upload endpoint exists in v1, so there is
  nothing to get wrong.
- Works with a laptop's built-in mic in a normal room. If it needs an interface
  and a condenser mic, it has failed.
- A take must be scored and on screen in under 500 ms after the last note.

---

## 3. Architecture

```
┌──────────────────────────────── main thread ────────────────────────────────┐
│  UI (Preact + TS)          Session engine        Storage (IndexedDB / idb)  │
│  ├ exercise view           ├ exercise generator  ├ takes                    │
│  ├ live pitch ribbon       ├ metronome sched.    ├ srs state                │
│  ├ take summary            ├ segmentation        ├ settings                 │
│  └ history / insights      ├ alignment (DTW)     └ audio blobs (capped)     │
│                            └ scoring rubric                                 │
└──────────────┬─────────────────────────────────────────────┬────────────────┘
               │ frames (f0, confidence, rms) via SAB ring   │ click schedule
┌──────────────┴──────────────┐                 ┌────────────┴────────────────┐
│  AudioWorklet: pitch        │                 │  AudioWorklet: metronome    │
│  ├ 2048 win / 512 hop       │                 │  sample-accurate clicks,    │
│  ├ YIN + parabolic interp   │                 │  narrow-band tick so it's   │
│  ├ octave-error guard       │                 │  easy to reject in analysis │
│  └ spectral-flux onsets     │                 └─────────────────────────────┘
└─────────────────────────────┘
```

**Why AudioWorklet and not ScriptProcessor or `requestAnimationFrame` polling:**
pitch analysis needs a fixed hop and must not drop frames when the UI is busy
rendering a canvas. The worklet runs on the audio thread at a guaranteed
cadence; results go to the main thread through a lock-free `SharedArrayBuffer`
ring buffer (with a `postMessage` fallback where SAB is unavailable, e.g.
without cross-origin isolation headers).

**Pitch algorithm: YIN**, cumulative-mean-normalised difference with parabolic
interpolation, aperiodicity as the confidence signal. Well understood, ~2 ms of
work per frame in plain JS at 2048 samples, accurate to a couple of cents on
sustained tones. Bass instruments need a longer window (4096) — window size is
derived from the instrument preset's low note. A WASM (Rust) implementation is a
phase-2 optimisation, only if profiling says so; a neural tracker (CREPE-tiny)
is explicitly rejected for v1 on bundle size and latency.

**Octave errors** are the classic YIN failure mode and would poison the
statistics. Guard: bias toward the previous frame's octave when the difference
function has near-equal minima, and hard-clamp to the instrument preset's range.

**Stack:** TypeScript, Vite, Preact, Zustand for state, Canvas 2D for the pitch
ribbon (60 fps, SVG won't hold up), `idb` for IndexedDB, Vitest + Playwright.
Deployed as static files to any CDN. Boring on purpose — the novelty budget is
spent in the DSP and the scoring model.

---

## 4. The hard parts, and how they get solved

These three decide whether the product is any good. They're front-loaded in the
schedule for that reason.

### 4.1 Input latency — or, why timing scores are garbage by default
Mic input latency ranges from ~5 ms on a wired interface to 150 ms+ on
Bluetooth, and the browser doesn't reliably tell you. Without correcting for it,
every onset lands late and the timing score is a measurement of the user's
hardware.

**Solution: a calibration wizard.** The user taps or claps along with a click
for 8 bars; we take the median of (detected onset − scheduled click) and store
it as `latencyOffsetMs` per input device. Recalibrate automatically when the
device ID changes. If the measured spread is wide (>25 ms IQR), we say so and
down-weight timing in the score rather than pretending to measure it.

### 4.2 Metronome bleed
Speakers put the click into the mic, and spectral-flux onset detection will
happily count it as a note. Mitigations, in order of preference:
1. Recommend headphones; detect the likely-bleeding case by correlating detected
   onsets against the known click schedule and warn once.
2. Use a narrow-band click (a short windowed sine around 2.5 kHz) and notch it
   out of the onset detector's spectral input.
3. Suppress onset candidates within ±15 ms of a scheduled click *only* when the
   frame's pitch confidence is low — a real note played on the beat must still
   register.

### 4.3 Alignment
The player drops notes, adds notes, repeats a bar. Naive index-matching breaks
immediately. Use **monotonic DTW** over (expected pitch, expected onset) vs.
(detected pitch, detected onset) with explicit insert/delete costs, so a missed
note costs a deletion instead of shifting every subsequent note's blame. Cost
function weights pitch distance in semitones and onset distance in beats;
deletions are cheaper than a wrong-pitch match so the algorithm prefers "you
missed it" over "you played it a fifth off".

### 4.4 Microphone variability — measured, not assumed

The obvious objection to this whole project is that people's microphones vary
wildly. Before building anything, the core claim was tested against synthesized
tones with exact ground truth, pushed through models of real microphone chains.

**Pitch is close to immune.** A cheap mic has poor frequency *response*, a high
noise floor and distortion; none of these shift the *periodicity* of the
waveform, and periodicity is all YIN measures. Median error vs. ground truth:

| Condition | C2 (65 Hz) | D3 | D4 | A5 |
|---|---|---|---|---|
| clean reference | +0.3¢ | +1.2¢ | +0.7¢ | +1.0¢ |
| laptop mic, HPF 150 Hz | +0.1¢ | +0.8¢ | +0.7¢ | +1.0¢ |
| aggressive HPF 300 Hz | +0.1¢ | +0.4¢ | +0.6¢ | +1.0¢ |
| noisy room, SNR 10 dB | +0.2¢ | +1.2¢ | +1.1¢ | +1.6¢ |
| clipping / overdriven input | +0.2¢ | +1.2¢ | +0.8¢ | +1.2¢ |
| AGC pumping | +0.3¢ | +1.2¢ | +0.7¢ | +1.0¢ |
| live room, RT60 1.2 s | +0.3¢ | +0.9¢ | +1.1¢ | +1.3¢ |
| Bluetooth HFP narrowband | +0.0¢ | +0.7¢ | +0.8¢ | +0.9¢ |
| worst realistic laptop combo | +1.7¢ | +2.4¢ | +1.9¢ | +2.1¢ |

A 10× margin against the 20¢ tolerance in the worst case. Note the C2 column
under a 300 Hz high-pass: the fundamental is entirely absent and it still tracks
to 0.1¢, because the harmonics preserve the period.

Two real effects did show up. **Strong spectral tilt biases the estimate** — a
realistically dull mic (LPF 2 kHz) costs +3.5¢, a very dull one +6.5¢ — but the
bias is systematic per (mic, instrument, register), so it is near-constant
across takes and largely cancels in the longitudinal analysis that the product
depends on. And **low SNR causes octave errors** (10% of frames at SNR 6 dB on
A5), which is what the octave guard in §3 is for.

**Onsets are the fragile axis.** Detections for an 8-note sequence:

| Condition | notes detected (of 8) |
|---|---|
| clean / HPF / Bluetooth | 7 |
| small room, RT60 0.4 s | 9 |
| live room, RT60 1.2 s | **22** |
| noisy room, SNR 10 dB | **40** |

Reverb and noise make spectral flux *invent* notes. Phantom onsets would wreck
DTW alignment and pollute the history with bogus extra-note penalties.

**Consequence for the design: segment on pitch, not on energy.** Note boundaries
come from f0 stability and confidence transitions, which inherit the robustness
measured above. Spectral flux is demoted to a tiebreaker for consecutive notes
at the *same* pitch, the one case pitch alone cannot segment.

### 4.5 Browser and OS voice processing

A bigger practical threat than mic quality, and one no amount of DSP fixes.
`getUserMedia` defaults to echo cancellation, noise suppression and AGC, all
tuned for speech; noise suppression will spectrally gate a sustained instrument
tone. Disable explicitly:

```js
getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false,
                        autoGainControl: false, latency: 0 } })
```

What cannot be disabled from JS is OS-level processing (macOS Voice Isolation,
Windows "audio enhancements"). That needs detection — probe with a known tone,
look for gating artifacts — and a prompt telling the user where to turn it off.
Bluetooth is the other one: opening an input stream flips the headset into HFP,
and while pitch survives it intact, latency jumps to 100–300 ms with poor jitter.

### 4.6 Setup check and per-metric degradation

Because of the above, a bad input chain must **never** produce a wrong number.
On first run and on any device change, measure the input: noise floor, sample
rate, track settings actually granted vs. requested, latency offset and jitter,
and gating artifacts. Then enable metrics individually:

| Measurement | Requires | If it fails |
|---|---|---|
| Pitch, stability | almost nothing | (effectively always available) |
| Timing | latency jitter IQR ≤ 25 ms | timing not scored; take shows pitch only |
| Note completion | SNR ≥ ~12 dB | warn that dropped/extra notes may be spurious |

The app says which metrics are live and why, in plain language. "Intonation
only — your Bluetooth headset's timing is too unstable to measure" is an honest
product. A confident timing score derived from a 200 ms Bluetooth delay is not.

---

## 5. The scoring model

Per note, from the segmented and aligned events:

| Component | Measurement | Default tolerance |
|---|---|---|
| **Pitch** | 10%-trimmed median cents deviation over the sustain portion (attack: first 60 ms excluded; release: last 40 ms excluded) | ≤ 20¢ |
| **Stability** | IQR of cents within the sustain portion | ≤ 15¢ |
| **Timing** | onset deviation from the quantised grid, after latency correction | ≤ min(40 ms, 12% of a beat) |

- `noteScore = 0.45·pitch + 0.35·timing + 0.20·stability`, each mapped through a
  soft curve so "just inside tolerance" isn't a cliff-edge 100.
- `takeScore = mean(noteScore over matched notes) × completion − extraNotePenalty`
  where `completion = matched / expected`.
- Tolerances are per-instrument and per-user. Voice and fretless strings get
  looser pitch tolerance by default; a beginner preset opens it to 30¢. **The
  tolerance is always shown next to the score** — an unlabelled number out of
  100 is meaningless.

**Mastery and the tempo ladder.** An exercise goes green after 3 consecutive
takes ≥ 90 at its target tempo. Then the target tempo increases by 4 bpm and the
streak resets. Nothing ever graduates permanently, because that is not how
practice works.

---

## 6. Memory: scheduling and weak-spot mining

### Spaced repetition, adapted
SM-2, but the grade comes from the measured score instead of a self-report
(musicians are terrible judges of their own takes, which is the entire premise
of the app):

| Take score | Next interval |
|---|---|
| < 70 | reset to 1 day, ease −0.2 |
| 70–85 | × 1.6 |
| 85–95 | × 2.2 |
| > 95 | × 2.8, ease +0.1 |

Capped at 60 days. A "build my session" button fills a requested duration
(default 30 min) from what's due, ordered long-tones → technical → weak-spot
drills, and refuses to schedule more than ~40% of the session on things you're
already good at.

### Weak-spot mining — the differentiator
Every scored note is stored with its context: scale degree, absolute pitch,
interval from the previous note, direction, position in the phrase, tempo. Over
a few weeks that's tens of thousands of labelled observations per user. Then:

- Aggregate cents error by **absolute pitch** → "your upper-register B is
  consistently 22¢ flat" (a real, physical instrument/embouchure problem).
- Aggregate by **interval and direction** → "descending minor 6ths land sharp".
- Aggregate by **transition** → "the B→C♯ crossing is late by 30 ms at any
  tempo above 100" (a fingering problem, not a timing problem).
- Aggregate by **position in phrase** → "you rush the last two notes of every
  ascending run".

Each finding above a significance threshold (effect size *and* sample count —
no calling a trend off four notes) generates a targeted micro-drill that isolates
the problem, and is injected into the SRS queue. This is the loop that a
metronome and a tuner cannot give you, and it's why the boring statistics
plumbing is worth building properly.

---

## 7. Data model

```ts
Instrument  { id, name, transposeSemitones, lowMidi, highMidi, defaultToleranceCents }
Exercise    { id, kind, params, targetTempoBpm, expectedNotes: ExpectedNote[], tags[] }
ExpectedNote{ midi, beat, durationBeats, degree, intervalFromPrev }
Take        { id, exerciseId, startedAt, tempoBpm, toleranceCents,
              notes: ScoredNote[], score, breakdown, audioBlobId? }
ScoredNote  { expectedIdx?, detectedIdx?, verdict: hit|flat|sharp|missed|extra,
              centsMedian, centsIqr, onsetDevMs, noteScore }
SrsState    { exerciseId, ease, intervalDays, dueAt, masteryStreak, targetTempoBpm }
Finding     { id, kind, subject, effectSize, sampleCount, firstSeen, drillExerciseId }
Settings    { a4Hz, toleranceCents, latencyOffsetMs, inputDeviceId, instrumentId,
              audioRetentionTakes }
```

Exercises are **generated from parameters, not stored as note lists** — a scale
exercise is `{ kind: "scale", root: "G", mode: "major", octaves: 2,
articulation: "legato", pattern: "up-down" }` and the note list is rendered from
it. That keeps the library infinite, the storage tiny, and makes targeted drill
generation a matter of constructing a params object.

Audio retention is capped (default: last 20 takes, Opus via `MediaRecorder`)
with a visible storage figure and a one-click purge.

---

## 8. Milestones

Estimates assume one person, part-time. Each milestone ends with something
runnable.

| # | Milestone | Est. | Done when |
|---|---|---|---|
| **M0** | **Spike** — throwaway | 1 wk | YIN in a worklet tracks a real instrument within 5¢ of a reference tuner *on a built-in laptop mic in a live room*; `getUserMedia` constraints verified as actually granted (and OS-level processing detected) on macOS, Windows and iOS; round-trip latency and jitter measured on 3 devices; SAB transport proven. Findings written up, code deleted. |
| **M1** | **Shell + tuner** | 1 wk | App shell, mic permission flow, live pitch ribbon on canvas, working chromatic tuner. **Ships as v0.1** — a good tuner is useful on its own and gets real-device feedback early. |
| **M2** | **Exercise engine** | 2 wks | Exercise params → rendered notes → on-screen strip; sample-accurate metronome with count-in; latency calibration wizard; instrument presets. |
| **M3** | **Scoring** | 2 wks | Pitch-driven note segmentation (flux as repeat-note tiebreaker only, per §4.4), DTW alignment, the rubric, per-metric degradation gates, and a take-summary screen with per-note verdicts. **This is the riskiest milestone** — budget for the scores to be wrong at first and for the fix to be in segmentation, not the rubric. |
| **M4** | **Memory** | 1.5 wks | IndexedDB persistence, history charts, SRS scheduler, "build my session". |
| **M5** | **Weak-spot mining** | 2 wks | Aggregation queries, significance thresholds, finding templates, generated drills in the queue. |
| **M6** | **Ship** | 1.5 wks | PWA/offline, onboarding, export/import, empty and error states, storage management. Public v1. |

**~11 weeks.** M0–M3 are the real project; if the scoring isn't convincing by
the end of M3, the rest isn't worth building and the tuner from M1 is a fine
place to stop.

---

## 9. Testing

Audio code is untestable by vibes, so this is decided up front:

- **Synthetic fixtures.** Generated WAVs with known ground truth — steady tones
  at exact cent offsets, vibrato at known depth/rate, glissandi, notes with
  attack transients, notes at known onset times. The pitch tracker and the
  segmenter are unit-tested against these with hard numeric assertions.
- **Recorded corpus.** 30–50 real takes across instruments and rooms, hand-
  labelled once, kept as a regression suite. Scoring changes must be diffed
  against the whole corpus before merging — "improved the rubric" is only true
  if the corpus says so.
- **Golden-file scoring tests.** Take fixture in, JSON breakdown out, committed.
- **Playwright** for the flows that involve real permissions and real state,
  feeding a synthetic stream in place of a microphone.
- **Manual device matrix** at each milestone: built-in mic on a laptop,
  phone, and one USB interface. Bluetooth explicitly tested as the bad case.

---

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Pitch tracking too noisy on real instruments in real rooms | Fatal | **Largely retired** by the §4.4 simulation: ≤2.4¢ error under a worst-case laptop chain, against a 20¢ tolerance. M0 confirms it on real hardware |
| Phantom onsets in live/noisy rooms corrupt alignment and history | High | Measured in §4.4 (up to 40 detections for 8 notes). Segment on pitch rather than energy; flux only breaks ties between same-pitch notes |
| OS-level voice processing mangles sustained tones, undisableable from JS | High | Detect via a known-tone probe; prompt the user to turn it off; degrade to pitch-only if they don't |
| Timing scores meaningless from input latency | High | Calibration wizard in M2; refuse to score timing when calibration confidence is low, rather than reporting a number we don't believe |
| Metronome bleed pollutes onsets | Medium | Narrow-band click + confidence-gated suppression + a headphone nudge |
| Scores feel unfair → users stop trusting it | Fatal (product, not technical) | Always show tolerance and per-note evidence; let the user play back the audio of any note the app marked wrong. If they can hear it was fine, the app is wrong and that's a bug report |
| Scope creep into polyphony/notation | High | Written into non-goals above; polyphony is a different product |
| IndexedDB eviction loses history | Medium | `navigator.storage.persist()`, plus a nagging export prompt after 30 takes |

---

## 11. Open questions

1. **Preact or Svelte?** Leaning Preact for ecosystem familiarity; Svelte would
   be a smaller bundle. Decide at M1, cheap to change then, expensive later.
2. **Cross-origin isolation** for `SharedArrayBuffer` constrains hosting and
   breaks some embeds. Build the `postMessage` fallback first and treat SAB as
   the optimisation?
3. **Does the tuner ship separately** as its own small free thing to build an
   audience before v1? Costs a week of polish.
4. **How loose should default tolerance be?** Too tight and everyone feels bad;
   too loose and the app is flattering and useless. Needs real players in M3.
5. **Guitar (monophonic single-note lines)** is in range technically but has
   nasty octave-error behaviour on the low E. Include in the v1 preset list or
   wait?

---

## 12. Alternatives considered

Briefly, so the reasoning isn't lost:

- **A setlist/gig manager for working bands.** Real pain, but it's CRUD — no
  interesting core and a crowded field.
- **A browser loop sequencer / DAW-lite.** Fun to build, but competing with
  free tools that are already excellent.
- **A granular delay as a CLAP/VST3 plugin.** Genuinely interesting DSP, but
  the audience is small and distribution (installers, DAW compatibility
  matrices, code signing) is most of the work.
- **Ear-training with SRS.** The SRS half is shared with this plan, but
  recognising intervals is a solved app category, and it trains your ears
  rather than your hands.

Woodshed won because the hard part is also the valuable part: nobody else is
keeping per-note statistics on your playing over months and telling you what
they mean.
