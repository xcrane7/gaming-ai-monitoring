"""3. Dynamic Motion Design Engine — animation de la caméra virtuelle.

Toutes les animations sont décrites par des courbes temporelles continues
(easing), puis traduites en expressions FFmpeg évaluées image par image :

- dynamic zoom : poussée lente et continue sur tout le clip (ease-in-out) ;
- punch zoom   : coup de zoom sec sur un impact (attaque rapide, retour amorti) ;
- smooth zoom  : zoom avant progressif pendant un ralenti ;
- beat pulse   : micro-zoom sur les temps forts de la musique ;
- camera shake : tremblement amorti, translation + légère rotation ;
- pan / tilt   : dérive lente de cadrage (direction choisie par le style) ;
- tracking     : le cadrage vertical suit la position de l'action ;
- speed ramp   : ralenti progressif (la vitesse descend puis remonte en douceur)
                 avec fusion d'images pour éviter les saccades ;
- motion blur  : traînée de mouvement sur les mouvements de caméra brusques.

Les mêmes fonctions d'easing existent en Python (pour le moteur graphique)
et en expression FFmpeg (pour le rendu vidéo), afin que tout bouge ensemble.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Easing (Python)
# ---------------------------------------------------------------------------


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def ease_out_cubic(x: float) -> float:
    x = clamp01(x)
    return 1 - (1 - x) ** 3


def ease_in_cubic(x: float) -> float:
    x = clamp01(x)
    return x ** 3


def ease_in_out_cubic(x: float) -> float:
    x = clamp01(x)
    return 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2


def ease_out_back(x: float, overshoot: float = 1.70158) -> float:
    x = clamp01(x)
    c3 = overshoot + 1
    return 1 + c3 * (x - 1) ** 3 + overshoot * (x - 1) ** 2


def ease_out_elastic(x: float) -> float:
    x = clamp01(x)
    if x in (0.0, 1.0):
        return x
    return 2 ** (-10 * x) * math.sin((x * 10 - 0.75) * (2 * math.pi / 3)) + 1


def smoothstep(x: float) -> float:
    x = clamp01(x)
    return x * x * (3 - 2 * x)


EASINGS = {
    "linear": clamp01, "out_cubic": ease_out_cubic, "in_cubic": ease_in_cubic,
    "in_out_cubic": ease_in_out_cubic, "out_back": ease_out_back,
    "out_elastic": ease_out_elastic, "smooth": smoothstep,
}


def tween(t: float, start: float, duration: float, a: float, b: float,
          easing: str = "out_cubic") -> float:
    """Valeur interpolée de a vers b entre `start` et `start + duration`."""
    if duration <= 0:
        return b if t >= start else a
    return a + (b - a) * EASINGS[easing]((t - start) / duration)


def punch_envelope(dt: float, attack: float = 0.07, decay: float = 5.0) -> float:
    """Enveloppe d'un coup (0 avant, monte en `attack`, redescend en douceur)."""
    if dt < 0:
        return 0.0
    return (1 - math.exp(-dt / max(attack / 3, 1e-3))) * math.exp(-decay * dt)


# ---------------------------------------------------------------------------
# Expressions FFmpeg
# ---------------------------------------------------------------------------


def _f(x: float) -> str:
    """Nombre formaté pour une expression FFmpeg (4 décimales, sans zéros inutiles)."""
    text = f"{float(x):.4f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def expr_smoothstep(var: str, start: float, duration: float) -> str:
    """smoothstep((var-start)/duration) en expression FFmpeg."""
    x = f"clip(({var}-{_f(start)})/{_f(max(duration, 1e-3))},0,1)"
    return f"({x}*{x}*(3-2*{x}))"


def expr_punch(times: list[float], amount: float, attack: float = 0.07,
               decay: float = 5.0, var: str = "t") -> str:
    """Somme de coups de zoom (forme analytique, continue et sans à-coup)."""
    if not times or amount == 0:
        return "0"
    k = 3.0 / max(attack, 1e-3)
    # max(..., 0) : évite tout dépassement numérique (exp d'un grand nombre) avant l'impact
    terms = []
    for t in times:
        dt = f"max({var}-{_f(t)},0)"
        terms.append(f"gte({var},{_f(t)})*(1-exp(-{_f(k)}*{dt}))*exp(-{_f(decay)}*{dt})")
    return f"{_f(amount)}*(" + "+".join(terms) + ")"


def expr_decay(times: list[float], decay: float, var: str = "t") -> str:
    if not times:
        return "0"
    return "+".join(f"gte({var},{_f(t)})*exp(-{_f(decay)}*max({var}-{_f(t)},0))" for t in times)


def expr_piecewise(points: list[tuple[float, float]], var: str = "t",
                   smooth: bool = True) -> str:
    """Courbe passant par des points clés ; interpolation lissée (smoothstep) ou linéaire."""
    if not points:
        return "0"
    if len(points) == 1:
        return _f(points[0][1])
    expr = _f(points[-1][1])
    for (t0, v0), (t1, v1) in reversed(list(zip(points, points[1:]))):
        if smooth:
            seg = f"({_f(v0)}+({_f(v1 - v0)})*{expr_smoothstep(var, t0, t1 - t0)})"
        else:
            seg = f"({_f(v0)}+({_f(v1 - v0)})*({var}-{_f(t0)})/{_f(max(t1 - t0, 1e-4))})"
        expr = f"if(lt({var},{_f(t1)}),{seg},{expr})"
    return f"if(lt({var},{_f(points[0][0])}),{_f(points[0][1])},{expr})"


# ---------------------------------------------------------------------------
# Speed ramp
# ---------------------------------------------------------------------------


@dataclass
class SpeedRamp:
    """Ralenti progressif entre `start` et `end` (secondes, relatives au clip source)."""

    start: float
    end: float
    min_speed: float = 0.35

    @property
    def ease(self) -> float:
        return min(0.25, (self.end - self.start) / 3)

    def speed(self, r: float) -> float:
        if r <= self.start or r >= self.end:
            return 1.0
        e = self.ease
        if r < self.start + e:
            k = smoothstep((r - self.start) / e)
        elif r > self.end - e:
            k = smoothstep((self.end - r) / e)
        else:
            k = 1.0
        return 1.0 - (1.0 - self.min_speed) * k

    def knots(self, step: float = 1 / 60) -> list[tuple[float, float]]:
        """Correspondance temps source -> temps monté, échantillonnée sur la rampe."""
        pts = [(self.start, self.start)]
        r, o = self.start, self.start
        n = max(2, int((self.end - self.start) / step))
        h = (self.end - self.start) / n
        for _ in range(n):
            # intégration de 1/vitesse (méthode du point milieu)
            o += h / self.speed(r + h / 2)
            r += h
            pts.append((r, o))
        return pts

    @property
    def extra(self) -> float:
        """Durée ajoutée au clip par le ralenti."""
        k = self.knots()
        return k[-1][1] - k[-1][0]

    def map(self, r: float) -> float:
        if r <= self.start:
            return r
        k = self.knots()
        if r >= self.end:
            return r + (k[-1][1] - k[-1][0])
        for (r0, o0), (r1, o1) in zip(k, k[1:]):
            if r <= r1:
                return o0 + (o1 - o0) * (r - r0) / max(r1 - r0, 1e-9)
        return r

    def setpts_expr(self) -> str:
        """Expression `setpts` : nouveau temps de présentation en fonction du temps source T."""
        k = self.knots(step=1 / 30)
        body = expr_piecewise(k, var="T", smooth=False)
        after = f"(T+{_f(k[-1][1] - k[-1][0])})"
        return f"(if(lt(T,{_f(self.start)}),T,if(gte(T,{_f(self.end)}),{after},{body})))/TB"

    def audio_rate(self) -> float:
        """Facteur de lecture du son pendant le ralenti (son grave, façon « bullet time »)."""
        k = self.knots()
        return (self.end - self.start) / max(k[-1][1] - self.start, 1e-6)


# ---------------------------------------------------------------------------
# Caméra
# ---------------------------------------------------------------------------


@dataclass
class CameraMove:
    """Description complète des mouvements de caméra d'un clip (temps du clip monté)."""

    duration: float
    push: float = 0.04  # zoom lent sur la durée du clip
    punches: list | None = None  # [(t, force)]
    shakes: list | None = None  # [(t, force)]
    beats: list | None = None  # temps forts musicaux
    smooth_zoom: tuple | None = None  # (début, fin) zoom avant pendant le ralenti
    pan: float = 0.0  # dérive horizontale totale, en fraction de marge (-1..1)
    tilt: float = 0.0
    rotate: bool = False  # rotation pendant les tremblements forts


def camera_filters(cam: CameraMove, width: int, height: int, intensity: float = 1.0,
                   preset: dict | None = None) -> str:
    """Chaîne `scale,crop[,rotate]` réalisant zooms, tremblements et dérives."""
    preset = preset or {}
    punch_k = float(preset.get("punch", 1.0))
    shake_k = float(preset.get("shake", 1.0))
    d = max(cam.duration, 0.1)
    zoom = [f"1+{_f(cam.push * intensity)}*{expr_smoothstep('t', 0, d)}"]
    groups: dict[float, list[float]] = {}
    for t, force in cam.punches or []:
        groups.setdefault(round(force, 2), []).append(t)
    for force, times in groups.items():
        zoom.append(expr_punch(times, 0.11 * force * intensity * punch_k))
    if cam.beats:
        zoom.append(expr_punch(cam.beats, 0.012 * intensity, attack=0.03, decay=9))
    if cam.smooth_zoom:
        a, b = cam.smooth_zoom
        zoom.append(f"0.08*{expr_smoothstep('t', a, max(b - a, 0.1))}"
                    f"*(1-{expr_smoothstep('t', b, 0.35)})")
    z = "+".join(zoom)
    parts = [f"scale=w='2*trunc({width}*({z})/2)':h='2*trunc({height}*({z})/2)':"
             "eval=frame:flags=bicubic"]
    sx = sy = rot = ""
    shakes = cam.shakes or []
    if shakes:
        sx_terms, sy_terms, r_terms = [], [], []
        for t, force in shakes:
            env = f"gte(t,{_f(t)})*exp(-7*max(t-{_f(t)},0))"
            amp = 0.018 * width * force * intensity * shake_k
            sx_terms.append(f"{_f(amp)}*{env}*(sin(43*t+{_f(t)})+0.5*sin(71*t))")
            sy_terms.append(f"{_f(amp * 0.7)}*{env}*(cos(51*t+{_f(t * 3)})+0.4*sin(89*t))")
            r_terms.append(f"{_f(0.012 * force * intensity * shake_k)}*{env}*sin(37*t)")
        sx, sy = "+" + "+".join(sx_terms), "+" + "+".join(sy_terms)
        rot = "+".join(r_terms)
    pan = (f"+{_f(cam.pan)}*(iw-{width})/2*({expr_smoothstep('t', 0, d)}*2-1)"
           if cam.pan else "")
    tilt = (f"+{_f(cam.tilt)}*(ih-{height})/2*({expr_smoothstep('t', 0, d)}*2-1)"
            if cam.tilt else "")
    parts.append(f"crop={width}:{height}:x='clip((iw-{width})/2{pan}{sx},0,iw-{width})':"
                 f"y='clip((ih-{height})/2{tilt}{sy},0,ih-{height})'")
    if cam.rotate and rot:
        parts.append(f"rotate='{rot}':fillcolor=black@1:bilinear=1")
    return ",".join(parts)


def motion_blur(windows: list[tuple[float, float]]) -> str:
    """Traînée de mouvement (mélange d'images) limitée aux instants de mouvement brusque."""
    if not windows:
        return ""
    enable = "+".join(f"between(t,{_f(a)},{_f(b)})" for a, b in windows)
    return f"tmix=frames=3:weights='1 2 1':enable='{enable}'"


def tracking_x(focus: dict, seg_start: float, seg_end: float, mapper=None) -> list[tuple]:
    """Points clés (temps du clip, position 0..1) de l'action pour le recadrage."""
    xs = focus.get("x") or []
    step = float(focus.get("step", 0.5))
    if not xs:
        return []
    pts = []
    t = seg_start
    while t <= seg_end + 1e-6:
        k = min(len(xs) - 1, max(0, int(round(t / step))))
        rel = t - seg_start
        pts.append((mapper(t) if mapper else rel, float(xs[k])))
        t += step
    return pts
