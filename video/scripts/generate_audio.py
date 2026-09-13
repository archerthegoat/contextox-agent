#!/usr/bin/env python3
"""Generate the original 96 BPM ContextOx product-film mix.

The soundtrack is synthesized from deterministic oscillators and seeded noise.
It contains no third-party samples, speech, or downloaded material.
"""

from __future__ import annotations

from array import array
import math
import os
from pathlib import Path
import random
import subprocess
import tempfile
import wave


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public" / "audio" / "contextox-v2-mix.wav"
FFMPEG_DIR = ROOT / "node_modules" / "@remotion" / "compositor-darwin-arm64"
FFMPEG = FFMPEG_DIR / "ffmpeg"

SAMPLE_RATE = 48_000
DURATION_SECONDS = 45
BPM = 96
BEAT_SECONDS = 60 / BPM
TAU = math.tau

SCENE_IMPACTS = (0.0, 4.0, 9.0, 15.0, 22.0, 29.0, 36.0, 41.0)
UI_CLICKS = (10.50, 11.50, 12.50, 14.60, 23.45, 25.00, 26.50, 28.15, 36.73)
CHIMES = (28.15, 29.05, 36.10, 41.05)
CHORDS = (
    (73.42, 110.00, 146.83),
    (58.27, 87.31, 116.54),
    (65.41, 98.00, 130.81),
    (55.00, 82.41, 110.00),
)


def envelope(time_value: float, start: float, duration: float, decay: float) -> float:
    delta = time_value - start
    if delta < 0 or delta >= duration:
        return 0.0
    attack = min(1.0, delta / 0.008)
    return attack * math.exp(-decay * delta)


def nearest_envelope(time_value: float, starts: tuple[float, ...], duration: float, decay: float) -> tuple[float, float]:
    best = (0.0, 0.0)
    for start in starts:
        amount = envelope(time_value, start, duration, decay)
        if amount > best[0]:
            best = (amount, time_value - start)
    return best


def synth_sample(time_value: float, noise: random.Random) -> tuple[float, float]:
    beat_index = int(time_value / BEAT_SECONDS)
    beat_phase = time_value - beat_index * BEAT_SECONDS
    chord = CHORDS[(beat_index // 8) % len(CHORDS)]

    fade_in = min(1.0, time_value / 0.45)
    fade_out = min(1.0, max(0.0, (DURATION_SECONDS - time_value) / 0.8))
    master = fade_in * fade_out

    pad_left = sum(math.sin(TAU * frequency * time_value + index * 0.9) for index, frequency in enumerate(chord)) * 0.018
    pad_right = sum(math.sin(TAU * frequency * time_value + index * 0.9 + 0.18) for index, frequency in enumerate(chord)) * 0.018

    pluck = 0.0
    if beat_phase < 0.32:
        pluck_frequency = chord[(beat_index + 1) % len(chord)] * 2
        pluck = 0.055 * math.exp(-8.5 * beat_phase) * math.sin(TAU * pluck_frequency * beat_phase)

    kick = 0.0
    if beat_phase < 0.2:
        kick_frequency = 78 - 42 * (beat_phase / 0.2)
        kick = 0.22 * math.exp(-17 * beat_phase) * math.sin(TAU * kick_frequency * beat_phase)

    hat_phase = (time_value - BEAT_SECONDS / 2) % BEAT_SECONDS
    hat = 0.0
    if hat_phase < 0.055:
        hat = (noise.random() * 2 - 1) * 0.032 * math.exp(-55 * hat_phase)

    impact_amount, impact_delta = nearest_envelope(time_value, SCENE_IMPACTS, 0.85, 5.5)
    impact = impact_amount * (
        0.18 * math.sin(TAU * (53 - 10 * min(impact_delta, 0.8)) * impact_delta)
        + 0.025 * math.sin(TAU * 420 * impact_delta)
    )

    click_amount, click_delta = nearest_envelope(time_value, UI_CLICKS, 0.085, 42)
    click = click_amount * (
        0.10 * math.sin(TAU * 1_720 * click_delta)
        + 0.045 * math.sin(TAU * 2_480 * click_delta)
    )

    chime_amount, chime_delta = nearest_envelope(time_value, CHIMES, 0.9, 3.5)
    chime = chime_amount * (
        0.065 * math.sin(TAU * 660 * chime_delta)
        + 0.045 * math.sin(TAU * 990 * chime_delta)
    )

    center = pluck + kick + hat + impact + click + chime
    left = max(-0.92, min(0.92, (pad_left + center) * master))
    right = max(-0.92, min(0.92, (pad_right + center * 0.97) * master))
    return left, right


def write_raw_mix(path: Path) -> None:
    generator = random.Random(20260913)
    block = array("h")
    total_samples = SAMPLE_RATE * DURATION_SECONDS
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        for sample_index in range(total_samples):
            left, right = synth_sample(sample_index / SAMPLE_RATE, generator)
            block.extend((int(left * 32767), int(right * 32767)))
            if len(block) >= SAMPLE_RATE * 2:
                output.writeframes(block.tobytes())
                block = array("h")
        if block:
            output.writeframes(block.tobytes())


def normalize(raw_path: Path) -> None:
    if not FFMPEG.is_file():
        raise FileNotFoundError(f"bundled ffmpeg not found: {FFMPEG}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["DYLD_LIBRARY_PATH"] = str(FFMPEG_DIR)
    subprocess.run(
        [
            str(FFMPEG),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(raw_path),
            "-af",
            "loudnorm=I=-16:TP=-1:LRA=7:linear=true",
            "-ar",
            str(SAMPLE_RATE),
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(OUTPUT),
        ],
        check=True,
        env=environment,
    )


def main() -> None:
    with tempfile.NamedTemporaryFile(prefix="contextox-v2-audio-", suffix=".wav", delete=False) as temporary:
        raw_path = Path(temporary.name)
    try:
        write_raw_mix(raw_path)
        normalize(raw_path)
    finally:
        raw_path.unlink(missing_ok=True)
    with wave.open(str(OUTPUT), "rb") as result:
        print(
            "generated",
            OUTPUT.relative_to(ROOT),
            f"{result.getnframes() / result.getframerate():.3f}s",
            f"{result.getframerate()}Hz",
            f"{result.getnchannels()}ch",
        )


if __name__ == "__main__":
    main()
