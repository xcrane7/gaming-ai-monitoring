"""Génération de « gameplays » synthétiques avec vérité terrain connue.

Sert aux tests, aux benchmarks et au Skills Researcher : on connaît exactement
où sont les moments d'action et les changements de plan, ce qui permet de
mesurer objectivement une méthode.
"""
from __future__ import annotations

from pathlib import Path

from .ffutil import run_ffmpeg

_SOURCES = [
    "testsrc2=s={w}x{h}:r={fps}:d={d}",
    "testsrc=s={w}x{h}:r={fps}:d={d}",
    "smptehdbars=s={w}x{h}:r={fps}:d={d}",
    "gradients=s={w}x{h}:r={fps}:d={d}:speed=0.05",
    "rgbtestsrc=s={w}x{h}:r={fps}:d={d}",
    "cellauto=s={w}x{h}:r={fps}:rule=110,trim=duration={d},setpts=PTS-STARTPTS",
]


def default_bursts(duration: float, every: float = 25.0, length: float = 4.0,
                   first: float = 12.0) -> list[tuple[float, float]]:
    bursts = []
    t = first
    while t + length < duration - 2:
        bursts.append((round(t, 2), round(t + length, 2)))
        t += every
    return bursts


def make_gameplay(path: str | Path, duration: float = 90.0, width: int = 1280,
                  height: int = 720, fps: int = 30,
                  bursts: list[tuple[float, float]] | None = None,
                  cut_every: float | None = 15.0, audio: bool = True,
                  noise: int = 70) -> dict:
    """Crée une vidéo de test. Retourne la vérité terrain {bursts, cuts}."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    bursts = default_bursts(duration) if bursts is None else bursts
    seg = cut_every if cut_every and cut_every < duration else duration
    count = max(1, int(round(duration / seg)))
    seg = duration / count
    parts = []
    for i in range(count):
        src = _SOURCES[i % len(_SOURCES)].format(w=width, h=height, fps=fps, d=f"{seg:.3f}")
        parts.append(f"{src},format=yuv420p,setsar=1[s{i}]")
    labels = "".join(f"[s{i}]" for i in range(count))
    enable = "+".join(f"between(t,{a},{b})" for a, b in bursts) or "0"
    graph = ";".join(parts) + f";{labels}concat=n={count}:v=1:a=0[cat];"
    graph += f"[cat]noise=alls={noise}:allf=t:enable='{enable}'[v]"
    args = ["-f", "lavfi", "-i", f"sine=f=180:r=48000:d={duration}",
            "-f", "lavfi", "-i", f"anoisesrc=r=48000:c=pink:a=0.9:d={duration}"]
    if audio:
        graph += (f";[0:a]volume=0.04[bg];[1:a]volume='if({enable},1,0)':eval=frame[burst];"
                  "[bg][burst]amix=inputs=2:normalize=0,aformat=channel_layouts=stereo[a]")
    args += ["-filter_complex", graph, "-map", "[v]"]
    if audio:
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", "128k"]
    args += ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
             "-t", f"{duration}", str(path)]
    run_ffmpeg(args)
    cuts = [round(seg * i, 3) for i in range(1, count)]
    return {"bursts": bursts, "cuts": cuts, "duration": duration}


def make_tone(path: str | Path, duration: float = 30.0, freq: int = 330) -> None:
    """Petite piste musicale synthétique (libre de droits, pour les tests)."""
    run_ffmpeg(["-f", "lavfi", "-i",
                f"sine=f={freq}:r=48000:d={duration},tremolo=f=4:d=0.6,"
                "aformat=channel_layouts=stereo",
                "-c:a", "pcm_s16le", str(path)])


def make_sfx(sfx_dir: str | Path) -> list[Path]:
    """Génère des effets sonores procéduraux (aucun droit d'auteur)."""
    sfx_dir = Path(sfx_dir)
    sfx_dir.mkdir(parents=True, exist_ok=True)
    whoosh = sfx_dir / "whoosh.wav"
    impact = sfx_dir / "impact.wav"
    run_ffmpeg(["-f", "lavfi", "-i",
                "anoisesrc=r=48000:c=white:a=0.8:d=0.45,highpass=f=500,lowpass=f=5000,"
                "afade=t=in:d=0.28:curve=qsin,afade=t=out:st=0.3:d=0.15,"
                "aformat=channel_layouts=stereo",
                "-c:a", "pcm_s16le", str(whoosh)])
    run_ffmpeg(["-f", "lavfi", "-i",
                "sine=f=55:r=48000:d=0.5,afade=t=out:st=0.02:d=0.48,volume=2.5,"
                "aformat=channel_layouts=stereo",
                "-c:a", "pcm_s16le", str(impact)])
    return [whoosh, impact]
