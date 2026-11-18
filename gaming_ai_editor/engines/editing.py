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
    "clutch": ["CLUTCH", "SAUVÉ", "IMPOSSIBLE"],
    "reaction": ["LA RÉACTION", "IL CRIE", "QUOI ?!"],
    "surprise": ["SURPRISE", "D'UN COUP", "PERSONNE S'Y ATTENDAIT"],
}
NOTIFY = {
    "explosion": ("Explosion", "bolt"), "impact": ("Impact", "crosshair"),
    "shot": ("Tir précis", "crosshair"), "burst": ("Rafale", "flame"),
    "kill": ("Élimination", "skull"), "headshot": ("Headshot", "crosshair"),
    "rush": ("Rush", "bolt"), "clutch": ("Clutch", "flame"),
}
HOOKS = ["Attends la fin", "Regarde bien ça", "Personne ne s'attendait à ça",
         "Ça part trop loin", "Le meilleur moment de la partie"]
# Accroches locales (sans IA) selon la nature du meilleur moment : une promesse ou une question
HOOK_TEMPLATES = {
    "kill": ["Il ne l'a pas vu venir", "Un contre tous…", "Regarde ce qu'il fait"],
    "headshot": ["Une balle, pas deux", "Le tir parfait ?", "Il vise où, là ?"],
    "clutch": ["Il reste 1 PV…", "Personne n'y croyait", "Impossible de s'en sortir ?"],
    "fail": ["Il aurait dû rester caché", "Ça ne pouvait pas mal tourner…", "Mauvaise idée"],
    "drôle": ["Je ne m'y attendais pas", "Attends la fin", "Qu'est-ce qui vient de se passer ?"],
    "victoire": ["Tout se joue ici", "La dernière manche…", "Il ne reste que lui"],
    "série": ["Il enchaîne tout le monde", "Impossible de l'arrêter", "Et ça continue…"],
    "surprise": ["Personne ne l'a vu venir", "D'un coup…", "Écoute bien"],
    "réaction": ["Sa réaction…", "Il ne s'y attendait pas", "Écoute bien"],
}
EVENT_PRIORITY = {"victory": 0, "headshot": 1, "notify": 2, "combo": 3, "word": 4}
SEMANTIC = {"kill", "headshot", "clutch", "victoire", "fail", "drôle"}


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
    """Étiquette fiable d'un moment proche : IA (Gemini, Claude) ou texte lu à l'écran (OCR)."""
    for h in analysis.get("highlights", []):
        if h.get("source") in ("gemini", "claude", "ocr") and h["start"] - 1.0 <= t <= h["end"] + 1.0:
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
            peak_label = _semantic_label(a, seg.peak) if seg.peak else None
            if not chosen and seg.peak and seg.start + 0.5 < seg.peak < seg.end - 0.5:
                chosen.append({"t": seg.peak, "kind": "impact", "strength": 0.4,
                               "label": peak_label})
            elif peak_label and chosen and not any(c.get("label") for c in chosen):
                # l'IA ou l'OCR a reconnu le moment (ex. une chute silencieuse = fail) mais aucun
                # son ne l'a marqué : l'étiquette va à l'événement le plus proche du pic
                min(chosen, key=lambda c: abs(c["t"] - seg.peak))["label"] = peak_label
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
                    # le jeu flashe déjà : un halo en plus surexpose l'image
                    fx += ["punch_soft", "sfx_impact"]
                elif kind == "rush":
                    fx += ["motion_blur", "sfx_whoosh"]
                elif kind == "surprise":  # silence puis pic : on sursaute avec le joueur
                    fx += ["punch", "flash", "sfx_impact"] + (["shake"] if heavy_ok else [])
                elif kind == "reaction":  # le joueur réagit : on souligne sans écraser sa voix
                    fx += ["punch_soft"]
                if {"shake", "glitch", "distortion"} & set(fx):
                    last_heavy = ev["t"]
                label = ev.get("label")
                if label in ("kill", "headshot", "clutch"):
                    fx += ["notify", "sfx_ding"] + (["headshot_gfx"] if label == "headshot" else [])
                elif label == "victoire":
                    fx += ["victory_gfx"]
                elif label == "fail":
                    fx += ["glitch", "sfx_glitch"]
                # pas de notification générique (« Explosion », « Impact ») : elle doublonne
                # le mot d'impact ; la carte façon kill feed est réservée aux vraies étiquettes
                ev["fx"] = fx
                ev["force"] = round(min(1.0, 0.5 + 0.6 * force) * k, 3)
            seg.events = chosen

    # -- 2. moment fort : ralenti -------------------------------------------------
    def ceiling(self, seg) -> float:
        """Fin maximale d'un clip : fin de la source, et jamais dans un menu / écran fixe."""
        a = self.by_path.get(seg.source, {})
        limit = float(a.get("duration", seg.end))
        for u0, u1 in a.get("ui_windows") or []:
            if u0 > seg.peak:
                limit = min(limit, u0)
        return max(limit, seg.end) if limit < seg.end else limit

    def drop_dead_clips(self) -> None:
        """Retire les clips de montée courts où rien ne se passe (aucun événement)."""
        plan = self.plan
        for seg in list(plan.segments):
            if seg.role == "build" and not seg.events and seg.duration < 3.0:
                if plan.duration - seg.out_duration >= plan.min_seconds:
                    plan.segments.remove(seg)
                    plan.notes.append(f"Clip sans action retiré ({seg.start:.1f}-{seg.end:.1f} s).")

    def vary_durations(self) -> None:
        """Jamais deux clips de durée proche (±20 %) à la suite : on raccourcit ou allonge
        l'un des deux d'un nombre entier de temps, ce qui garde les coupes sur le tempo.
        Le cold open et le climax ne sont jamais modifiés."""
        m = self.plan.music
        beat = 60.0 / float(m["bpm"]) if m.get("bpm") else 0.5
        segs = self.plan.segments

        def distinct(x: float, y: float) -> bool:
            return abs(x - y) / max(x, y, 1e-6) >= 0.2

        def try_adjust(i: int) -> bool:
            seg = segs[i]
            if seg.role in ("teaser", "climax") or i in (0, len(segs) - 1):
                return False
            floor = max([seg.start + 1.5] + [e["t"] + 0.6 for e in seg.events]
                        + ([seg.ramp[1] + 0.5] if seg.ramp else []))
            ceil = self.ceiling(seg) - 0.05
            d = seg.out_duration
            # raccourcir d'abord (plus nerveux), puis allonger ; jusqu'à 8 temps
            for steps in [-n for n in range(1, 9)] + list(range(1, 9)):
                new_end, new_d = seg.end + steps * beat, d + steps * beat
                if not floor <= new_end <= ceil:
                    continue
                if distinct(new_d, segs[i - 1].out_duration) and \
                        distinct(new_d, segs[i + 1].out_duration):
                    seg.end = round(new_end, 6)
                    return True
            # sinon : raccourcir l'entrée en matière (le calme avant l'action), toujours
            # d'un nombre entier de temps pour que les coupes suivantes restent sur le tempo
            first = min([e["t"] for e in seg.events] + [seg.peak])
            latest = min(first - 0.8, seg.ramp[0] - 0.5 if seg.ramp else first)
            for n in range(1, 9):
                new_start, new_d = seg.start + n * beat, d - n * beat
                if new_start > latest or seg.end - new_start < 1.5:
                    break
                if distinct(new_d, segs[i - 1].out_duration) and \
                        distinct(new_d, segs[i + 1].out_duration):
                    seg.start = round(new_start, 6)
                    return True
            return False

        for k in range(1, len(segs)):
            if not distinct(segs[k - 1].out_duration, segs[k].out_duration):
                try_adjust(k) or try_adjust(k - 1)
        while self.plan.duration > self.plan.max_seconds and len(segs) > 2:
            longest = max(segs[1:-1], key=lambda s: s.duration)
            if longest.duration - beat < 2.0:
                break
            longest.end -= beat

    def choose_ramps(self) -> None:
        st = self.cfg["style"]
        n = int(st["max_ramps_short"] if self.short else st["max_ramps_long"])
        cands = []
        segments = self.plan.segments
        if any(s.role == "climax" for s in segments):  # structure narrative : ralenti au climax
            segments = [s for s in segments if s.role == "climax"]
        for seg in segments:
            for ev in seg.events:
                if ev["kind"] in ("explosion", "impact", "shot", "surprise") and \
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
            self.plan.music = {"mode": "generated", "bpm": self.dna["bpm"],
                               "loop": bool(self.plan.loop)}
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
            if new_end > min(durations.get(seg.source, new_end), self.ceiling(seg)) - 0.05:
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
        """Coupes sèches pendant la montée, transitions visibles entre les séquences.

        Visible : sortie du cold open, entrée dans le climax, et au plus une jonction sur
        trois pendant la montée. Jamais deux fois la même transition de suite.
        """
        n = len(self.plan.segments)
        if self.plan.transition != "xfade" or n < 2:
            self.plan.transitions, self.plan.transition_durations = [], []
            return
        family = TRANSITION_FAMILIES[self.dna["transitions"]]
        visible_len = float(self.plan.transition_seconds) or 0.3
        names, durations, last, since_visible = [], [], None, 0
        for k in range(n - 1):
            cur, nxt = self.plan.segments[k], self.plan.segments[k + 1]
            strong = any(e["kind"] == "explosion" for e in nxt.events)
            if cur.role == "teaser":
                visible, choice = True, "fadewhite"
            elif nxt.role == "climax":
                visible = True
                choice = self.rng.choice([t for t in ("zoomin", "fadewhite", "hblur")
                                          if t != last])
            elif not self.short or not cur.role:
                visible = True
                choice = None
            else:  # montée : coupe sèche, sauf de temps en temps
                visible = since_visible >= 2 and self.rng.random() < 0.5
                choice = None
            if visible:
                if choice is None:
                    options = [t for t in family if t != last]
                    choice = "fadewhite" if strong and "fadewhite" in options else \
                        self.rng.choice(options)
                names.append(choice)
                durations.append(round(visible_len * (1.4 if nxt.role == "climax" else 1.0), 3))
                since_visible = 0
            else:  # « coupe » : fondu de 2 images, imperceptible, garde la chaîne xfade
                names.append("fade")
                durations.append(round(2.0 / self.plan.fps, 4))
                since_visible += 1
            last = names[-1]
        self.plan.transitions, self.plan.transition_durations = names, durations

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
            plan.hook = self.choose_hook()
            teaser = plan.segments[0].out_duration if plan.segments[0].role == "teaser" else 0.0
            hook_end = min(max(2.8, teaser + 0.6), D * 0.3)
            if plan.hook:
                g.append({"type": "hook", "start": 0.1, "end": hook_end, "text": plan.hook})
                for w in range(len(plan.hook.split())):
                    cues.append({"t": 0.1 + 0.09 * w, "kind": "swoosh", "gain": 0.5})
                if plan.loop and D > hook_end + 3:  # boucle : l'accroche revient à la fin
                    g.append({"type": "hook", "start": round(D - 0.9, 3), "end": D,
                              "text": plan.hook, "reprise": True})
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
        # transitions visibles : whoosh calé sur la jonction ; coupes sèches : rien
        for jt, lap in zip(plan.junctions(), plan.overlaps()):
            if lap >= 0.15:
                cues.append({"t": jt, "kind": "whoosh", "gain": 0.8,
                             "pan": self.rng.uniform(-0.5, 0.5)})
        last_word, slots, worded = -10.0, [], set()
        # pendant l'accroche, pas de mot d'impact : deux gros textes à la fois se lisent mal
        hook_until = max([x["end"] for x in g if x["type"] == "hook" and not x.get("reprise")],
                         default=0.0)
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
                if t - last_word >= word_gap and (self.short or big) and i not in worded \
                        and not (hook_until and t < hook_until) \
                        and "headshot_gfx" not in fx and "victory_gfx" not in fx:
                    worded.add(i)  # une légende d'événement par clip au maximum
                    text = captions.pop(0) if captions else self._word(ev.get("label") or ev["kind"])
                    style = "slam" if "climax" in fx or ev["kind"] == "explosion" else \
                        self.rng.choice(["pop", "pop", "slide"])
                    g.append({"type": "word", "start": t, "end": t + (1.4 if "climax" in fx else 1.0),
                              "text": str(text)[:22], "style": style,
                              "pos": [0.47, 0.56] if vertical else [0.5, 0.7],
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
        if self.short and st.get("subscribe_outro", True) and D > 8 and not plan.loop:
            g.append({"type": "subscribe", "start": D - 2.6, "end": D - 0.1})
            cues.append({"t": D - 2.6 + 0.85, "kind": "click", "gain": 0.8})
        plan.graphics = self.one_event_text_at_a_time(sorted(g, key=lambda x: x["start"]))
        plan.sfx = sorted(cues, key=lambda x: x["t"])

    def choose_hook(self) -> str:
        """Accroche : texte imposé, désactivée, Claude, ou modèle local selon le meilleur moment."""
        setting = self.cfg["short"].get("hook_text", "auto")
        if setting == "off":
            return ""
        if setting not in ("auto", "", None):
            return str(setting)[:48]
        if self.texts.get("hook"):
            return str(self.texts["hook"])[:48]
        best = max(self.plan.segments, key=lambda sg: sg.score)
        labels = [e.get("label") for e in best.events if e.get("label")] + [best.label or ""]
        for label in labels:
            key = "série" if label.startswith("série") else label
            if key in HOOK_TEMPLATES:
                return self.rng.choice(HOOK_TEMPLATES[key])
        return self.rng.choice(HOOKS)

    @staticmethod
    def one_event_text_at_a_time(items: list[dict]) -> list[dict]:
        """Jamais deux textes d'événement à l'écran en même temps : le plus important reste
        (victoire > headshot > notification > combo > mot d'impact)."""
        kept: list[dict] = []
        events = sorted((x for x in items if x["type"] in EVENT_PRIORITY),
                        key=lambda x: (EVENT_PRIORITY[x["type"]], x["start"]))
        for item in events:
            if all(item["end"] <= k["start"] or item["start"] >= k["end"] for k in kept):
                kept.append(item)
        others = [x for x in items if x["type"] not in EVENT_PRIORITY]
        return sorted(others + kept, key=lambda x: x["start"])

    def end_on_beat(self) -> None:
        """Fin sèche sur un temps fort : la durée totale tombe sur un temps de la musique."""
        m = self.plan.music
        if not self.plan.loop or not m.get("bpm") or not self.plan.segments:
            return
        beat = 60.0 / float(m["bpm"])
        last = self.plan.segments[-1]
        floor = max([last.start + 1.5] + [e["t"] + 0.6 for e in last.events]
                    + ([last.ramp[1] + 0.5] if last.ramp else []))
        excess = self.plan.duration % beat
        if excess > 1e-3 and last.end - excess >= floor:
            last.end = round(last.end - excess, 6)

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
        self.drop_dead_clips()
        self.choose_ramps()
        self.choose_transitions()  # avant la musique : les coupes se calent sur le tempo
        self.plan_music()
        if self.short:
            self.vary_durations()
            self.end_on_beat()
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
