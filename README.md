# Modern Mono

Modern Mono brings the classic Monolog speech synthesizer to current versions
of NVDA. It is intended for people who enjoy compact, responsive speech and
for long-time screen-reader users who remember the sound of older synthesizers.

The add-on runs locally and includes both the cleaner 22 kHz voice and the
lower-fidelity 11 kHz voice. No old Windows installation or speech hardware is
required.

## Download and install

**[Download the latest Modern Mono release](https://github.com/SeanRandall/Modernmono/releases/latest)**

Download the `.nvda-addon` file from the release, open it, approve the NVDA
installation prompt, and restart NVDA when requested. Select **Modern Mono**
from NVDA's speech synthesizer settings.

## What it offers

- Two Monolog voices: 22 kHz/16-bit and the more traditional 11 kHz/8-bit sound.
- Two opt-in **Monolog DT Fast** variants which retain those acoustic voices but
  use faster, DoubleTalk-inspired period/unit scheduling, stronger pause
  shortening and the expanded community/CMU pronunciation path by default.
- The original Monolog dictionary and pronunciation rules.
- An optional community American-English dictionary for broader vocabulary.
- A separate user dictionary under NVDA's Speech dictionaries preferences,
  including a preview button for hearing phonetic entries.
- Several optional rate-boost experiments, ranging from original-style timing
  to whole-utterance WSOLA compression and shortened punctuation pauses.
- Adjustable pitch, volume and excitation (sentence-level pitch movement).
- Optional backtick commands for changing pitch, speed, volume and delay inside
  text.

All additional dictionaries and rate-boost modes are **off by default**. A new
installation therefore starts with the closest available behaviour to ordinary
Monolog, and each experiment can be enabled separately from NVDA's voice
settings.

Selecting a **DT Fast** voice is itself an opt-in: it automatically enables the
expanded pronunciation path for that voice and applies a fast-reading profile,
without changing the settings or output of either ordinary Monolog voice. The
DoubleTalk ROM is not included and no ROM speech, dictionary, or program data is
copied into the add-on.

The **Precise Pete** voice is a separate DoubleTalk-preset reconstruction. ROM
voice 3 is a parameter preset rather than another acoustic sample bank: it uses
pitch 60, expression 4, formant setting 6, treble tone, articulation 8 and no
reverb. Modern Mono maps the compatible controls onto its 22 kHz voice and uses
a peak-normalized treble treatment; unsupported controls are not faked by
copying ROM code or waveform data.

The same ROM also provides parameter rows for **Vader**, **Big Bob** and
**Ricochet Randy**. These are exposed as additional 22 kHz voices. Their exact
pitch/expression settings are applied directly; formant, tone and reverb values
are represented by bounded, peak-normalized time-domain treatments. The ROM's
Perfect Paul row is the neutral baseline and therefore remains the ordinary
Monolog voice rather than appearing as a duplicate.

DoubleTalk's main voice-shaping parameters are available in NVDA's synthesizer
settings and settings ring: **articulation**, **formant frequency**, **tone**
(bass/normal/treble), **reverb**, pitch and excitation/expression. Selecting a
ROM-derived voice loads its recovered defaults; every parameter can then be
changed independently. Signal-shaping controls are peak bounded to avoid
introducing amplitude clipping.

## Rate boost choices

Different listeners value speed, clarity and the original sound differently,
so the current add-on deliberately provides several choices:

- **Off:** original Monolog 0–9 timing.
- **Clean reading:** extends the recovered timing only to its safe limit and
  progressively shortens punctuation pauses.
- **WSOLA crisp, balanced and smooth:** compress complete rendered speech with
  different window sizes rather than cutting individual speech units.
- **WSOLA maximum speed:** the strongest whole-utterance compression profile.
- **WSOLA balanced + shorter pauses:** combines balanced compression with more
  aggressive pause reduction.
- **Previous whole-unit boost:** retains the earlier pitch-stable experiment.
- **Legacy overlap/add:** retains the faster behaviour from early builds.

These modes are experimental. The best choice is whichever remains most
understandable with your voice, pitch and usual NVDA reading rate.

## Dictionaries

The community dictionary is optional because it changes the pronunciation of
many words. Modern Mono first respects its native dictionary, then uses
converted community and CMU pronunciations where appropriate. Recent mapping
work improves reduced vowels and words such as “long,” “pause,” “because,”
“sentence” and “short.”

Personal corrections can be added through **NVDA Preferences → Speech
dictionaries → Modern Mono user dictionary** without modifying the bundled
files.

## Technical background

Modern Mono is a native Python reconstruction of the Monolog Win16 speech
engine. It does not run the original Win16 program. The project parses the old
NE resources, dictionary, spelling rules, phonetic command language, transition
matrices and recorded synthesis units, then schedules those units directly for
NVDA.

The recovered renderer currently includes:

- all 891 original dictionary entries;
- the original `HASH`/`RULE` pronunciation fallback;
- 22 kHz/16-bit and 11 kHz/8-bit unit data;
- duration, pitch-period and repetition scheduling;
- boundary interpolation and crossfades;
- stress, punctuation and sentence-intonation contours;
- number, date, currency, abbreviation and symbol handling;
- optional excitation scaling and several rate experiments.

Deeper reverse-engineering notes are in
[`analysis/ENGINE_MAP.md`](analysis/ENGINE_MAP.md) and
[`analysis/PYTHON_NVDA_PLAN.md`](analysis/PYTHON_NVDA_PLAN.md).

## Testing and development

Run the complete automated test suite:

```powershell
python -m unittest discover -s tests -v
```

Render ordinary text to a WAV file:

```powershell
python tools\render_text.py "absence, actually another dictionary." output.wav
```

Render a raw phonetic sequence using the recovered scheduler:

```powershell
python tools\render_units.py monologue16\FB_22K16.DLL "hEHl'OW" output.wav --mode scheduled
```

Or use the interactive Windows console:

```powershell
python tools\console_speak.py
```

The diagnostic tools expose the original 0–9 pitch and speed ranges and are
primarily intended for engine investigation. The NVDA add-on provides the
normal 0–100 controls and the additional user-facing settings described above.
