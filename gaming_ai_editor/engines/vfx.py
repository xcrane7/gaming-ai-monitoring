"""4. Gaming VFX Engine — effets image appliqués sur la vidéo du jeu.

Chaque effet est une fonction qui renvoie un fragment de chaîne de filtres
FFmpeg, actif uniquement pendant ses fenêtres de temps (option `enable`), donc
sans coût de calcul le reste du temps :

    impact_flash      éclair blanc qui décroît (luminosité)
    rgb_split         séparation des canaux rouge / bleu
    chromatic         aberration chromatique légère et permanente (look objectif)
    glitch            décalage de canaux + bruit + saut de teinte, très bref
    glow              halo lumineux (copie floutée fusionnée en mode « écran »)
    distortion        déformation d'objectif (onde de choc)
    vignette          assombrissement des bords, qui se resserre sur les impacts
    cinematic_blur    mise au point : le clip démarre flou puis devient net

Les particules, ondes de choc et explosions dessinées sont dans graphics.py
(couche d'incrustation), car elles se superposent à l'image au lieu de la modifier.
"""
from __future__ import annotations

from .motion import _f, expr_decay

EFFECTS = ("impact_flash", "rgb_split", "chromatic", "glitch", "glow", "distortion",
           "vignette", "cinematic_blur")


def _enable(windows: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{_f(a)},{_f(b)})" for a, b in windows) or "0"


def impact_flash(times: list[tuple[float, float]], intensity: float = 1.0) -> str:
    """times = [(instant, force)]."""
    if not times:
        return ""
    terms = [f"{_f(0.28 * f * intensity)}*({expr_decay([t], 10)})" for t, f in times]
    return f"eq=brightness='{'+'.join(terms)}':eval=frame"


def rgb_split(windows: list[tuple[float, float]], amount: int = 12) -> str:
    if not windows:
        return ""
    a = max(2, int(amount))
    return f"rgbashift=rh=-{a}:bh={a}:rv={a // 3}:edge=smear:enable='{_enable(windows)}'"


def chromatic(amount: int = 3) -> str:
    return f"rgbashift=rh=-{amount}:bh={amount}:edge=smear"


def glitch(windows: list[tuple[float, float]], width: int) -> str:
    if not windows:
        return ""
    e = _enable(windows)
    shift = max(6, width // 60)
    return (f"rgbashift=rh={shift}:gh=-{shift // 2}:bv={shift // 2}:edge=wrap:enable='{e}',"
            f"noise=alls=45:allf=t:enable='{e}',"
            f"hue=h=90:s=1.6:enable='{e}'")


def glow(windows: list[tuple[float, float]], width: int, label: str) -> tuple[str, str]:
    """Retourne (graphe, étiquette de sortie). Nécessite une entrée étiquetée `label`."""
    if not windows:
        return "", label
    e = _enable(windows)
    sigma = max(8, width // 45)
    out = f"{label}g"
    graph = (f"[{label}]split=2[{label}ga][{label}gb];"
             f"[{label}gb]gblur=sigma={sigma}:enable='{e}',"
             f"eq=brightness=0.06:saturation=1.4:enable='{e}'[{label}gc];"
             f"[{label}ga][{label}gc]blend=all_mode=screen:all_opacity=0.38:enable='{e}'[{out}]")
    return graph, out


def distortion(windows: list[tuple[float, float]], strength: float = 1.0) -> str:
    if not windows:
        return ""
    k = -0.22 * strength
    return f"lenscorrection=k1={_f(k)}:k2={_f(-k / 3)}:i=bilinear:enable='{_enable(windows)}'"


def vignette(impacts: list[float], base: float = 0.55) -> str:
    """Angle de vignette : plus fermé brièvement sur les impacts."""
    pulse = f"+0.25*({expr_decay(impacts, 6)})" if impacts else ""
    return f"vignette=angle='{_f(base)}{pulse}':eval=frame"


def cinematic_blur(label: str, width: int, duration: float = 0.45) -> tuple[str, str]:
    """Le clip démarre flou et devient net (mise au point)."""
    sigma = max(10, width // 40)
    out = f"{label}c"
    graph = (f"[{label}]split=2[{label}ca][{label}cb];"
             f"[{label}cb]gblur=sigma={sigma}:enable='lt(t,{_f(duration)})',format=yuva420p,"
             f"fade=t=out:st=0:d={_f(duration)}:alpha=1[{label}cc];"
             f"[{label}ca][{label}cc]overlay=enable='lt(t,{_f(duration)})'[{out}]")
    return graph, out
