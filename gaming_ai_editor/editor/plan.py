"""Construction du plan de montage (EDL) à partir des moments forts.

Un plan peut mélanger plusieurs vidéos sources (compilation).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

MAX_XFADE_CLIPS = 24


@dataclass
class Segment:
    source: str
    start: float
    end: float
    score: float = 0.0
    label: str = ""
    peak: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class EditPlan:
    format: str  # "short" | "long"
    width: int
    height: int
    fps: float
    segments: list[Segment] = field(default_factory=list)
    transition: str = "cut"
    transition_seconds: float = 0.0
    min_seconds: float = 0.0
    max_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    title: str = ""

    @property
    def duration(self) -> float:
        total = sum(s.duration for s in self.segments)
        if self.transition == "xfade" and len(self.segments) > 1:
            total -= (len(self.segments) - 1) * self.transition_seconds
        return total

    def to_dict(self) -> dict:
        data = asdict(self)
        data["duration"] = round(self.duration, 3)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "EditPlan":
        data = dict(data)
        data.pop("duration", None)
        segments = [Segment(**s) for s in data.pop("segments", [])]
        return cls(segments=segments, **data)


def _candidate(h: dict, source: str, duration: float, fc: dict) -> Segment:
    clip_min, clip_max = float(fc["clip_min"]), float(fc["clip_max"])
    start = max(0.0, h["start"] - float(fc["pre_roll"]))
    end = min(duration, h["end"] + float(fc["post_roll"]))
    if end - start > clip_max:
        start = max(start, min(h["start"] - float(fc["pre_roll"]), h["peak"] - 0.6 * clip_max))
        start = max(0.0, start)
        end = min(duration, start + clip_max)
    if end - start < clip_min:
        missing = clip_min - (end - start)
        start = max(0.0, start - missing / 2)
        end = min(duration, start + clip_min)
        start = max(0.0, end - clip_min)
    return Segment(source, start, end, float(h["score"]), h.get("label", ""), float(h["peak"]))


def _total(segments: list[Segment]) -> float:
    return sum(s.duration for s in segments)


def _merge_same_source(segments: list[Segment], gap: float) -> list[Segment]:
    out: list[Segment] = []
    for seg in sorted(segments, key=lambda s: (s.source, s.start)):
        if out and out[-1].source == seg.source and seg.start - out[-1].end <= gap:
            last = out[-1]
            last.end = max(last.end, seg.end)
            if seg.score > last.score:
                last.score, last.label, last.peak = seg.score, seg.label, seg.peak
        else:
            out.append(Segment(**asdict(seg)))
    return out


def _snap(value: float, fps: float) -> float:
    return round(round(value * fps) / fps, 6)


def build_plan(fmt: str, analyses: list[dict], cfg, title: str = "") -> EditPlan:
    """Sélectionne les meilleurs passages pour atteindre la durée cible du format."""
    fc = cfg[fmt]
    durations = {a["path"]: float(a["duration"]) for a in analyses}
    fps = min(float(cfg["render"]["max_fps"]),
              max([round(a.get("fps") or 30) for a in analyses] or [30]))
    fps = float(fps or 30)
    plan = EditPlan(format=fmt, width=int(fc["width"]), height=int(fc["height"]), fps=fps,
                    transition=fc.get("transition", "cut"),
                    transition_seconds=float(fc.get("transition_seconds", 0.0)),
                    min_seconds=float(fc["min_seconds"]), max_seconds=float(fc["max_seconds"]),
                    title=title)
    total_source = sum(durations.values())
    target = min(float(fc["target_seconds"]), float(fc["max_seconds"]))
    min_s, max_s = float(fc["min_seconds"]), float(fc["max_seconds"])
    merge_gap = float(fc.get("merge_gap", 1.0))

    # Source trop courte pour ce format : on garde tout, la QA le signalera.
    if total_source <= min_s:
        plan.segments = [Segment(a["path"], 0.0, float(a["duration"]), 0.0, "source entière")
                         for a in analyses]
        plan.transition, plan.transition_seconds = "cut", 0.0
        plan.notes.append(
            f"Sources trop courtes ({total_source:.0f}s) pour la durée minimale du format "
            f"({min_s:.0f}s) : contenu intégral conservé.")
        _finalize(plan, durations)
        return plan

    candidates = []
    for a in analyses:
        for h in a.get("highlights", []):
            candidates.append(_candidate(h, a["path"], float(a["duration"]), fc))
    candidates.sort(key=lambda s: s.score, reverse=True)

    def overhead(n: int) -> float:
        return (n - 1) * plan.transition_seconds if plan.transition == "xfade" and n > 1 else 0.0

    chosen: list[Segment] = []
    for cand in candidates:
        merged = _merge_same_source(chosen + [cand], merge_gap)
        effective = _total(merged) - overhead(len(merged))
        if effective > max_s:
            room = max_s - (_total(chosen) - overhead(len(chosen) + 1))
            if room >= float(fc["clip_min"]):
                cut = Segment(**asdict(cand))
                cut.start = max(cut.start, min(cut.peak - room * 0.6, cut.end - room))
                cut.end = min(cut.end, cut.start + room)
                trial = _merge_same_source(chosen + [cut], merge_gap)
                if _total(trial) - overhead(len(trial)) <= max_s:
                    chosen = trial
            continue
        chosen = merged
        if effective >= target:
            break

    # Pas assez de matière : on élargit les passages retenus jusqu'à la durée minimale.
    guard = 0
    while chosen and _total(chosen) - overhead(len(chosen)) < min_s + 0.5 and guard < 2000:
        guard += 1
        before = _total(chosen)
        for seg in chosen:
            seg.start = max(0.0, seg.start - 1.0)
            seg.end = min(durations[seg.source], seg.end + 1.0)
        chosen = _merge_same_source(chosen, 0.0)
        if _total(chosen) <= before + 1e-6:
            break
    if _total(chosen) - overhead(len(chosen)) > max_s and chosen:
        excess = _total(chosen) - overhead(len(chosen)) - max_s
        longest = max(chosen, key=lambda s: s.duration)
        longest.end -= min(excess, longest.duration - 1.0)

    if plan.transition == "xfade" and len(chosen) > MAX_XFADE_CLIPS:
        plan.transition, plan.transition_seconds = "cut", 0.0
        plan.notes.append("Trop de clips pour des fondus : coupes franches utilisées.")

    order = {a["path"]: i for i, a in enumerate(analyses)}
    chosen.sort(key=lambda s: (order[s.source], s.start))
    if fmt == "short" and fc.get("hook_first") and len(chosen) > 2:
        hook = max(chosen, key=lambda s: s.score)
        chosen.remove(hook)
        chosen.insert(0, hook)
        plan.notes.append("Accroche : le moment le plus fort est placé en ouverture.")
    plan.segments = chosen
    _finalize(plan, durations)
    return plan


def _finalize(plan: EditPlan, durations: dict[str, float]) -> None:
    """Aligne les bornes sur la grille d'images et retire les clips trop courts."""
    cleaned = []
    for seg in plan.segments:
        seg.start = _snap(max(0.0, seg.start), plan.fps)
        seg.end = _snap(min(durations.get(seg.source, seg.end), seg.end), plan.fps)
        if seg.end > durations.get(seg.source, seg.end):
            seg.end -= 1.0 / plan.fps
        min_len = max(0.5, 2.5 * plan.transition_seconds)
        if seg.duration >= min_len:
            cleaned.append(seg)
    plan.segments = cleaned
    if len(plan.segments) < 2:
        plan.transition, plan.transition_seconds = "cut", 0.0
