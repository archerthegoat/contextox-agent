#!/usr/bin/env python3
"""Generate restrained, deterministic UI sounds for the ContextOx V4 film."""

from __future__ import annotations

from array import array
import math
from pathlib import Path
import random
import wave


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "public" / "audio" / "v4"
SAMPLE_RATE = 48_000
TAU = math.tau


def envelope(time_value: float, duration: float, decay: float, attack: float = 0.002) -> float:
    if time_value < 0 or time_value >= duration:
        return 0.0
    return min(1.0, time_value / attack) * math.exp(-decay * time_value)


def soft_clip(value: float) -> float:
    return math.tanh(value * 1.12) / math.tanh(1.12)


def write_sound(name: str, duration: float, synth) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    samples = array("h")
    noise = random.Random(f"contextox-v4:{name}:20260914")
    for index in range(round(duration * SAMPLE_RATE)):
        time_value = index / SAMPLE_RATE
        left, right = synth(time_value, noise)
        samples.extend((int(soft_clip(left) * 32767), int(soft_clip(right) * 32767)))
    path = OUTPUT / name
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(SAMPLE_RATE)
        output.writeframes(samples.tobytes())
    print(f"generated {path.relative_to(ROOT)} {duration:.3f}s")


def ui_click(time_value: float, noise: random.Random) -> tuple[float, float]:
    body = envelope(time_value, 0.13, 34) * (
        0.22 * math.sin(TAU * 1_180 * time_value)
        + 0.11 * math.sin(TAU * 1_760 * time_value + 0.4)
        + 0.035 * (noise.random() * 2 - 1)
    )
    return body, body * 0.94


def confirm_tick(time_value: float, noise: random.Random) -> tuple[float, float]:
    first = envelope(time_value, 0.36, 8.4, 0.004) * (
        0.15 * math.sin(TAU * 540 * time_value)
        + 0.08 * math.sin(TAU * 1_080 * time_value + 0.2)
    )
    second_time = time_value - 0.09
    second = envelope(second_time, 0.42, 7.6, 0.004) * (
        0.18 * math.sin(TAU * 720 * second_time)
        + 0.07 * math.sin(TAU * 1_440 * second_time + 0.25)
    )
    texture = envelope(time_value, 0.10, 30) * 0.012 * (noise.random() * 2 - 1)
    return first + second + texture, first * 0.92 + second + texture


def result_open(time_value: float, noise: random.Random) -> tuple[float, float]:
    duration = 0.72
    shape = math.sin(math.pi * min(1.0, time_value / duration)) ** 1.8
    sweep_frequency = 190 + 250 * (time_value / duration)
    tonal = shape * (
        0.065 * math.sin(TAU * sweep_frequency * time_value)
        + 0.035 * math.sin(TAU * sweep_frequency * 2 * time_value + 0.5)
    )
    brush = shape * 0.018 * (noise.random() * 2 - 1)
    return tonal + brush, tonal * 0.96 - brush * 0.7


def main() -> None:
    write_sound("ui-click.wav", 0.18, ui_click)
    write_sound("confirm-tick.wav", 0.58, confirm_tick)
    write_sound("result-open.wav", 0.72, result_open)


if __name__ == "__main__":
    main()
