# Recovered engine map

## Runtime path

`FB_SPCH!SPEAKPHONETICS` does not synthesize audio itself. It stores the far
pointer to the phonetic string at session offsets `0x46`/`0x48`, then posts
`WM_COMMAND` (`0x111`) with command `0x700` to the hidden `FB_NGN` window.

`FB_NGN` receives the session, loads the selected voice DLL, initializes its
phonetic parser and transition state, and opens a mono WinMM `waveOut` stream.
For the high-quality voice this stream is 22,050 Hz signed 16-bit PCM.

## Voice resources

`FB_22K16.DLL` contains:

| Resource | Purpose | Recovered size |
|---|---|---:|
| `NUMPCMRESOURCES` | little-endian PCMD count | 403 |
| `INST` / 256 | synthesis-unit descriptors | 3,648 × 8 bytes |
| `DEMI` / 257 | voice header and transition matrices | 8,704 bytes |
| `HASH` / 259 | rule lookup index | 512 bytes |
| `RULE` / 260 | textual pronunciation rules | 24,064 bytes |
| `PCMD` / 300–702 | signed 16-bit PCM sample storage | 403 resources |

The 11 kHz voice has the same model with 98 `PCMD` resources, 3,904 `INST`
records, and unsigned 8-bit samples.

### DEMI header

The first six 16-bit words are:

```text
flags, phoneme_count, sample_rate, bits_per_sample, nominal_pitch, pitch_bias
```

Both voices contain 46 phonemes. Starting at byte 12 are two signed 46×46
matrices. Each non-negative matrix cell is an index into `INST`; `-1` means no
unit chain. `FB_NGN` uses the first and second matrices for the two phases of a
phoneme transition.

### INST record

Every eight-byte record is:

```text
uint16 flags
uint16 byte_length
uint16 pcmd_resource_index
uint16 byte_offset
```

The corresponding sample data is:

```text
PCMD resource ID = 300 + pcmd_resource_index
slice = resource[byte_offset : byte_offset + byte_length]
```

Bit `0x0100` in `flags` terminates an `INST` chain. All 3,648 high-quality
records resolve inside their referenced PCMD resources, and all 2×46×46 matrix
chains terminate without escaping the table.

## Phonetic parser

The worker recognizes 46 phoneme IDs (`0x00`–`0x2d`) plus stress, pitch,
boundary, speed, volume, mouth, frequency, delay and duration commands.

Before parsing, the original expands:

```text
AX -> [AH
IX -> [IH
```

`tools/phonetics.py` implements the recovered parser. All 891 pronunciations in
the supplied dictionary are accepted.

## Dictionary format

`FB_DEFLT.DIC` is:

```text
uint16 entry_count       # 891
uint16 string_data_size  # 0x4afa

entry[entry_count]:
    uint16 active
    uint32 spelling_offset
    uint32 phonetics_offset

NUL-terminated ASCII string data
```

String data begins at file offset `0x22d2`.

## What the renderers do

For each adjacent phoneme pair, including silence ID 0 at both ends, it walks
the terminated chain selected by transition matrix A and then matrix B. It
concatenates the referenced PCM slices and writes a correctly formatted WAV.

Raw mode proves the complete relationship:

```text
phonetic text -> phoneme IDs -> transition matrices -> INST -> PCMD -> PCM
```

Scheduled mode additionally ports the default renderer arithmetic recovered
from the original 16-bit instructions:

- signed fixed-point unit resizing (`FUN_1000_006e`);
- pitch-period calculation (`FUN_1000_0000`);
- cumulative phase-based repeat selection (`FUN_1000_00f2`);
- tail interpolation when shortening units (`FUN_1000_0552`);
- 31-sample voiced-boundary interpolation for the 22 kHz voice
  (`FUN_1000_0338`).

The external settings route matches `FB_TRANS`: pitch is session word 0 and
speed is word 1. Internally, pitch drives unit resizing while speed drives
period/repetition scheduling; their perceptual effects therefore interact.

### Prosody and command events

The scheduled event generator now ports the three-slot contour queue used by
`FUN_1000_187c` and `FUN_1000_198c`. Primary stress, secondary stress, rising,
falling, and small pitch movements are spread across the two transition phases
surrounding the affected phonemes. The queue carries a Q6 duration excursion
and a period offset, which are attached to each `INST` event before rendering.

Inline phonetic commands are also represented:

- `P0`–`P9`: subsequent pitch setting;
- `S0`–`S9`: subsequent speed setting;
- `V0`–`V9`: subsequent volume setting;
- `D<number>`: silence in the original quarter-second timing units;
- `^` and `|`: boundary/separator events (no PCM payload).

## Remaining synthesis work

The raw concatenation is not the finished voice. The next porting targets in
`FB_NGN` are:

1. verify fixed-point rounding and control behaviour against recordings from
   the original engine under OTVDM;
2. confirm high-volume (`V6`–`V9`) behaviour, which the original delegates to
   the WinMM device rather than modifying PCM;
3. move the verified renderer behind a native C ABI and streaming PCM sink.

The oracle is most useful from this point onward: identical phonetic input and
settings can distinguish remaining arithmetic errors from resource-selection
errors.
