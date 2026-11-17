"""6. Audio Intelligence Engine — musique, rythme et sound design.

- **Détection du tempo et des temps** d'une musique fournie (flux spectral +
  autocorrélation) : les coupes et les effets se calent dessus.
- **Musique générée** (aucun droit d'auteur) quand aucune musique n'est fournie :
  batterie, basse, nappe et mélodie, avec une **montée** (riser + roulement)
  avant le moment fort, un passage filtré pendant le ralenti et un **drop** juste
  après. La grille rythmique est alignée pour que le drop tombe pile au bon moment.
- **Bibliothèque d'effets sonores** synthétisés : whoosh, impact, explosion,
  riser, sub drop, notification, swoosh de texte, glitch, clic.
- **Piste d'effets** : chaque effet est placé à 1/48 000 s près sur la chronologie
  du montage, à partir des mêmes événements que les animations.

Tout est calculé avec NumPy ; FFmpeg ne fait que le mixage final.
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np

from ..ffutil import _popen_kwargs, ffmpeg_bin

SR = 48000


# ---------------------------------------------------------------------------
# Entrées / sorties
# ---------------------------------------------------------------------------


def load_mono(path: str | Path, rate: int = 22050, max_seconds: float | None = None) -> np.ndarray:
    cmd = [ffmpeg_bin(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path)]
    if max_seconds:
        cmd += ["-t", f"{max_seconds:.3f}"]
    cmd += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, timeout=600, **_popen_kwargs()).stdout
    return np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0


def write_wav(path: str | Path, stereo: np.ndarray, rate: int = SR) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.clip(stereo, -1.0, 1.0)
    if data.ndim == 1:
        data = np.stack([data, data], axis=1)
    pcm = (data * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return path


# ---------------------------------------------------------------------------
# Analyse rythmique
# ---------------------------------------------------------------------------


def onset_envelope(y: np.ndarray, rate: int, n_fft: int = 1024, hop: int = 512):
    if y.size < n_fft * 4:
        return np.zeros(1), np.zeros(1), hop / rate
    frames = np.lib.stride_tricks.sliding_window_view(y, n_fft)[::hop]
    mag = np.abs(np.fft.rfft(frames * np.hanning(n_fft), axis=1))
    logmag = np.log1p(100 * mag)
    flux = np.maximum(0.0, np.diff(logmag, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    bass_bins = max(2, int(150 * n_fft / rate))
    bass = np.maximum(0.0, np.diff(logmag[:, 1:bass_bins], axis=0)).sum(axis=1)
    bass = np.concatenate([[0.0], bass])
    flux = flux - np.convolve(flux, np.ones(16) / 16, mode="same")
    return np.maximum(flux, 0), bass, hop / rate


def detect_beats(path: str | Path, max_seconds: float = 240.0) -> dict:
    """Tempo (BPM), instants des temps et des premiers temps de mesure, intensité."""
    rate = 22050
    y = load_mono(path, rate, max_seconds)
    env, bass, step = onset_envelope(y, rate)
    duration = y.size / rate
    if env.size < 64 or env.max() <= 0:
        return {"bpm": 0.0, "beats": [], "downbeats": [], "duration": duration,
                "confidence": 0.0}
    env = env / (env.std() + 1e-9)
    best = (0.0, 0.0)
    ac = np.correlate(env, env, mode="full")[env.size - 1:]
    for bpm in np.arange(70.0, 181.0, 0.5):
        lag = 60.0 / bpm / step
        lo, hi = int(np.floor(lag)), int(np.ceil(lag))
        if hi >= ac.size:
            continue
        val = ac[lo] + (ac[hi] - ac[lo]) * (lag - lo)
        val += 0.5 * np.interp(2 * lag, np.arange(ac.size), ac)  # cohérence sur 2 temps
        prior = np.exp(-0.5 * (np.log2(bpm / 130.0) / 0.6) ** 2)  # tempos de montage typiques
        score = val * prior
        if score > best[0]:
            best = (score, bpm)
    def grid_fit(bpm_value: float) -> tuple[float, float, list[float]]:
        """Somme de l'enveloppe sur la grille de temps la mieux placée pour ce tempo."""
        per = 60.0 / bpm_value / step
        ph = np.arange(0, per, 0.25)
        grid = np.arange(0, env.size, per)
        totals = [float(np.interp(p + grid[grid + p < env.size - 1], np.arange(env.size),
                                  env).sum()) for p in ph]
        i = int(np.argmax(totals))
        return totals[i], float(ph[i]), totals

    # affinage : le tempo qui aligne le mieux la grille sur les attaques réelles
    coarse = float(best[1])
    fine = max(np.arange(coarse - 2.0, coarse + 2.01, 0.05), key=lambda b: grid_fit(b)[0])
    bpm = float(fine)
    period = 60.0 / bpm / step
    _, phase, sums = grid_fit(bpm)
    beats = [round((phase + k * period) * step, 4)
             for k in range(int((env.size - phase) / period) + 1)]
    idx = np.round(np.array(beats) / step).astype(int).clip(0, bass.size - 1)
    bar_scores = [bass[idx[o::4]].sum() for o in range(4)]
    first = int(np.argmax(bar_scores))
    confidence = float(np.max(sums) / (np.mean(sums) + 1e-9))
    return {"bpm": round(bpm, 2), "beats": beats, "downbeats": beats[first::4],
            "duration": round(duration, 3), "confidence": round(confidence, 3)}


# ---------------------------------------------------------------------------
# Synthèse
# ---------------------------------------------------------------------------


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(seconds * SR)) / SR


def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    return (1 - np.exp(-t / max(attack, 1e-4))) * np.exp(-t / max(decay, 1e-4))


def _fft_filter(x: np.ndarray, low: float | None = None, high: float | None = None) -> np.ndarray:
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(x.size, 1 / SR)
    mask = np.ones_like(f)
    if high:
        mask *= 1 / np.sqrt(1 + (f / high) ** 4)
    if low:
        mask *= 1 / np.sqrt(1 + (low / np.maximum(f, 1e-3)) ** 4)
    return np.fft.irfft(spec * mask, n=x.size)


def _sweep(x: np.ndarray, f0: float, f1: float, chunks: int = 24, band: bool = True) -> np.ndarray:
    """Filtre passe-bande dont la fréquence glisse de f0 à f1 (overlap-add)."""
    n = x.size
    size = max(256, n // chunks * 2)
    hop = size // 2
    out = np.zeros(n + size)
    win = np.hanning(size)
    positions = range(0, n, hop)
    count = max(1, len(positions) - 1)
    for k, p in enumerate(positions):
        seg = np.zeros(size)
        part = x[p:p + size]
        seg[: part.size] = part
        fc = f0 * (f1 / f0) ** (k / count)
        filt = _fft_filter(seg * win, low=fc * 0.5 if band else None, high=fc * 1.8)
        out[p:p + size] += filt
    return out[:n]


def _norm(x: np.ndarray, peak: float = 0.9) -> np.ndarray:
    m = float(np.abs(x).max()) if x.size else 0.0
    return x * (peak / m) if m > 0 else x


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def sfx(kind: str, seed: int = 0) -> np.ndarray:
    """Effet sonore mono (float32, 48 kHz)."""
    rng = _rng(seed)
    if kind == "whoosh":
        d = 0.5
        n = _rng(seed).standard_normal(int(d * SR))
        x = _sweep(n, 300, 6000) * np.sin(np.pi * np.linspace(0, 1, n.size)) ** 1.5
        return _norm(x, 0.8)
    if kind == "swoosh":
        d = 0.2
        x = _fft_filter(rng.standard_normal(int(d * SR)), low=2500) * _env(int(d * SR), 0.01, 0.06)
        return _norm(x, 0.5)
    if kind == "impact":
        t = _t(0.7)
        f = 35 + 85 * np.exp(-t / 0.05)
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.18)
        click = _fft_filter(rng.standard_normal(t.size), high=4000) * np.exp(-t / 0.012)
        return _norm(np.tanh(2.2 * (body + 0.5 * click)), 0.95)
    if kind == "boom":
        t = _t(1.8)
        f = 30 + 60 * np.exp(-t / 0.12)
        sub = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.5)
        rumble = _fft_filter(rng.standard_normal(t.size), high=600) * np.exp(-t / 0.45)
        crack = _fft_filter(rng.standard_normal(t.size), low=1500) * np.exp(-t / 0.03)
        return _norm(np.tanh(1.8 * (sub + 0.9 * _norm(rumble, 1) + 0.4 * _norm(crack, 1))), 0.95)
    if kind == "riser":
        d = 1.6
        t = _t(d)
        noise = _sweep(rng.standard_normal(t.size), 200, 9000)
        tone = np.sin(2 * np.pi * np.cumsum(220 * 4 ** (t / d)) / SR)
        x = (_norm(noise, 1) + 0.35 * tone) * (t / d) ** 2
        return _norm(x, 0.7)
    if kind == "sub_drop":
        t = _t(0.9)
        f = 30 + 70 * np.exp(-t / 0.25)
        return _norm(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.4), 0.9)
    if kind == "ding":
        t = _t(0.45)
        x = (np.sin(2 * np.pi * 1318.5 * t) + 0.6 * np.sin(2 * np.pi * 1975.5 * t)) * \
            np.exp(-t / 0.12)
        x[int(0.07 * SR):] += 0.8 * np.sin(2 * np.pi * 1760 * t[: t.size - int(0.07 * SR)]) * \
            np.exp(-t[: t.size - int(0.07 * SR)] / 0.15)
        return _norm(x, 0.45)
    if kind == "glitch":
        d = 0.3
        n = int(d * SR)
        x = np.zeros(n)
        pos = 0
        while pos < n:
            length = int(rng.integers(300, 2400))
            freq = float(rng.choice([180, 440, 880, 1600, 3200]))
            seg = np.sign(np.sin(2 * np.pi * freq * np.arange(length) / SR))
            if rng.random() < 0.4:
                seg = rng.standard_normal(length)
            x[pos:pos + length] = seg[: n - pos] * rng.uniform(0.2, 1.0)
            pos += length + int(rng.integers(0, 800))
        return _norm(np.round(x * 6) / 6, 0.45)
    if kind == "click":
        t = _t(0.06)
        return _norm(_fft_filter(rng.standard_normal(t.size), low=1500) * np.exp(-t / 0.006), 0.5)
    raise ValueError(f"effet sonore inconnu : {kind}")


SFX_KINDS = ("whoosh", "swoosh", "impact", "boom", "riser", "sub_drop", "ding", "glitch", "click")


def render_sfx_track(cues: list[dict], duration: float, path: str | Path,
                     samples: dict | None = None) -> Path:
    """Place les effets {t, kind, gain, pan} sur une piste stéréo de la durée du montage.

    `t` est l'instant où l'effet doit « frapper » ; pour un riser c'est la fin.
    `samples` remplace un effet synthétisé par un son fourni (ex. assets/sfx/whoosh.wav).
    """
    samples = samples or {}
    n = int(duration * SR) + 1
    out = np.zeros((n, 2), dtype=np.float64)
    cache: dict[tuple, np.ndarray] = {}
    for i, cue in enumerate(cues):
        kind = cue["kind"]
        key = (kind, i % 3)
        if key not in cache:
            cache[key] = samples[kind] if kind in samples else sfx(kind, seed=7 * (i % 3) + 1)
        x = cache[key] * float(cue.get("gain", 1.0))
        start = float(cue["t"])
        if kind == "riser":
            start -= x.size / SR
        elif kind == "whoosh":
            start -= 0.25
        a = int(round(start * SR))
        if a >= n:
            continue
        b0 = max(0, -a)
        a = max(0, a)
        seg = x[b0: b0 + n - a]
        pan = float(cue.get("pan", 0.0))
        out[a:a + seg.size, 0] += seg * np.sqrt(0.5 * (1 - pan))
        out[a:a + seg.size, 1] += seg * np.sqrt(0.5 * (1 + pan))
    out = np.tanh(out * 1.1) * 0.95  # limiteur doux
    return write_wav(path, out.astype(np.float32))


# ---------------------------------------------------------------------------
# Musique générée
# ---------------------------------------------------------------------------

_SCALES = {"minor": [0, 2, 3, 5, 7, 8, 10], "phrygian": [0, 1, 3, 5, 7, 8, 10],
           "dorian": [0, 2, 3, 5, 7, 9, 10]}
_PROGRESSIONS = [[0, 5, 2, 6], [0, 3, 5, 4], [0, 6, 5, 4], [0, 5, 3, 4]]


def _note(root: float, degree: int, scale: list[int], octave: int = 0) -> float:
    semis = scale[degree % 7] + 12 * (degree // 7 + octave)
    return root * 2 ** (semis / 12)


def _saw(freq: float, n: int, detune: float = 0.0) -> np.ndarray:
    t = np.arange(n) / SR
    x = 2 * ((t * freq * (1 + detune)) % 1.0) - 1
    return x


def music_grid(duration: float, bpm: float, drop_at: float | None) -> float:
    """Origine de la grille rythmique : un début de mesure tombe exactement sur `drop_at`."""
    bar = 4 * 60.0 / bpm
    if drop_at is None:
        return 0.0
    return drop_at - np.ceil(drop_at / bar) * bar


def generate_music(path: str | Path, duration: float, bpm: float = 140.0,
                   drop_at: float | None = None, calm: tuple[float, float] | None = None,
                   seed: int = 0, energy: float = 1.0) -> dict:
    """Morceau de la durée du montage. Retourne {path, bpm, beats, downbeats}.

    - `drop_at` : instant du drop (la mesure entière démarre là) ; montée de 2 mesures avant.
    - `calm` : (début, fin) passage filtré et allégé (pendant un ralenti).
    """
    rng = _rng(seed)
    beat = 60.0 / bpm
    bar = 4 * beat
    origin = music_grid(duration, bpm, drop_at)
    n = int((duration + 0.5) * SR)
    drums = np.zeros(n)
    bassline = np.zeros(n)
    synth = np.zeros(n)
    root = 55.0 * 2 ** (int(rng.integers(0, 7)) / 12)  # La1 à Mi2
    scale = _SCALES[list(_SCALES)[int(rng.integers(0, len(_SCALES)))]]
    prog = _PROGRESSIONS[int(rng.integers(0, len(_PROGRESSIONS)))]
    arp = [int(x) for x in rng.choice([0, 2, 4, 7, 4, 2, 9, 7], size=8)]

    t_k = _t(0.45)
    kick = np.sin(2 * np.pi * np.cumsum(45 + 110 * np.exp(-t_k / 0.03)) / SR) * \
        np.exp(-t_k / 0.16)
    kick = np.tanh(2.5 * kick) * 0.9
    snare_n = int(0.25 * SR)
    snare = (_fft_filter(rng.standard_normal(snare_n), low=1200, high=9000) * _env(snare_n, 0.001, 0.07)
             + 0.4 * np.sin(2 * np.pi * 190 * np.arange(snare_n) / SR) * _env(snare_n, 0.001, 0.05))
    snare = _norm(snare, 0.55)
    hat_n = int(0.06 * SR)
    hat = _norm(_fft_filter(rng.standard_normal(hat_n), low=7000) * _env(hat_n, 0.0005, 0.015), 0.25)

    def add(buf: np.ndarray, sample: np.ndarray, at: float, gain: float = 1.0) -> None:
        a = int(round(at * SR))
        if a >= n or a + sample.size <= 0:
            return
        s0 = max(0, -a)
        a = max(0, a)
        seg = sample[s0: s0 + n - a]
        buf[a:a + seg.size] += seg * gain

    build_start = drop_at - 2 * bar if drop_at is not None else None

    def section(t: float) -> str:
        if calm and calm[0] <= t < calm[1]:
            return "calm"
        if drop_at is not None and build_start <= t < drop_at:
            return "build"
        if t >= duration - bar:
            return "outro"
        return "main"

    k = 0
    while True:
        t = origin + k * beat / 2  # croches
        if t > duration + beat:
            break
        k_beat = k // 2
        off = k % 2
        sec = section(max(t, 0.0))
        bar_idx = int(np.floor((t - origin) / bar))
        chord = prog[bar_idx % len(prog)]
        if sec == "calm":
            if off == 0 and k_beat % 4 == 0:
                add(drums, kick, t, 0.7)
        elif sec == "build":
            prog_build = (t - build_start) / (2 * bar)
            if off == 0 and k_beat % 4 != 3:
                add(drums, kick, t, 0.8)
            add(drums, snare, t, 0.25 + 0.5 * prog_build)
            if prog_build > 0.5:  # roulement : doubles croches à la fin
                add(drums, snare, t + beat / 4, 0.3 + 0.4 * prog_build)
        else:
            gain = 0.6 if sec == "outro" else 1.0
            if off == 0:
                add(drums, kick, t, gain)
                if k_beat % 2 == 1:
                    add(drums, snare, t, gain)
            add(drums, hat, t + (beat / 4 if off == 0 else 0), 0.8 * gain)
            freq = _note(root, chord, scale)
            note_n = int(beat / 2 * SR * 0.9)
            bnote = (np.sign(np.sin(2 * np.pi * freq * np.arange(note_n) / SR)) * 0.5
                     + _saw(freq, note_n) * 0.5) * _env(note_n, 0.003, 0.18)
            add(bassline, bnote, t, 0.55 * gain * energy)
            lead_f = _note(root, chord + arp[k % 8], scale, octave=3)
            lead_n = int(beat / 2 * SR)
            lnote = (_saw(lead_f, lead_n) + _saw(lead_f, lead_n, 0.006)) * _env(lead_n, 0.002, 0.09)
            add(synth, lnote, t, 0.16 * gain * energy)
        if off == 0 and k_beat % 4 == 0 and sec != "build":  # nappe : un accord par mesure
            pad_n = int(bar * SR)
            pad = sum(_saw(_note(root, chord + d, scale, 2), pad_n, det)
                      for d in (0, 2, 4) for det in (-0.004, 0.004))
            pad = pad * np.minimum(1, np.arange(pad_n) / (0.2 * SR)) * \
                np.minimum(1, (pad_n - np.arange(pad_n)) / (0.3 * SR))
            add(synth, pad, t, 0.035 if sec == "calm" else 0.05)
        k += 1

    bass_f = _fft_filter(bassline, high=700)
    synth_f = _fft_filter(synth, high=5000)
    mix = drums + bass_f + synth_f
    if drop_at is not None:
        riser = sfx("riser", seed)
        add(mix, riser * 0.7, drop_at - riser.size / SR)
        add(mix, sfx("sub_drop", seed) * 0.9, drop_at)
    if calm:  # passage étouffé (filtre passe-bas) avec fondus
        a, b = int(calm[0] * SR), int(calm[1] * SR)
        a, b = max(0, a), min(n, b)
        if b > a:
            muffled = _fft_filter(mix, high=500)
            w = np.zeros(n)
            ramp = int(0.15 * SR)
            w[a:b] = 1
            w[a:a + ramp] = np.linspace(0, 1, min(ramp, b - a))
            w[max(a, b - ramp):b] = np.linspace(1, 0, b - max(a, b - ramp))
            mix = mix * (1 - w) + muffled * w * 0.8
    mix = _norm(np.tanh(mix * 1.2), 0.85)
    fade = int(min(1.5, duration / 4) * SR)
    mix[-fade:] *= np.linspace(1, 0, fade)
    left = mix
    right = np.roll(mix, int(0.008 * SR)) * 0.96 + mix * 0.04  # légère largeur stéréo
    write_wav(path, np.stack([left, right], axis=1)[: int(duration * SR)].astype(np.float32))
    beats = [round(origin + i * beat, 4) for i in range(int((duration - origin) / beat) + 1)
             if origin + i * beat >= 0]
    downbeats = [b for b in beats if abs(((b - origin) / bar) - round((b - origin) / bar)) < 1e-3]
    return {"path": str(path), "bpm": bpm, "beats": beats, "downbeats": downbeats,
            "origin": round(origin, 4)}
