# FB_SPCH front-end coverage audit

This audit supersedes earlier claims that the major text-processing paths were
complete. Those claims considered the dictionary/RULE path and the final
boundary rewrite table, but did not inventory every direct append from
`FB_SPCH!FUN_1000_3c14` or its private data-segment phonetic tables.

## Confirmed and ported

- All 891 active `FB_DEFLT.DIC` entries.
- `HASH`/`RULE` unknown-word pronunciation.
- The complete ten-entry `FUN_1000_4834` boundary rewrite table.
- Sentence target generation in `FUN_1000_3f6c`.
- Cardinal number phonetics from offsets `0x468`–`0x61e`, including the
  numeric-zero/word-zero distinction and leading-zero digit mode.
- Cardinal hundred and scale fragments at `0x83a`, `0x84e`, and
  `0x62c`–`0x685` plus `0x85a`.
- Ordinal and month tables.
- Fixed symbol/operator phrases and dollar/cent morphology.
- Contextual `DR`, `ST`, and `MT` expansions and possessive suffix selection.
- Inline setting scopes and raw phonetics.

## Confirmed missing or approximated

- Some edge cases in the currency and ordinal state machines.
- Exact acronym/spelling decision patterns referenced by `FUN_1000_2318`.
- Parenthesis and quotation grouping state used by the sentence post-pass.
- Some punctuation look-ahead decisions, especially period versus decimal,
  ellipsis, and abbreviation termination.

## Parsed but apparently dormant

The phonetic parser returns command types 1 and 2 for `M` and `F`, but
`FB_NGN!FUN_1000_149c` has no handlers for cases 1 or 2. They are discarded by
the shipped engine. This is evidence of dormant/reserved commands, not yet
evidence of missing acoustic processing.

## Renderer uncertainties

- Exact 16-bit fixed-point rounding at a few interpolation boundaries.
- `V6`–`V9`, which the original forwards to WinMM device volume.
- Timing differences caused by the original streaming callback cadence.

No remaining area in this document should be described as complete until it
has either an exact port with regression tests or an OTVDM oracle comparison.
