"""Construction du plan de montage (EDL) à partir des moments forts.

Un plan peut mélanger plusieurs vidéos sources (compilation).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

MAX_XFADE_CLIPS = 24


@dataclass
class Segment:
    source: str
    start: float
    end: float
    score: float = 0.0
    label: str = ""
    peak: float = 0.0
    events: list = field(default_factory=list)  # événements retenus et leurs effets
    ramp: list = field(default_factory=list)  # [début, fin, vitesse mini] (temps source)
    focus: list = field(default_factory=list)  # [[t du clip, x 0..1]] suivi de l'action
    camera: dict = field(default_factory=dict)  # poussée, panoramique, tremblements...
    vfx: dict = field(default_factory=dict)  # fenêtres des effets image (temps du clip)
    caption: str = ""
    role: str = ""  # "teaser" (cold open), "build", "climax" ou "" (format long)

    @property
    def duration(self) -> float:
        """Durée prise dans la source."""
        return self.end - self.start

    def speed_ramp(self):
        if len(self.ramp) != 3:
            return None
        from ..engines.motion import SpeedRamp

        return SpeedRamp(self.ramp[0] - self.start, self.ramp[1] - self.start, self.ramp[2])

    @property
    def out_duration(self) -> float:
        """Durée dans le montage (un ralenti allonge le clip)."""
        ramp = self.speed_ramp()
        return self.duration + (ramp.extra if ramp else 0.0)

    def map_time(self, t_src: float) -> float:
        """Temps source -> temps dans le clip monté (tient compte du ralenti)."""
        ramp = self.speed_ramp()
        rel = t_src - self.start
        return ramp.map(rel) if ramp else rel


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
    hook: str = ""  # texte d'accroche affiché au début
    style: dict = field(default_factory=dict)  # identité visuelle (Style DNA)
    transitions: list = field(default_factory=list)  # une transition xfade par jonction
    music: dict = field(default_factory=dict)  # mode, tempo, temps forts, drop
    graphics: list = field(default_factory=list)  # chronologie de la couche graphique
    sfx: list = field(default_factory=list)  # effets sonores placés
    effects: dict = field(default_factory=dict)  # résumé des effets utilisés
    transition_durations: list = field(default_factory=list)  # durée de chaque jonction
    loop: bool = False  # fin coupée net (boucle TikTok) : pas de fondu final

    def overlaps(self) -> list[float]:
        """Durée de chevauchement de chaque jonction (0 pour une coupe franche)."""
        n = max(0, len(self.segments) - 1)
        if self.transition != "xfade" or n == 0:
            return [0.0] * n
        if len(self.transition_durations) == n:
            return [float(d) for d in self.transition_durations]
        return [self.transition_seconds] * n

    def offsets(self) -> list[float]:
        """Instant de début de chaque clip dans le montage."""
        laps = self.overlaps() + [0.0]
        out, acc = [], 0.0
        for seg, lap in zip(self.segments, laps):
            out.append(acc)
            acc += seg.out_duration - lap
        return out

    def junctions(self) -> list[float]:
        """Instant visuel de chaque changement de clip (milieu de la transition)."""
        return [o + lap / 2 for o, lap in zip(self.offsets()[1:], self.overlaps())]

    @property
    def duration(self) -> float:
        return sum(s.out_duration for s in self.segments) - sum(self.overlaps())

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


def _adaptive_bounds(h: dict, analysis: dict, fc: dict) -> tuple[float, float] | None:
    """Bornes qui suivent l'action au lieu d'un décalage fixe.

    Début : le moment le plus calme (ou un changement de plan) dans les secondes qui
    précèdent l'action, pour qu'on voie la situation s'installer. Fin : quand l'intensité
    est vraiment retombée, plus un court temps de respiration.
    """
    score = np.asarray(analysis.get("score") or [], dtype=np.float64)
    w = float(analysis.get("window") or 0.5)
    if score.size < 8:
        return None
    duration = float(analysis["duration"])
    base = float(np.median(score))
    k_peak = min(score.size - 1, int(h["peak"] / w))
    top = max(float(score[k_peak]), base + 1e-3)
    lead_min, lead_max = float(fc.get("lead_min", 0.8)), float(fc.get("lead_max", 3.5))
    tail_max = float(fc.get("tail_max", 4.0))
    lo, hi = max(0.0, h["start"] - lead_max), max(0.0, h["start"] - lead_min)
    a, b = int(lo / w), max(int(lo / w) + 1, int(hi / w) + 1)
    window = score[a:b]
    start = lo + float(np.argmin(window)) * w if window.size else lo
    cuts = [c for c in analysis.get("scene_cuts") or [] if lo <= c <= hi]
    if cuts:  # un changement de plan est le meilleur point d'entrée
        start = min(cuts, key=lambda c: abs(c - start))
    settle = base + 0.35 * (top - base)
    k = min(score.size - 1, int(h["end"] / w))
    limit = min(score.size - 1, int((h["end"] + tail_max) / w))
    calm = 0
    while k < limit and calm < 2:
        calm = calm + 1 if score[k] < settle else 0
        k += 1
    end = max(h["end"] + 0.6, (k - calm) * w + 0.4)
    near = [c for c in analysis.get("scene_cuts") or [] if h["end"] + 0.3 <= c <= end + 0.6]
    if near:  # on coupe sur le changement de plan plutôt qu'au milieu d'un plan
        end = min(near)
    return max(0.0, start), min(duration, end)


def _candidate(h: dict, source: str, duration: float, fc: dict,
               analysis: dict | None = None) -> Segment:
    clip_min, clip_max = float(fc["clip_min"]), float(fc["clip_max"])
    bounds = None
    if analysis is not None and fc.get("bounds", "adaptive") == "adaptive":
        bounds = _adaptive_bounds(h, analysis, fc)
    if bounds:
        start, end = bounds
    else:
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
        ui = a.get("ui_windows") or []
        for h in a.get("highlights", []):
            if h.get("label") == "menu" and any(x.get("label") != "menu"
                                                 for x in a["highlights"]):
                continue  # on ne monte pas un menu tant qu'il reste du jeu
            cand = _candidate(h, a["path"], float(a["duration"]), fc, a)
            for u0, u1 in ui:  # rogne les bords qui débordent sur un menu
                if u0 <= cand.start < u1 < cand.peak:
                    cand.start = u1
                if cand.peak < u0 < cand.end <= u1 + 1e-6 or cand.peak < u0 < cand.end:
                    cand.end = min(cand.end, u0)
            candidates.append(cand)
    candidates.sort(key=lambda s: s.score, reverse=True)

    def overhead(n: int) -> float:
        return (n - 1) * plan.transition_seconds if plan.transition == "xfade" and n > 1 else 0.0

    chosen: list[Segment] = []
    for cand in candidates:
        merged = _merge_same_source(chosen + [cand], merge_gap)
        if len(merged) <= len(chosen) and \
                max(s.duration for s in merged) > 1.25 * float(fc["clip_max"]):
            continue  # ce passage fusionnerait en un clip trop long : on le laisse de côté
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

    # Bornes calées sur un changement de plan proche : les clips démarrent proprement.
    cuts = {a["path"]: a.get("scene_cuts") or [] for a in analyses}
    for seg in chosen:
        for cut in cuts.get(seg.source, []):
            if abs(cut - seg.start) <= 1.0 and cut < seg.peak - 1.0:
                seg.start = float(cut)
            elif abs(cut - seg.end) <= 1.0 and cut > seg.peak + 1.0:
                seg.end = float(cut)
    while chosen and _total(chosen) - overhead(len(chosen)) > max_s:
        longest = max(chosen, key=lambda s: s.duration)
        longest.end -= min(_total(chosen) - overhead(len(chosen)) - max_s,
                           longest.duration - 1.0)
        if longest.duration <= 1.0:
            break

    if plan.transition == "xfade" and len(chosen) > MAX_XFADE_CLIPS:
        plan.transition, plan.transition_seconds = "cut", 0.0
        plan.notes.append("Trop de clips pour des fondus : coupes franches utilisées.")

    order = {a["path"]: i for i, a in enumerate(analyses)}
    chosen.sort(key=lambda s: (order[s.source], s.start))
    structure = fc.get("structure", "story") if fmt == "short" else "chrono"
    if structure == "story" and len(chosen) >= 2:
        chosen = _story_order(chosen, plan, max_s)
    elif structure == "best_first" and len(chosen) > 2:
        hook = max(chosen, key=lambda s: s.score)
        chosen.remove(hook)
        chosen.insert(0, hook)
        plan.notes.append("Accroche : le moment le plus fort est placé en ouverture.")
    plan.loop = fmt == "short" and bool(fc.get("loop_ending", False))
    plan.segments = chosen
    _finalize(plan, durations)
    return plan


def _story_order(chosen: list[Segment], plan: EditPlan, max_s: float) -> list[Segment]:
    """Cold open (éclat du meilleur moment, coupé avant sa résolution) → montée
    chronologique → climax en entier à la fin."""
    climax = max(chosen, key=lambda s: s.score)
    build = [s for s in chosen if s is not climax]
    for s in build:
        s.role = "build"
    climax.role = "climax"
    t_start = max(climax.start, climax.peak - 1.6)
    t_end = min(climax.end, climax.peak + 0.35)
    ordered = build + [climax]
    if t_end - t_start >= 1.2:
        teaser = Segment(climax.source, t_start, t_end, climax.score, climax.label,
                         climax.peak, role="teaser")
        ordered = [teaser] + ordered
        plan.notes.append("Structure : cold open sur le meilleur moment, montée, climax à la fin.")
    else:
        plan.notes.append("Structure : montée chronologique, climax à la fin.")
    overlap = plan.transition_seconds if plan.transition == "xfade" else 0.0
    total = sum(s.duration for s in ordered) - overlap * (len(ordered) - 1)
    while total > max_s and build:  # la durée maximale reste respectée
        longest = max(build, key=lambda s: s.duration)
        cut = min(total - max_s, longest.duration - 2.0)
        if cut <= 0:
            break
        longest.end -= cut
        total -= cut
    return ordered


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
