#!/usr/bin/env python3
"""Generate the original 100 BPM ContextOx V3 jazz-drums-and-bass mix.

Every sound is synthesized deterministically from oscillators and seeded noise.
The track contains no speech, third-party samples, or downloaded media.
"""

from __future__ import annotations

from array import array
from bisect import bisect_right
import math
import os
from pathlib import Path
import random
import subprocess
import tempfile
import wave


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public" / "audio" / "contextox-v3-jazz-mix.wav"
FFMPEG_DIR = ROOT / "node_modules" / "@remotion" / "compositor-darwin-arm64"
FFMPEG = FFMPEG_DIR / "ffmpeg"

SAMPLE_RATE = 48_000
DURATION_SECONDS = 45
BPM = 100
BEAT_SECONDS = 60 / BPM
SWING_RATIO = 0.58
TAU = math.tau

SCENE_BEATS = (0, 8, 16, 22, 34, 44, 54, 64, 69)
OPENING_HITS = tuple(beat * BEAT_SECONDS for beat in (1, 3, 5))
UI_CLICKS = tuple(
    beat * BEAT_SECONDS
    for beat in (24.0, 26.0, 28.0, 31.5, 46.0, 48.0, 50.0, 52.0, 65.0)
)
CONFIRM_IMPACTS = tuple(beat * BEAT_SECONDS for beat in (53.0, 54.0, 69.0))
BRUSH_FILLS = tuple(beat * BEAT_SECONDS for beat in (15.25, 15.5, 15.75, 43.25, 43.5, 43.75, 52.25, 52.5, 52.75))

BASS_ROOTS = (55.00, 65.41, 73.42, 49.00)  # A1, C2, D2, G1
BASS_STEPS = (0, 7, 10, 12, 10, 7, 5, 3)


def decay_envelope(delta: float, duration: float, decay: float, attack: float = 0.004) -> float:
    if delta < 0 or delta >= duration:
        return 0.0
    return min(1.0, delta / attack) * math.exp(-decay * delta)


def nearby_event(time_value: float, starts: tuple[float, ...], duration: float) -> float | None:
    index = bisect_right(starts, time_value) - 1
    if index < 0:
        return None
    delta = time_value - starts[index]
    return delta if delta < duration else None


def soft_clip(value: float) -> float:
    return math.tanh(value * 1.08) / math.tanh(1.08)


def bass_frequency(beat_index: int) -> float:
    section = min(3, beat_index // 18)
    root = BASS_ROOTS[section]
    semitones = BASS_STEPS[beat_index % len(BASS_STEPS)]
    return root * (2 ** (semitones / 12))


def synth_sample(time_value: float, noise: random.Random) -> tuple[float, float]:
    beat_position = time_value / BEAT_SECONDS
    beat_index = int(beat_position)
    beat_phase = time_value - beat_index * BEAT_SECONDS
    eighth_offset = BEAT_SECONDS * SWING_RATIO

    fade_in = min(1.0, time_value / 0.18)
    fade_out = min(1.0, max(0.0, (DURATION_SECONDS - time_value) / 1.0))
    master = fade_in * fade_out

    # Walking, muted bass. Notes are deliberately short so the drums lead.
    bass_phase = beat_phase
    bass_env = decay_envelope(bass_phase, 0.48, 4.8, attack=0.012)
    frequency = bass_frequency(beat_index)
    bass = bass_env * (
        0.19 * math.sin(TAU * frequency * bass_phase)
        + 0.045 * math.sin(TAU * frequency * 2 * bass_phase + 0.4)
    )
    if 16 <= beat_index < 22 or beat_index >= 69:
        bass *= 0.64

    # Dry kick: two-beat pulse with small syncopations in the product section.
    kick = 0.0
    if beat_index % 4 in (0, 2) or (22 <= beat_index < 64 and beat_index % 8 == 7):
        kick_env = decay_envelope(beat_phase, 0.24, 15, attack=0.002)
        kick_freq = 78 - 36 * min(1.0, beat_phase / 0.18)
        kick = kick_env * 0.34 * math.sin(TAU * kick_freq * beat_phase)

    # Rim/brush backbeat on 2 and 4 with a very short noisy tail.
    snare = 0.0
    if beat_index % 4 in (1, 3):
        snare_env = decay_envelope(beat_phase, 0.15, 24, attack=0.0015)
        snare = snare_env * (
            0.11 * (noise.random() * 2 - 1)
            + 0.055 * math.sin(TAU * 178 * beat_phase)
        )

    # Swung closed hats. The offbeat lives at 58% of each beat.
    hat = 0.0
    for offset, gain in ((0.0, 0.045), (eighth_offset, 0.058)):
        hat_delta = beat_phase - offset
        hat_env = decay_envelope(hat_delta, 0.055, 58, attack=0.0007)
        if hat_env:
            hat += gain * hat_env * (noise.random() * 2 - 1)

    # Brush/tom fills bridge from one scene to the next.
    brush = 0.0
    brush_delta = nearby_event(time_value, BRUSH_FILLS, 0.18)
    if brush_delta is not None:
        brush_env = decay_envelope(brush_delta, 0.18, 18, attack=0.001)
        brush = brush_env * (
            0.09 * (noise.random() * 2 - 1)
            + 0.08 * math.sin(TAU * 142 * brush_delta)
        )

    # Three opening punches and the confirm / outro impacts.
    impact = 0.0
    opening_delta = nearby_event(time_value, OPENING_HITS, 0.55)
    if opening_delta is not None:
        impact_env = decay_envelope(opening_delta, 0.55, 6.4, attack=0.001)
        impact = impact_env * (
            0.34 * math.sin(TAU * (62 - 20 * min(1.0, opening_delta / 0.4)) * opening_delta)
            + 0.035 * (noise.random() * 2 - 1)
        )
    confirm_delta = nearby_event(time_value, CONFIRM_IMPACTS, 0.72)
    if confirm_delta is not None:
        confirm_env = decay_envelope(confirm_delta, 0.72, 5.8, attack=0.001)
        impact += confirm_env * (
            0.28 * math.sin(TAU * (74 - 25 * min(1.0, confirm_delta / 0.55)) * confirm_delta)
            + 0.07 * math.sin(TAU * 151 * confirm_delta)
        )

    # UI clicks are short and sit above the groove without becoming a melody.
    click = 0.0
    click_delta = nearby_event(time_value, UI_CLICKS, 0.075)
    if click_delta is not None:
        click_env = decay_envelope(click_delta, 0.075, 52, attack=0.0005)
        click = click_env * (
            0.075 * math.sin(TAU * 1_780 * click_delta)
            + 0.035 * math.sin(TAU * 2_620 * click_delta)
        )

    # A low room tone glues the track but ducks during the comparison scene.
    room = 0.012 * math.sin(TAU * 55 * time_value) + 0.006 * math.sin(TAU * 82.41 * time_value + 0.9)
    if 9.6 <= time_value < 13.2:
        room *= 0.35
    elif 20.4 <= time_value < 38.4:
        room *= 0.72

    center = bass + kick + snare + hat + brush + impact + click + room
    stereo_motion = 0.012 * math.sin(TAU * 0.17 * time_value)
    left = soft_clip((center + stereo_motion * (hat + brush)) * master)
    right = soft_clip((center - stereo_motion * (hat + brush)) * 0.985 * master)
    return left, right


def write_raw_mix(path: Path) -> None:
    generator = random.Random(20260914)
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
    with tempfile.NamedTemporaryFile(prefix="contextox-v3-audio-", suffix=".wav", delete=False) as temporary:
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
            f"{BPM}bpm",
            f"{int(30 * BEAT_SECONDS)}frames/beat",
        )


if __name__ == "__main__":
    main()
