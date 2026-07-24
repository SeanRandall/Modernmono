#!/usr/bin/env python3
"""Render Monolog transition units with recovered duration and pitch scheduling."""

from __future__ import annotations

import argparse
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

from .phonetics import parse
from .voice_inspect import inspect_voice, transition_units


def selected_units(manifest: dict, phonetics: str) -> list[int]:
    phonemes = [token.value for token in parse(phonetics) if token.name == "phoneme"]
    result: list[int] = []
    left = 0
    for right in [*phonemes, 0]:
        result.extend(transition_units(manifest, left, right, "a"))
        result.extend(transition_units(manifest, left, right, "b"))
        left = right
    return result


@dataclass
class UnitEvent:
    unit_index: int | None
    duration_contour: int = 0
    period_offset: int = 0
    command_pitch: int | None = None
    command_speed: int | None = None
    volume: int = 5
    delay_units: int = 0


_CONTOUR_COMMANDS = {
    "pitch_small_down": (0, -5),
    "pitch_small_up": (0, 5),
    "pitch_up": (15, 0),
    "pitch_down": (-15, 0),
    "primary_stress": (25, -8),
    "secondary_stress": (15, -5),
    "pitch_fall": (-15, 5),
}


def _future_phonemes(tokens, start: int, count: int = 2) -> list[int]:
    result = []
    for token in tokens[start:]:
        if token.name == "delay":
            break
        if token.name == "phoneme":
            result.append(token.value)
            if len(result) == count:
                break
    return result + [0] * (count - len(result))


def scheduled_events(manifest: dict, phonetics: str) -> list[UnitEvent]:
    """Port FB_NGN's transition event order and three-slot contour queue."""
    tokens = parse(phonetics)
    # value_q6, remaining unit count, delta_q6, period offset
    contours = [[0, -1, 0, 0] for _ in range(3)]
    current_contour = 0
    previous = 0
    current = 0
    events: list[UnitEvent] = []
    command_pitch: int | None = None
    command_speed: int | None = None
    volume = 5

    def duration_values() -> tuple[list[int], int]:
        """Port the numeric-target pre-pass in FB_NGN!FUN_1000_16d8."""
        segments: list[tuple[int, int, int]] = []
        current_value = 50
        current_phoneme = 0
        unit_count = 0
        since_target = False
        for token_index, token in enumerate(tokens):
            if token.name == "phoneme":
                previous_phoneme = current_phoneme
                current_phoneme = token.value
                if since_target:
                    unit_count += chain_length(previous_phoneme, current_phoneme, "a")
                since_target = True
                unit_count += chain_length(previous_phoneme, current_phoneme, "b")
            elif token.name == "duration" and since_target:
                following = _future_phonemes(tokens, token_index + 1, 1)[0]
                unit_count += chain_length(current_phoneme, following, "a")
                delta_q6 = _trunc_div((token.value - current_value) * 0x40, unit_count) if unit_count else 0
                segments.append((current_value * 0x40, unit_count, delta_q6))
                current_value = token.value
                unit_count = 0
                since_target = False
        if unit_count:
            segments.append((current_value * 0x40, unit_count, 0))
        values: list[int] = []
        for start_q6, count, delta_q6 in segments:
            value_q6 = start_q6 - delta_q6
            for _ in range(count):
                value_q6 += delta_q6
                values.append(_trunc_div(value_q6, 0x40) - 50)
        return values, current_value - 50

    def chain_length(left: int, right: int, matrix: str) -> int:
        return len(transition_units(manifest, left, right, matrix))

    numeric_durations, numeric_tail = duration_values()
    numeric_index = 0

    def annotate(unit_index: int) -> None:
        nonlocal contours, current_contour, numeric_index
        while contours[0][1] == 0:
            contours = [contours[1], contours[2], [0, -1, 0, 0]]
            if contours[0][1] > 0:
                current_contour = contours[0][0] - contours[0][2]
                break
        if contours[0][1] > 0:
            current_contour += contours[0][2]
            contours[0][1] -= 1
        events.append(
            UnitEvent(
                unit_index,
                _trunc_div(current_contour, 0x40) + (
                    numeric_durations[numeric_index]
                    if numeric_index < len(numeric_durations) else numeric_tail
                ),
                contours[0][3],
                command_pitch,
                command_speed,
                volume,
            )
        )
        numeric_index += 1

    for token_index, token in enumerate(tokens):
        if token.name in _CONTOUR_COMMANDS:
            excursion, period_offset = _CONTOUR_COMMANDS[token.name]
            following, after_following = _future_phonemes(tokens, token_index + 1)
            # FUN_1000_187c deliberately starts one phoneme behind the current
            # parser position to shape coarticulation into the stressed sound.
            contour_left = previous
            if contours[0][1] == -1:
                contours[0][2] = 0
                contours[0][0] = 0
                contours[0][1] = chain_length(contour_left, following, "a")
            contours[1][3] += period_offset
            contours[2][0] += excursion * 0x40
            contours[1][1] = chain_length(contour_left, following, "b")
            if contours[1][1] > 0:
                contours[1][2] = _trunc_div(contours[2][0], contours[1][1])
            contours[2][3] += period_offset
            contours[2][1] = chain_length(following, after_following, "a")
            if contours[2][1] > 0:
                contours[2][2] = _trunc_div(-contours[2][0], contours[2][1])
            continue
        if token.name == "pitch":
            command_pitch = max(0, min(9, token.value))
            continue
        if token.name == "speed":
            command_speed = max(0, min(9, token.value))
            continue
        if token.name == "volume":
            volume = max(0, min(9, token.value))
            continue
        if token.name == "delay":
            # A delay follows the ordinary transition from the current
            # phoneme to silence.  Resetting first dropped both transition
            # chains and audibly clipped sentence-final consonants/vowels.
            previous, current = current, 0
            for matrix in ("a", "b"):
                for unit_index in transition_units(manifest, previous, current, matrix):
                    annotate(unit_index)
            events.append(
                UnitEvent(
                    None,
                    command_pitch=command_pitch,
                    command_speed=command_speed,
                    volume=volume,
                    delay_units=max(0, token.value),
                )
            )
            continue
        if token.name != "phoneme":
            continue
        previous, current = current, token.value
        for unit_index in transition_units(manifest, previous, current, "a"):
            annotate(unit_index)
        # The original emits a mouth-position event here; it carries no PCM.
        for unit_index in transition_units(manifest, previous, current, "b"):
            annotate(unit_index)

    if current != 0:
        previous, current = current, 0
        for matrix in ("a", "b"):
            for unit_index in transition_units(manifest, previous, current, matrix):
                annotate(unit_index)
    return events


def _trunc_div(value: int, divisor: int) -> int:
    return abs(value) // divisor * (-1 if value < 0 else 1)


def _interpolate(fraction: int, first: int, second: int) -> int:
    # FB_SPCH!FRACINTERP uses a signed Q15 fraction.
    return first + _trunc_div((second - first) * fraction, 0x7FFF)


def _resize_unit(samples: list[int], byte_delta: int, sample_width: int) -> list[int]:
    byte_delta &= ~1
    if byte_delta == 0 or not samples:
        return samples.copy()
    sample_delta = byte_delta // sample_width
    if sample_delta > 0:
        return samples + [samples[-1]] * sample_delta

    new_count = max(0, len(samples) + sample_delta)
    result = samples[:new_count]
    quarter_bytes = len(samples) * sample_width // 4
    removed_bytes = -byte_delta
    fade_bytes = removed_bytes if removed_bytes <= quarter_bytes else removed_bytes // 2
    fade_samples = min(fade_bytes // sample_width, len(result), len(samples) - new_count)
    if fade_samples:
        destination = len(result) - fade_samples
        source = len(samples) - fade_samples
        step = 0x7FFF // fade_samples
        fraction = 0
        for index in range(fade_samples):
            result[destination + index] = _interpolate(
                fraction, result[destination + index], samples[source + index]
            )
            fraction += step
    return result


def _extended_period(length: int, control: int) -> int:
    """Continue FB_NGN's speed curve past S9 without crossing zero.

    The recovered expression is retained exactly through S13 so legacy boost
    matches the older builds. Beyond it, continue by the final step's ratio,
    linearly driving the period negative.  This only changes native unit
    repetition scheduling; PCM samples and their pitch remain untouched.
    """
    if control <= 130:
        return max(1, length + ((length * (25 - control)) >> 7))
    native_s13 = length + ((length * (25 - 130)) >> 7)
    extra_steps = (control - 130) / 10.0
    return max(1, round(native_s13 * (23.0 / 33.0) ** extra_steps))


def _scheduled_frames(
    manifest: dict,
    pcmd: dict[int, bytes],
    events: list[UnitEvent],
    *,
    speed: int = 5,
    pitch: int = 5,
    excitation: int = 50,
) -> bytes:
    sample_width = manifest["bits_per_sample"] // 8
    signed = sample_width == 2
    source_phase = 0
    output_phase = 0
    previous_sample = 0 if signed else 0x80
    output: list[int] = []
    boost_event_index = 0
    if sample_width == 1:
        fade_bytes = 0x0F if manifest["nominal_pitch"] < 0xA0 else 0x1F
    else:
        fade_bytes = 0x1E if manifest["nominal_pitch"] < 0x140 else 0x3E
    fade_samples = fade_bytes // sample_width
    fade_step = 0x800 if fade_bytes == 0x1E else 0x400

    for event in events:
        effective_pitch = pitch if event.command_pitch is None else event.command_pitch
        effective_speed = speed if event.command_speed is None else event.command_speed
        if event.delay_units:
            period = _extended_period(0x200, min(effective_speed, 18) * 10)
            period = max(1, period)
            repeats = (manifest["sample_rate"] // period) * event.delay_units
            bytes_per_repeat = period // (2 if sample_width == 2 else 4)
            silence_samples = repeats * bytes_per_repeat // sample_width
            output.extend([0 if signed else 0x80] * silence_samples)
            continue
        if event.unit_index is None:
            continue
        index = event.unit_index
        unit = manifest["units"][index]
        start = unit["byte_offset"]
        raw = pcmd[unit["resource_index"]][start : start + unit["byte_length"]]
        if signed:
            samples = list(struct.unpack(f"<{len(raw) // 2}h", raw))
        else:
            samples = list(raw)

        original_bytes = unit["byte_length"]
        # FUN_1000_16d8 seeds a 50<<6 duration contour.  FUN_1000_198c
        # divides that Q6 value by 64 before attaching it to each unit event.
        # FB_TRANS stores pitch at session word 0 and speed at word 1.
        # FB_NGN routes word 0 through the unit-resizing contour and word 1
        # through period/repetition scheduling; both affect perceived pitch.
        pitch_offset = effective_pitch - 5
        duration_contour = _trunc_div(event.duration_contour * excitation, 50)
        period_offset = _trunc_div(event.period_offset * excitation, 50)
        duration = 50 + pitch_offset * (40 if pitch_offset < 1 else 20) + duration_contour
        internal_period_control = min(effective_speed, 18) * 10 + period_offset
        delta = _trunc_div(original_bytes * (50 - duration), 400) * 2
        if not unit["flags"] & 0x80 and delta > 0:
            delta = 0
        adjusted_bytes = min(0x400, original_bytes + delta)
        delta = adjusted_bytes - original_bytes
        adjusted = _resize_unit(samples, delta, sample_width)
        adjusted_bytes = len(adjusted) * sample_width

        pitch_period = _extended_period(original_bytes, internal_period_control)
        source_phase += pitch_period
        output_phase += adjusted_bytes
        repeats = 1
        lower = source_phase - adjusted_bytes // 2
        upper = source_phase + adjusted_bytes // 2
        if unit["flags"] & 0x2100 == 0:
            for _ in range(3):
                if output_phase >= lower:
                    break
                output_phase += adjusted_bytes
                repeats += 1
            if output_phase > upper:
                output_phase -= adjusted_bytes
                repeats -= 1
        if output_phase > 20000:
            output_phase -= 10000
            source_phase -= 10000

        # S18 is approximately where phase scheduling reaches its natural
        # one-unit floor. Above it, continue the same whole-unit selection
        # idea with deterministic, progressively nested unit selection. No sample is stretched,
        # resampled, or shortened, so the surviving units retain their pitch.
        drop_event = False
        if effective_speed > 18:
            drop_event = boost_event_index % 27 < effective_speed - 18
        boost_event_index += 1

        for _ in range(0 if drop_event else max(0, repeats)):
            rendered = adjusted.copy()
            if unit["flags"] & 0x80 and rendered:
                fraction = fade_step
                for sample_index in range(min(fade_samples, len(rendered))):
                    rendered[sample_index] = _interpolate(
                        fraction, previous_sample, rendered[sample_index]
                    )
                    fraction += fade_step
                previous_sample = rendered[-1]
            if event.volume < 5:
                shift = 5 - event.volume
                if signed:
                    rendered = [sample >> shift for sample in rendered]
                else:
                    rendered = [0x80 + ((sample - 0x80) >> shift) for sample in rendered]
            output.extend(rendered)

    if signed:
        return struct.pack(f"<{len(output)}h", *output)
    return bytes(max(0, min(255, value)) for value in output)


def render(
    voice: Path,
    phonetics: str,
    output: Path,
    *,
    mode: str = "raw",
    speed: int = 5,
    pitch: int = 5,
) -> list[int]:
    manifest, pcmd = inspect_voice(voice)
    sequence = selected_units(manifest, phonetics)
    if mode == "scheduled":
        events = scheduled_events(manifest, phonetics)
        sequence = [event.unit_index for event in events if event.unit_index is not None]
        frames = _scheduled_frames(manifest, pcmd, events, speed=speed, pitch=pitch)
    else:
        frames = bytearray()
        for index in sequence:
            unit = manifest["units"][index]
            start = unit["byte_offset"]
            frames.extend(pcmd[unit["resource_index"]][start : start + unit["byte_length"]])
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(manifest["bits_per_sample"] // 8)
        stream.setframerate(manifest["sample_rate"])
        stream.writeframes(frames)
    return sequence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("voice", type=Path)
    parser.add_argument("phonetics")
    parser.add_argument("output", type=Path)
    parser.add_argument("--mode", choices=("raw", "scheduled"), default="raw")
    parser.add_argument("--speed", type=int, choices=range(10), default=5)
    parser.add_argument("--pitch", type=int, choices=range(10), default=5)
    args = parser.parse_args()
    sequence = render(
        args.voice, args.phonetics, args.output,
        mode=args.mode, speed=args.speed, pitch=args.pitch,
    )
    print(f"wrote {args.output} from {len(sequence)} selected INST units: {sequence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
