"""5. Cinematic Color Engine — étalonnage.

Les « looks » sont des LUT 3D (.cube) calculées par le programme : aucun
fichier tiers, aucune licence. Le look est choisi selon le profil colorimétrique
de la vidéo (Gameplay Intelligence) et l'identité visuelle du montage :

    teal_orange     ombres bleu-vert, peaux et explosions orangées (blockbuster)
    neon_night      contraste fort, magentas et cyans saturés (jeux sombres, néons)
    warm_cinematic  chaud et doux, noirs relevés (aventure, extérieurs)
    cold_tactical   froid, désaturé, contrasté (FPS tactique, militaire)
    punchy          saturation et contraste renforcés (jeux ternes ou délavés)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

LOOKS = ("teal_orange", "neon_night", "warm_cinematic", "cold_tactical", "punchy")


def _apply_look(rgb: np.ndarray, look: str, strength: float) -> np.ndarray:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    out = rgb.copy()

    def s_curve(x: np.ndarray, amount: float) -> np.ndarray:
        """Courbe en S : plus de contraste dans les tons moyens, extrêmes préservés."""
        x = np.clip(x, 0.0, 1.0)
        return x + amount * (0.5 - 0.5 * np.cos(np.pi * x) - x)

    def saturate(x: np.ndarray, amount: float) -> np.ndarray:
        lum = (0.2126 * x[..., 0] + 0.7152 * x[..., 1] + 0.0722 * x[..., 2])[..., None]
        return lum + (x - lum) * amount

    if look == "teal_orange":
        shadow = (1 - luma)[..., None]
        high = luma[..., None]
        out = out + shadow * np.array([-0.05, 0.02, 0.06]) + high * np.array([0.06, 0.01, -0.05])
        out = saturate(out, 1.15)
        out = s_curve(out, 0.35)
    elif look == "neon_night":
        out = saturate(out, 1.35)
        out = out + (1 - luma)[..., None] * np.array([0.03, -0.03, 0.07])
        out = s_curve(out, 0.55)
    elif look == "warm_cinematic":
        out = out * np.array([1.05, 1.0, 0.9]) + 0.03
        out = saturate(out, 1.05)
        out = s_curve(out, 0.25)
    elif look == "cold_tactical":
        out = out * np.array([0.93, 1.0, 1.07])
        out = saturate(out, 0.82)
        out = s_curve(out, 0.5)
    elif look == "punchy":
        out = saturate(out, 1.4)
        out = s_curve(out, 0.45)
    out = np.clip(out, 0.0, 1.0)
    return rgb + (out - rgb) * strength


def write_cube(path: Path, look: str, strength: float = 1.0, size: int = 33) -> Path:
    """Écrit une LUT 3D au format .cube (lue par le filtre FFmpeg lut3d)."""
    if look not in LOOKS:
        raise ValueError(f"look inconnu : {look}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    axis = np.linspace(0.0, 1.0, size)
    # ordre .cube : rouge varie le plus vite, puis vert, puis bleu
    b, g, r = np.meshgrid(axis, axis, axis, indexing="ij")
    rgb = np.stack([r, g, b], axis=-1).reshape(-1, 3)
    graded = _apply_look(rgb, look, strength)
    lines = [f'TITLE "{look}"', f"LUT_3D_SIZE {size}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    lines += [f"{x:.5f} {y:.5f} {z:.5f}" for x, y, z in graded]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")
    return path


def choose_look(profile: dict, seed: int = 0) -> str:
    """Look adapté à la vidéo : sombre et saturé -> néon, terne -> punchy, etc."""
    hue = float(profile.get("hue", 0.0))
    sat = float(profile.get("saturation", 0.3))
    luma = float(profile.get("luma", 0.45))
    if luma < 0.28 and sat > 0.25:
        return "neon_night"
    if sat < 0.18:
        return "punchy"
    if 0.5 <= hue <= 0.72 and sat < 0.4:
        return "cold_tactical" if seed % 2 else "teal_orange"
    if hue < 0.12 or hue > 0.92:
        return "warm_cinematic" if seed % 2 else "teal_orange"
    return ("teal_orange", "punchy")[seed % 2]


def grade_filter(cube: Path, strength_eq: float = 1.0) -> str:
    """Fragment FFmpeg : LUT + petite accentuation de netteté."""
    # chemin relatif : le rendu s'exécute dans le dossier de travail (pas de « : » Windows)
    return f"lut3d=file={cube.name}:interp=tetrahedral,unsharp=5:5:{0.35 * strength_eq:.2f}"
