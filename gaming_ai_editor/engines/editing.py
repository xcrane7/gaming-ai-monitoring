"""2. Intelligent Editing Engine — le réalisateur.

À partir du plan de base (quels passages garder) et des événements détectés,
il décide de tout le reste :

- **Style DNA** : identité visuelle de la vidéo (palette, police, look couleur,
  famille de transitions, intensité, cadrage). Elle découle du contenu (couleurs,
  rythme, mobilité de l'action) et d'une graine propre à chaque fichier : deux
  vidéos différentes n'ont pas le même habillage, la même vidéo rendue deux fois
  garde le sien. Une couleur et un nom de chaîne fixes dans la configuration
  donnent une identité commune à toute la chaîne.
- **Direction des effets** : chaque événement reçoit des effets adaptés à sa
  nature et à sa force (une explosion n'est pas traitée comme un tir), avec des
  délais minimum entre effets lourds pour ne jamais saturer l'image, et une
  rotation des variantes pour éviter la répétition.
- **Moment fort** : ralenti progressif + montée musicale + drop.
- **Rythme** : coupes calées sur les temps de la musique.
- **Textes** : accroche, mots d'impact, notifications (IA si disponible).
"""
from __future__ import annotations

import hashlib
import math
import random

from . import color
from .graphics import available_fonts
from .motion import tracking_x

PALETTES = {
    "toxic": {"accent": "#B6F24A", "accent2": "#3CE7C2", "text": "#FFFFFF", "shadow": "#000000"},
    "cyber": {"accent": "#FF2E88", "accent2": "#2EE6FF", "text": "#FFFFFF", "shadow": "#000000"},
    "inferno": {"accent": "#FF6A13", "accent2": "#FFC93C", "text": "#FFFFFF", "shadow": "#000000"},
    "ice": {"accent": "#4CC9FF", "accent2": "#A78BFA", "text": "#FFFFFF", "shadow": "#000000"},
    "royal": {"accent": "#FFC531", "accent2": "#9B5CFF", "text": "#FFFFFF", "shadow": "#000000"},
}
# teinte (0..1) dominante de chaque palette, pour choisir un contraste avec la vidéo
_PALETTE_HUE = {"toxic": 0.22, "cyber": 0.93, "inferno": 0.07, "ice": 0.55, "royal": 0.12}
TRANSITION_FAMILIES = {
    "aggressive": ["zoomin", "fadewhite", "hblur", "slideleft", "pixelize", "smoothup"],
    "smooth": ["smoothleft", "circleopen", "dissolve", "smoothup", "fade", "radial"],
    "glitchy": ["pixelize", "hlslice", "fadewhite", "hblur", "vdslice", "zoomin"],
}
WORDS = {
    "explosion": ["BOOM", "KABOOM", "ÇA PÈTE", "EXPLOSIF"],
    "impact": ["IMPACT", "DIRECT", "TOUCHÉ", "PAF"],
    "shot": ["PAN", "PRÉCIS", "DANS LE MILLE"],
    "burst": ["RAFALE", "SANS PITIÉ", "ENCHAÎNEMENT"],
    "rush": ["VITESSE MAX", "ÇA FILE", "TROP RAPIDE"],
    "flash": ["WOW", "AVEUGLANT"],
    "kill": ["ÉLIMINÉ", "AU SUIVANT", "NETTOYÉ"],
    "headshot": ["HEADSHOT"],
    "victoire": ["VICTOIRE", "GG"],
    "fail": ["OUPS", "AÏE", "RATÉ"],
    "drôle": ["MDR", "QUOI ?!", "N'IMPORTE QUOI"],
}
NOTIFY = {
    "explosion": ("Explosion", "bolt"), "impact": ("Impact", "crosshair"),
    "shot": ("Tir précis", "crosshair"), "burst": ("Rafale", "flame"),
    "kill": ("Élimination", "skull"), "headshot": ("Headshot", "crosshair"),
    "rush": ("Rush", "bolt"),
}
HOOKS = ["Attends la fin", "Regarde bien ça", "Personne ne s'attendait à ça",
         "Ça part trop loin", "Le meilleur moment de la partie"]
SEMANTIC = {"kill", "headshot", "victoire", "fail", "drôle"}


def seed_of(*parts) -> int:
    return int(hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


# ---------------------------------------------------------------------------
# Style DNA
# ---------------------------------------------------------------------------


def _hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 1.0
    return min(d, 1 - d)


def style_dna(analyses: list[dict], cfg, fmt: str, key: str) -> dict:
    st = cfg["style"]
    seed = seed_of(key)
    rng = random.Random(seed)
    prof = analyses[0].get("color") or {}
    pace = analyses[0].get("pace") or {}
    hue = float(prof.get("hue", 0.0))
    # palette : contraste avec la couleur dominante du jeu, départage par la graine
    ranked = sorted(PALETTES, key=lambda p: -_hue_distance(_PALETTE_HUE[p], hue)
                    + 0.08 * rng.random())
    palette = dict(PALETTES[ranked[0]])
    name = ranked[0]
    if st.get("accent_color"):
        palette["accent"], name = st["accent_color"], "chaîne"
    fonts = available_fonts() or ["DejaVu Sans Bold"]
    font = st.get("font") or fonts[seed % min(len(fonts), 2)]
    epm = float(pace.get("events_per_minute", 10))
    energy = min(1.0, 0.55 + epm / 40 + float(pace.get("motion_mean", 0.05)) * 2)
    family = ("aggressive" if energy > 0.8 else "smooth" if energy < 0.62
              else ["aggressive", "glitchy", "smooth"][seed % 3])
    intensity = round(energy * (1.0 if fmt == "short" else float(st["long_intensity"])), 3)
    layout = cfg["short"].get("layout", "auto") if fmt == "short" else "fit"
    if layout == "auto":
        spread = float((analyses[0].get("focus") or {}).get("spread", 0.0))
        layout = "track" if spread > 0.07 else "hybrid"
    look = color.choose_look(prof, seed) if prof else "teal_orange"
    return {
        "seed": seed, "palette_name": name, "palette": palette, "font": font,
        "look": look if st.get("grade", True) else None, "transitions": family,
        "intensity": intensity, "energy": round(energy, 3), "layout": layout,
        "bpm": float(rng.choice([128, 135, 140, 145, 150]) if energy > 0.7
                     else rng.choice([110, 118, 124])),
        "pan": rng.choice([-1, 1]) * (0.5 if fmt == "short" else 0.3),
        "word_angle": rng.choice([-6, -4, 4, 6]),
        "embers": name in ("inferno", "royal") and fmt == "short",
    }


# ---------------------------------------------------------------------------
# Direction des effets
# ---------------------------------------------------------------------------


def _semantic_label(analysis: dict, t: float) -> str | None:
    """Étiquette d'IA (Gemini ou Claude) d'un moment fort proche, s'il y en a une."""
    for h in analysis.get("highlights", []):
        if h.get("source") in ("gemini", "claude") and h["start"] - 1.0 <= t <= h["end"] + 1.0:
            label = str(h.get("label", ""))
            if label in SEMANTIC:
                return label
    return None


class Director:
    def __init__(self, plan, analyses: list[dict], cfg, texts: dict | None, key: str,
                 music_info: dict | None = None):
        self.plan = plan
        self.cfg = cfg
        self.by_path = {a["path"]: a for a in analyses}
        self.analyses = analyses
        self.texts = texts or {}
        self.dna = style_dna(analyses, cfg, plan.format, key)
        self.rng = random.Random(self.dna["seed"])
        self.music_info = music_info
        self.word_pool: dict[str, list[str]] = {}
        self.short = plan.format == "short"

    def _word(self, kind: str) -> str:
        pool = self.word_pool.get(kind)
        if not pool:
            pool = list(WORDS.get(kind, WORDS["impact"]))
            self.rng.shuffle(pool)
            self.word_pool[kind] = pool
        return pool.pop()

    # -- 1. événements et effets de chaque clip ----------------------------------
    def choose_events(self) -> None:
        k = self.dna["intensity"]
        for seg in self.plan.segments:
            a = self.by_path.get(seg.source, {})
            raw = [e for e in a.get("events", [])
                   if seg.start + 0.4 <= e["t"] <= seg.end - 0.4]
            raw.sort(key=lambda e: e.get("strength", 0), reverse=True)
            chosen: list[dict] = []
            limit = 4 if self.short else 3
            for e in raw:
                if len(chosen) >= limit:
                    break
                if any(abs(e["t"] - c["t"]) < 0.9 for c in chosen):
                    continue
                ev = dict(e)
                ev["label"] = _semantic_label(a, e["t"])
                chosen.append(ev)
            if not chosen and seg.peak and seg.start + 0.5 < seg.peak < seg.end - 0.5:
                chosen.append({"t": seg.peak, "kind": "impact", "strength": 0.4, "label": None})
            chosen.sort(key=lambda e: e["t"])
            last_heavy = -10.0
            for ev in chosen:
                kind, force = ev["kind"], float(ev.get("strength", 0.5))
                heavy_ok = ev["t"] - last_heavy >= 1.5
                fx: list[str] = []
                if kind == "explosion":
                    fx += ["punch", "flash", "explosion_gfx", "sfx_boom"]
                    if heavy_ok:
                        fx += ["shake", "glow"] + (["distortion"] if force > 0.6 else [])
                elif kind in ("impact", "shot"):
                    fx += ["punch", "flash", "sparks_gfx", "sfx_impact"]
                    if heavy_ok and self.rng.random() < 0.6:
                        fx += ["rgb_split"]
                    if heavy_ok and force > 0.7:
                        fx += ["shake"]
                elif kind == "burst":
                    fx += ["combo", "glitch", "sfx_glitch"]
                elif kind == "flash":
                    fx += ["glow", "punch_soft", "sfx_impact"]
                elif kind == "rush":
                    fx += ["motion_blur", "sfx_whoosh"]
                if {"shake", "glitch", "distortion"} & set(fx):
                    last_heavy = ev["t"]
                label = ev.get("label")
                if label in ("kill", "headshot"):
                    fx += ["notify", "sfx_ding"] + (["headshot_gfx"] if label == "headshot" else [])
                elif label == "victoire":
                    fx += ["victory_gfx"]
                elif label == "fail":
                    fx += ["glitch", "sfx_glitch"]
                elif kind in ("explosion", "impact", "shot", "burst") and force >= 0.45:
                    fx += ["notify"]
                ev["fx"] = fx
                ev["force"] = round(min(1.0, 0.5 + 0.6 * force) * k, 3)
            seg.events = chosen

    # -- 2. moment fort : ralenti -------------------------------------------------
    def choose_ramps(self) -> None:
        st = self.cfg["style"]
        n = int(st["max_ramps_short"] if self.short else st["max_ramps_long"])
        cands = []
        for seg in self.plan.segments:
            for ev in seg.events:
                if ev["kind"] in ("explosion", "impact", "shot") and \
                        ev["t"] - seg.start >= 1.2 and seg.end - ev["t"] >= 1.2:
                    cands.append((ev.get("strength", 0) + (0.5 if ev.get("label") else 0), seg, ev))
        cands.sort(key=lambda c: c[0], reverse=True)
        used = set()
        for _, seg, ev in cands:
            if len(used) >= n or id(seg) in used:
                continue
            a, b = ev["t"] - 0.25, ev["t"] + 0.55
            seg.ramp = [round(a, 4), round(b, 4), 0.35]
            ev["fx"] = [f for f in ev["fx"] if f != "shake"] + ["climax", "sfx_riser"]
            if "explosion_gfx" not in ev["fx"] and "sparks_gfx" not in ev["fx"]:
                ev["fx"].append("sparks_gfx")
            used.add(id(seg))

    # -- 3. musique et calage des coupes ----------------------------------------------
    def plan_music(self) -> None:
        st = self.cfg["style"]
        info = self.music_info
        if info and info.get("bpm"):
            self.plan.music = {"mode": "user", "path": info["path"], "bpm": info["bpm"],
                               "beats": info["beats"]}
        elif st.get("music_generate", True) and self.cfg["music"]["enabled"]:
            self.plan.music = {"mode": "generated", "bpm": self.dna["bpm"]}
        else:
            self.plan.music = {"mode": "none"}
            return
        if not st.get("beat_sync", True) or len(self.plan.segments) < 2:
            return
        beat = 60.0 / float(self.plan.music["bpm"])
        if self.plan.music["mode"] == "user":
            grid = list(self.plan.music["beats"])
        else:
            grid = [i * beat for i in range(int(self.plan.max_seconds / beat) + 64)]
        if not grid:
            return
        durations = {a["path"]: float(a["duration"]) for a in self.analyses}
        for k in range(len(self.plan.segments) - 1):
            seg = self.plan.segments[k]
            cut = self.plan.junctions()[k]
            target = min(grid, key=lambda g: abs(g - cut))
            delta = target - cut
            floor = max([seg.start + 1.5] + [e["t"] + 0.6 for e in seg.events]
                        + ([seg.ramp[1] + 0.5] if seg.ramp else []))
            new_end = seg.end + delta
            if new_end < floor:
                new_end += beat
            if new_end > durations.get(seg.source, new_end) - 0.05:
                new_end -= beat
            if new_end >= floor:
                seg.end = round(new_end, 6)
        while self.plan.duration > self.plan.max_seconds and self.plan.segments:
            last = self.plan.segments[-1]
            floor = max([last.start + 1.5] + [e["t"] + 0.6 for e in last.events])
            if last.end - beat < floor:
                break
            last.end -= beat
        self.plan.segments[-1].events = [e for e in self.plan.segments[-1].events
                                         if e["t"] <= self.plan.segments[-1].end - 0.4]

    # -- 4. caméra, VFX et cadrage par clip ---------------------------------------------
    def build_camera(self) -> None:
        beats = self.montage_beats()
        offsets = self.plan.offsets()
        for i, seg in enumerate(self.plan.segments):
            d = seg.out_duration
            off = offsets[i]
            punches, shakes, flashes, windows = [], [], [], {}
            for ev in seg.events:
                t = round(seg.map_time(ev["t"]), 4)
                f = float(ev["force"])
                if "punch" in ev["fx"]:
                    punches.append((t, f))
                if "punch_soft" in ev["fx"]:
                    punches.append((t, f * 0.4))
                if "shake" in ev["fx"]:
                    shakes.append((t, f))
                if "flash" in ev["fx"] or "climax" in ev["fx"]:
                    flashes.append((t, f))
                for name, length in (("rgb_split", 0.18), ("glitch", 0.22), ("glow", 0.4),
                                     ("distortion", 0.2), ("motion_blur", 0.6)):
                    if name in ev["fx"]:
                        start = t - (0.05 if name != "motion_blur" else 0.3)
                        windows.setdefault(name, []).append((round(max(0.0, start), 4),
                                                             round(min(d, t + length), 4)))
                if "climax" in ev["fx"]:
                    windows.setdefault("glitch", []).append((round(max(0.0, t - 0.32), 4),
                                                             round(max(0.0, t - 0.2), 4)))
            for t, _f in shakes:  # traînée de mouvement pendant les tremblements
                windows.setdefault("motion_blur", []).append((t, round(min(d, t + 0.25), 4)))
            ramp = seg.speed_ramp()
            smooth = None
            if ramp:
                smooth = (round(ramp.start, 4), round(ramp.map(ramp.end), 4))
            clip_beats = [round(b - off, 4) for b in beats if 0.1 < b - off < d - 0.1]
            seg.camera = {
                "push": 0.05 if self.short else 0.03,
                "punches": punches, "shakes": shakes, "smooth_zoom": smooth,
                "beats": clip_beats[:: (1 if self.dna["energy"] > 0.75 else 2)],
                "pan": self.dna["pan"] * (1 if i % 2 == 0 else -1),
                "rotate": any(f > 0.7 for _, f in shakes),
            }
            seg.vfx = {"flash": flashes, **windows,
                       "cinematic_blur": i == 0 or self.rng.random() < 0.25}
            a = self.by_path.get(seg.source, {})
            if self.dna["layout"] == "track" and a.get("focus", {}).get("x"):
                seg.focus = [[round(t, 3), round(x, 3)]
                             for t, x in tracking_x(a["focus"], seg.start, seg.end, seg.map_time)]
            else:
                seg.focus = []

    def montage_beats(self) -> list[float]:
        m = self.plan.music
        if m.get("mode") == "user":
            return [b for b in m.get("beats", []) if b <= self.plan.duration]
        if m.get("mode") == "generated":
            beat = 60.0 / float(m["bpm"])
            return [round(i * beat, 4) for i in range(int(self.plan.duration / beat) + 1)]
        return []

    # -- 5. transitions -----------------------------------------------------------------
    def choose_transitions(self) -> None:
        if self.plan.transition != "xfade" or len(self.plan.segments) < 2:
            self.plan.transitions = []
            return
        family = TRANSITION_FAMILIES[self.dna["transitions"]]
        out, last = [], None
        for k in range(len(self.plan.segments) - 1):
            nxt = self.plan.segments[k + 1]
            strong = any(e["kind"] == "explosion" for e in nxt.events)
            options = [t for t in family if t != last]
            choice = "fadewhite" if strong and "fadewhite" in family and last != "fadewhite" \
                else self.rng.choice(options)
            out.append(choice)
            last = choice
        self.plan.transitions = out

    # -- 6. couche graphique et sound design ------------------------------------------------
    def build_graphics_and_sound(self) -> None:
        plan, dna = self.plan, self.dna
        D = plan.duration
        offsets = plan.offsets()
        g: list[dict] = []
        cues: list[dict] = []
        vertical = plan.height > plan.width
        st = self.cfg["style"]
        if st.get("hud", True):
            g.append({"type": "hud", "start": 0.0, "end": D})
        if self.short:
            g.append({"type": "progress", "start": 0.0, "end": D})
            hook = self.texts.get("hook") or self.rng.choice(HOOKS)
            plan.hook = str(hook)[:48]
            g.append({"type": "hook", "start": 0.1, "end": min(2.8, D * 0.3), "text": plan.hook})
            for w in range(len(plan.hook.split())):
                cues.append({"t": 0.1 + 0.09 * w, "kind": "swoosh", "gain": 0.5})
        else:
            title = self.texts.get("title") or "Meilleurs moments"
            g.append({"type": "title", "start": 0.3, "end": 3.3, "text": str(title)[:40],
                      "subtitle": st.get("channel_name") or ""})
            cues.append({"t": 0.3, "kind": "whoosh", "gain": 0.7})
        if st.get("channel_name"):
            g.append({"type": "lower_third", "start": 3.5 if not self.short else 3.0,
                      "end": 7.5 if not self.short else 6.0, "title": st["channel_name"],
                      "subtitle": self.texts.get("game", "")})
        if dna.get("embers"):
            g.append({"type": "embers", "start": 0.0, "end": D, "seed": dna["seed"] % 97})
        if not self.short:
            for i, off in enumerate(offsets):
                seg = plan.segments[i]
                g.append({"type": "scoreboard", "start": off + 0.4, "end": off + 3.4,
                          "index": i + 1, "total": len(offsets),
                          "score": round(min(1.0, 0.35 + seg.score / 3), 3)})
        # transitions : whoosh calé sur la jonction
        for jt in plan.junctions():
            cues.append({"t": jt, "kind": "whoosh", "gain": 0.8, "pan": self.rng.uniform(-0.5, 0.5)})
        last_word, slots = -10.0, []
        captions = list(self.texts.get("captions") or [])
        for i, seg in enumerate(plan.segments):
            a = self.by_path.get(seg.source, {})
            focus = a.get("focus", {})
            for ev in seg.events:
                t = offsets[i] + seg.map_time(ev["t"])
                fx = ev["fx"]
                x = 0.5
                if not vertical and focus.get("x"):
                    k = min(len(focus["x"]) - 1, int(ev["t"] / float(focus.get("step", 0.5))))
                    x = float(focus["x"][k])
                y = 0.45 if vertical else 0.5
                s = dna["seed"] + int(t * 100)
                if "explosion_gfx" in fx:
                    g.append({"type": "explosion", "start": t, "x": x, "y": y, "seed": s})
                if "sparks_gfx" in fx:
                    g.append({"type": "sparks", "start": t, "x": x, "y": y, "seed": s,
                              "count": 45 if self.short else 25, "life": 0.6})
                    g.append({"type": "shockwave", "start": t, "x": x, "y": y, "life": 0.4,
                              "radius": 0.35})
                if "combo" in fx and ev.get("count"):
                    hits = [round(t + (h - ev["t"]), 3) for h in
                            self._burst_hits(a, ev)]
                    g.append({"type": "combo", "start": t, "end": hits[-1] + 1.4, "hits": hits})
                if "headshot_gfx" in fx:
                    g.append({"type": "headshot", "start": t, "end": t + 1.4})
                if "victory_gfx" in fx:
                    g.append({"type": "victory", "start": t, "end": t + 2.2})
                if "notify" in fx:
                    kind = ev.get("label") or ev["kind"]
                    text, ic = NOTIFY.get(kind, NOTIFY["impact"])
                    slots = [x for x in slots if x > t]
                    slot = len(slots) % 3
                    slots.append(t + 2.0)
                    g.append({"type": "notify", "start": t, "end": t + 2.0, "text": text,
                              "icon": ic, "slot": slot})
                word_gap = 1.6 if self.short else 3.0
                big = "climax" in fx or ev.get("label") in SEMANTIC or ev.get("strength", 0) > 0.6
                if t - last_word >= word_gap and (self.short or big) \
                        and "headshot_gfx" not in fx and "victory_gfx" not in fx:
                    text = captions.pop(0) if captions else self._word(ev.get("label") or ev["kind"])
                    style = "slam" if "climax" in fx or ev["kind"] == "explosion" else \
                        self.rng.choice(["pop", "pop", "slide"])
                    g.append({"type": "word", "start": t, "end": t + (1.4 if "climax" in fx else 1.0),
                              "text": str(text)[:22], "style": style,
                              "pos": [0.5, 0.64 if vertical else 0.7],
                              "angle": dna["word_angle"] * (1 if len(g) % 2 else -1),
                              "size": 150 if "climax" in fx else 120})
                    last_word = t
                # sound design
                if "sfx_boom" in fx:
                    cues.append({"t": t, "kind": "boom", "gain": 0.9})
                if "sfx_impact" in fx:
                    cues.append({"t": t, "kind": "impact", "gain": 0.75})
                if "sfx_glitch" in fx:
                    cues.append({"t": t, "kind": "glitch", "gain": 0.6})
                if "sfx_whoosh" in fx:
                    cues.append({"t": t, "kind": "whoosh", "gain": 0.6})
                if "sfx_ding" in fx or "notify" in fx:
                    cues.append({"t": t + 0.05, "kind": "ding", "gain": 0.5})
                if "climax" in fx:
                    cues.append({"t": t, "kind": "riser", "gain": 0.8})
                    cues.append({"t": t, "kind": "sub_drop", "gain": 0.9})
        if self.short and st.get("subscribe_outro", True) and D > 8:
            g.append({"type": "subscribe", "start": D - 2.6, "end": D - 0.1})
            cues.append({"t": D - 2.6 + 0.85, "kind": "click", "gain": 0.8})
        plan.graphics = sorted(g, key=lambda x: x["start"])
        plan.sfx = sorted(cues, key=lambda x: x["t"])

    @staticmethod
    def _burst_hits(analysis: dict, burst: dict) -> list[float]:
        hits = [e["t"] for e in analysis.get("events", [])
                if e["kind"] != "burst" and burst["t"] - 0.01 <= e["t"] <= burst.get("end", burst["t"]) + 0.01]
        return hits or [burst["t"]]

    # -- 7. musique : drop au moment fort -------------------------------------------------
    def finalize_music(self) -> None:
        m = self.plan.music
        if m.get("mode") != "generated":
            return
        offsets = self.plan.offsets()
        for i, seg in enumerate(self.plan.segments):
            ramp = seg.speed_ramp()
            if ramp:
                a = offsets[i] + ramp.start
                b = offsets[i] + ramp.map(ramp.end)
                beat = 60.0 / m["bpm"]
                drop = round(math.ceil(b / beat - 1e-6) * beat, 4)  # premier temps après le ralenti
                m["calm"] = [round(a, 4), round(drop, 4)]
                m["drop_at"] = drop
                break

    def run(self) -> None:
        plan = self.plan
        plan.style = self.dna
        if not self.cfg["style"]["enabled"] or not plan.segments:
            return
        self.choose_events()
        self.choose_ramps()
        self.plan_music()
        self.choose_transitions()
        self.build_camera()
        self.finalize_music()
        self.build_graphics_and_sound()
        counts: dict[str, int] = {}
        for seg in plan.segments:
            for ev in seg.events:
                for f in ev["fx"]:
                    counts[f] = counts.get(f, 0) + 1
        for item in plan.graphics:
            counts["gfx:" + item["type"]] = counts.get("gfx:" + item["type"], 0) + 1
        plan.effects = counts


def direct(plan, analyses: list[dict], cfg, texts: dict | None = None, key: str = "",
           music_info: dict | None = None):
    """Point d'entrée : enrichit le plan en place et le renvoie."""
    Director(plan, analyses, cfg, texts, key, music_info).run()
    return plan
