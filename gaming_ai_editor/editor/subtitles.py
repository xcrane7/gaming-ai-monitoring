"""Sous-titres : lecture SRT, recalage sur le montage, écriture ASS, Whisper local optionnel."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .plan import EditPlan

_TIME = re.compile(r"(\d+):(\d\d):(\d\d)[,.](\d{1,3})")


@dataclass
class Cue:
    start: float
    end: float
    text: str


def _seconds(match: re.Match) -> float:
    h, m, s, ms = match.groups()
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms.ljust(3, "0")) / 1000


def parse_srt(text: str) -> list[Cue]:
    cues: list[Cue] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        for i, line in enumerate(lines):
            times = list(_TIME.finditer(line))
            if "-->" in line and len(times) >= 2:
                body = " ".join(lines[i + 1:]).strip()
                body = re.sub(r"<[^>]+>", "", body)
                if body:
                    cues.append(Cue(_seconds(times[0]), _seconds(times[1]), body))
                break
    return cues


def remap_cues(plan: EditPlan, cues_by_source: dict[str, list[Cue]]) -> list[Cue]:
    """Transpose les sous-titres des sources vers la chronologie du montage."""
    out: list[Cue] = []
    offset = 0.0
    overlap = plan.transition_seconds if plan.transition == "xfade" else 0.0
    for seg in plan.segments:
        for cue in cues_by_source.get(seg.source, []):
            start, end = max(cue.start, seg.start), min(cue.end, seg.end)
            if end - start >= 0.3:
                out.append(Cue(offset + start - seg.start, offset + end - seg.start, cue.text))
        offset += seg.duration - overlap
    out.sort(key=lambda c: c.start)
    for a, b in zip(out, out[1:]):  # pas de chevauchement à l'écran
        if a.end > b.start:
            a.end = max(a.start + 0.2, b.start - 0.02)
    return out


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def write_ass(cues: list[Cue], path: Path, width: int, height: int, font_size: int = 16) -> None:
    """Écrit un fichier ASS stylé (gros texte contouré, lisible sur mobile)."""
    vertical = height > width
    size = int(height * (0.042 if vertical else 0.055) * font_size / 16)
    margin_v = int(height * (0.22 if vertical else 0.07))
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,Arial,{size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{max(2, size // 12)},1,2,{int(width * 0.06)},"
        f"{int(width * 0.06)},{margin_v},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = []
    for cue in cues:
        text = cue.text.replace("\n", "\\N").replace("{", "(").replace("}", ")")
        lines.append(f"Dialogue: 0,{_ass_time(cue.start)},{_ass_time(cue.end)},Default,,0,0,0,,"
                     f"{text}")
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:
        return False


def transcribe(path: str | Path, model: str = "small", language: str | None = None) -> list[Cue]:
    """Transcription locale gratuite via faster-whisper (si installé)."""
    from faster_whisper import WhisperModel  # type: ignore

    try:
        wm = WhisperModel(model, device="cuda", compute_type="float16")
    except Exception:
        wm = WhisperModel(model, device="cpu", compute_type="int8")
    segments, _ = wm.transcribe(str(path), language=language, vad_filter=True)
    return [Cue(float(s.start), float(s.end), s.text.strip()) for s in segments if s.text.strip()]


def cues_for_source(source: str | Path, cfg: dict) -> tuple[list[Cue], str]:
    """Retourne (sous-titres, origine) pour une vidéo source selon la configuration."""
    mode = cfg.get("mode", "auto")
    if mode == "off":
        return [], "off"
    source = Path(source)
    if mode in ("auto", "sidecar"):
        sidecar = source.with_suffix(".srt")
        if sidecar.exists():
            return parse_srt(sidecar.read_text(encoding="utf-8-sig", errors="replace")), "sidecar"
    if mode in ("auto", "whisper") and whisper_available():
        try:
            return transcribe(source, cfg.get("whisper_model", "small"), cfg.get("language")), \
                "whisper"
        except Exception:
            return [], "whisper-error"
    return [], "none"
