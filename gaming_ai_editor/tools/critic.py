"""Relecture critique d'un rendu (skill edit-critic).

    python -m gaming_ai_editor.tools.critic <rendu.mp4> [--before <ancien.mp4>]
                                            [--plan <rapport.json>] [--out <dossier>]

Produit :
- des **données objectives** : durées des clips, coupes par seconde au début et à la fin,
  courbe d'énergie audio (montée ? pic ?), part de temps calme, accroche présente ;
- des **images clés** (0,1 s, 0,8 s, 1,5 s, pic, milieu, fin), une planche (1 image / s),
  les images de chaque jonction ;
- la **grille anti-robot** (note /10 par ligne avec justification) et un **verdict** : les
  3 problèmes les plus graves, chacun rattaché à un fichier ;
- en A/B (`--before`) : planches côte à côte et tableau des métriques avant / après.

Sans `--out`, le résultat va dans data/critique/<date>_<vidéo>/ (non versionné).
Les points subjectifs (lisibilité, texte qui masque l'action) se jugent EN REGARDANT les
images : la grille le signale au lieu d'inventer une note.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import numpy as np

from ..editor.plan import EditPlan
from ..ffutil import probe, run_ffmpeg


def find_plan(render: Path) -> dict | None:
    """Plan de montage du rendu, retrouvé dans OUTPUT/reports/."""
    for reports in (render.parent.parent / "reports", render.parent / "reports"):
        for rep in sorted(reports.glob("*.json"), reverse=True) if reports.is_dir() else []:
            try:
                data = json.loads(rep.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for out in data.get("outputs", []):
                if Path(out.get("path", "")).resolve() == render.resolve():
                    return out.get("plan")
    return None


def sheet(video: Path, out: Path, every: float = 1.0, cols: int = 10, width: int = 180) -> Path:
    rows = max(1, int(-(-probe(video).duration // (every * cols))))
    run_ffmpeg(["-i", str(video), "-vf", f"fps=1/{every},scale={width}:-2,"
                f"tile={cols}x{rows}:padding=3", "-frames:v", "1", str(out)])
    return out


def frame(video: Path, t: float, out: Path, width: int = 360) -> Path:
    run_ffmpeg(["-ss", f"{max(0.0, t):.3f}", "-i", str(video), "-frames:v", "1",
                "-vf", f"scale={width}:-2", str(out)])
    return out


def metrics(render: Path, plan: EditPlan | None) -> dict:
    """Mesures objectives du rendu (et du plan s'il est connu)."""
    from ..qa.motion import _audio_energy, _gray_frames

    info = probe(render)
    dur = max(info.duration, 0.1)
    energy = _audio_energy(render, hop=0.5)  # RMS en dB par tranche de 0,5 s
    luma, diff = _gray_frames(render)
    fps = info.fps or 30
    per = max(1, int(round(fps * 0.5)))
    motion = [float(np.mean(diff[i:i + per])) for i in range(1, len(diff), per)]
    n = min(len(energy), len(motion))
    calm = [(energy[i] < -35 and motion[i] < 0.006) for i in range(n)]
    thirds = np.array_split(np.asarray(energy[:n]), 3) if n >= 3 else [np.asarray(energy)] * 3
    out = {
        "durée": round(info.duration, 2),
        "énergie_audio_db": [round(float(x), 1) for x in energy[:n]],
        "pic_audio_à": round(float(np.argmax(energy[:n])) * 0.5 / dur, 2) if n else None,
        "montée_db": round(float(thirds[2].mean() - thirds[0].mean()), 1) if n >= 3 else 0.0,
        "temps_calme": round(sum(calm) / max(n, 1), 3),
    }
    if plan is not None:
        durs = [s.out_duration for s in plan.segments]
        cuts = plan.junctions()
        out.update({
            "clips": len(durs),
            "durée_clips": {"moyenne": round(statistics.mean(durs), 2),
                            "écart_type": round(statistics.pstdev(durs), 2),
                            "min": round(min(durs), 2), "max": round(max(durs), 2)},
            "coupes_par_s_début": round(sum(1 for c in cuts if c <= 3.0) / 3.0, 2),
            "coupes_par_s_fin": round(sum(1 for c in cuts if c >= dur - 3.0) / 3.0, 2),
            "accroche_2s": any(g["type"] == "hook" and g["start"] < 2.0 for g in plan.graphics),
        })
    return out


def key_times(plan: EditPlan | None, m: dict, duration: float) -> dict[str, float]:
    """0,1 s, 0,8 s, 1,5 s, le pic, le milieu et la fin."""
    peak = None
    if plan is not None:
        offsets = plan.offsets()
        for seg, off in zip(plan.segments, offsets):
            ramp = seg.speed_ramp()
            if ramp and ramp.slow:
                peak = off + ramp.map((ramp.start + ramp.end) / 2)
    if peak is None and m.get("pic_audio_à") is not None:
        peak = m["pic_audio_à"] * duration
    times = {"0.1s": 0.1, "0.8s": 0.8, "1.5s": 1.5, "pic": peak or duration * 0.8,
             "milieu": duration / 2, "fin": max(0.0, duration - 0.15)}
    return {k: round(min(max(v, 0.0), duration - 0.05), 2) for k, v in times.items()}


FIXES = {
    "accroche": ("Ouvrir sur le meilleur moment avec un texte lisible avant 1,5 s",
                 "editor/plan.py (_story_order), engines/editing.py (choose_hook)"),
    "variété des durées": ("Varier la longueur des clips (pas deux proches de suite)",
                           "engines/editing.py (vary_durations)"),
    "courbe d'intensité": ("Placer le climax dans le dernier tiers et faire monter l'énergie",
                           "editor/plan.py (_story_order), engines/audio.py (generate_music)"),
    "texte et légendes": ("Ajouter accroche, mots d'impact ou sous-titres",
                          "engines/editing.py (build_graphics_and_sound), editor/subtitles.py"),
    "impacts sonores": ("Un son d'impact sur chaque moment fort",
                        "engines/editing.py (sfx_*), engines/audio.py (render_sfx_track)"),
    "musique": ("Musique qui accompagne : montée, drop, calée sur le tempo",
                "engines/editing.py (plan_music), editor/render.py (_music)"),
    "fin voulue": ("Fin en boucle ou appel à l'action, sans traîne",
                   "editor/plan.py (loop_ending), engines/editing.py (end_on_beat)"),
    "transitions": ("Varier les transitions visibles sans en abuser",
                    "engines/editing.py (choose_transitions)"),
    "temps morts": ("Retirer ou accélérer les passages sans action",
                    "engines/editing.py (drop_dead_clips, speed_up_dead_time)"),
}


def grade(plan: EditPlan | None, m: dict) -> list[dict]:
    """Grille anti-robot : note /10 et justification par ligne (heuristiques mesurées).

    Les lignes marquées « à vérifier en regardant » ont une note mesurée partielle :
    la lisibilité réelle se juge sur les images clés.
    """
    g: list[dict] = []

    def add(name, score, why, look=False):
        g.append({"ligne": name, "note": int(max(0, min(10, round(score)))), "pourquoi": why,
                  "à_regarder": look, "correction": FIXES[name][0], "fichier": FIXES[name][1]})

    if plan is None:
        return g
    durs = [s.out_duration for s in plan.segments]
    first = plan.segments[0].role if plan.segments else ""
    hook = next((x for x in plan.graphics if x["type"] == "hook"), None)
    add("accroche", 10 if (hook and hook["start"] < 1.5 and first == "teaser") else
        7 if hook and hook["start"] < 1.5 else 4 if first == "teaser" else 2,
        f"texte : {'oui à ' + str(round(hook['start'], 1)) + ' s' if hook else 'non'}, "
        f"ouverture : {first or 'clip normal'}", look=True)
    cv = statistics.pstdev(durs) / max(statistics.mean(durs), 1e-6) if len(durs) > 1 else 0
    close = sum(1 for a, b in zip(durs, durs[1:]) if abs(a - b) / max(a, b) < 0.2)
    add("variété des durées", min(10, cv * 20) - 3 * close,
        f"coefficient de variation {cv:.2f}, {close} paire(s) de durées proches")
    pos, rise = m.get("pic_audio_à") or 0, m.get("montée_db", 0)
    climax = [i for i, s in enumerate(plan.segments)
              if (s.speed_ramp() and s.speed_ramp().slow) or s.role == "climax"]
    cpos = (plan.offsets()[climax[-1]] + plan.segments[climax[-1]].out_duration / 2) / \
        max(plan.duration, 1) if climax else 0
    add("courbe d'intensité", (5 if cpos >= 0.6 else 1) + (3 if pos >= 0.5 else 0)
        + (2 if rise > 0 else 0),
        f"climax à {cpos:.0%}, pic audio à {pos:.0%}, énergie {rise:+.1f} dB du 1er au "
        "dernier tiers")
    words = sum(1 for x in plan.graphics if x["type"] in ("word", "notify", "combo"))
    caps = any(x["type"] == "captions" for x in plan.graphics)
    add("texte et légendes", min(10, (4 if hook else 0) + min(4, words) + (2 if caps else 0)),
        f"accroche {'oui' if hook else 'non'}, {words} légendes d'événement, sous-titres "
        f"{'oui' if caps else 'non'}", look=True)
    events = sum(len(s.events) for s in plan.segments)
    hits = sum(1 for c in plan.sfx if c["kind"] in ("impact", "boom", "glitch"))
    add("impacts sonores", 10 * min(1.0, hits / max(events, 1)),
        f"{hits} sons d'impact pour {events} moments animés")
    mus = plan.music or {}
    add("musique", {"generated": 9, "user": 8}.get(mus.get("mode"), 2)
        + (1 if mus.get("drop_at") else 0),
        f"mode {mus.get('mode', 'aucune')}, tempo {mus.get('bpm', '-')}, drop "
        f"{'oui' if mus.get('drop_at') else 'non'}")
    add("fin voulue", 10 if plan.loop or any(x["type"] == "subscribe" for x in plan.graphics)
        else 3, "boucle" if plan.loop else "appel à l'action" if
        any(x["type"] == "subscribe" for x in plan.graphics) else "fondu au noir")
    visible = [(n, d) for n, d in zip(plan.transitions, plan.overlaps()) if d >= 0.1]
    names = [n for n, _ in visible]
    repeats = sum(1 for a, b in zip(names, names[1:]) if a == b)
    per_min = len(visible) / max(plan.duration / 60, 1e-6)
    add("transitions", 10 - 3 * repeats - (3 if per_min > 12 else 0)
        - (4 if len(set(names)) < 2 and len(names) > 2 else 0),
        f"{len(visible)} visibles ({len(set(names))} sortes), {repeats} répétition(s), "
        f"{per_min:.0f}/min")
    calm = m.get("temps_calme", 0)
    add("temps morts", 10 - calm * 50, f"{calm:.0%} du temps sans son ni mouvement notable")
    return g


def checklist(plan: EditPlan, duration: float) -> list[dict]:
    """Contrôles automatiques (oui / non + preuve) de la checklist anti-robot."""
    items = []
    durs = [s.out_duration for s in plan.segments]
    same = [(i, round(a, 2), round(b, 2)) for i, (a, b) in enumerate(zip(durs, durs[1:]))
            if max(a, b) > 0 and abs(a - b) / max(a, b) < 0.2]
    items.append({"point": "durées de clips variées (pas deux à ±20 % de suite)",
                  "ok": not same, "preuve": f"durées {[round(d, 1) for d in durs]}"
                  + (f", trop proches : {same}" if same else "")})
    offsets = plan.offsets()
    climax = [i for i, s in enumerate(plan.segments)
              if (s.speed_ramp() and s.speed_ramp().slow) or s.role == "climax"]
    if climax:
        pos = (offsets[climax[-1]] + plan.segments[climax[-1]].out_duration / 2) / max(duration, 1)
        items.append({"point": "climax dans le dernier tiers", "ok": pos >= 0.6,
                      "preuve": f"climax à {pos:.0%} de la vidéo"})
    else:
        items.append({"point": "climax dans le dernier tiers", "ok": False,
                      "preuve": "aucun ralenti / climax dans le plan"})
    names = plan.transitions or []
    repeats = [i for i, (a, b) in enumerate(zip(names, names[1:]))
               if a == b and plan.overlaps()[i] >= 0.15 and plan.overlaps()[i + 1] >= 0.15]
    items.append({"point": "jamais la même transition visible deux fois de suite",
                  "ok": not repeats, "preuve": f"transitions {names}"})
    hook = any(g["type"] == "hook" and g["start"] < 1.0 for g in plan.graphics)
    first = plan.segments[0].role if plan.segments else ""
    items.append({"point": "accroche dans les 2 premières secondes",
                  "ok": hook or first == "teaser",
                  "preuve": f"texte d'accroche : {'oui' if hook else 'non'}, ouverture : "
                            f"{first or 'clip normal'}"})
    items.append({"point": "fin nette (boucle) ou appel à l'action",
                  "ok": plan.loop or any(g["type"] == "subscribe" for g in plan.graphics),
                  "preuve": "boucle" if plan.loop else "bouton d'abonnement"
                  if any(g["type"] == "subscribe" for g in plan.graphics) else "fondu au noir"})
    claims = [g.get("text") for g in plan.graphics if g["type"] in ("headshot", "victory")]
    ai = any(s.label in ("kill", "headshot", "victoire") for s in plan.segments) or \
        any(ev.get("label") for s in plan.segments for ev in s.events)
    items.append({"point": "aucune affirmation inventée (kill / headshot / victoire)",
                  "ok": not claims or ai,
                  "preuve": "affirmations appuyées par une étiquette IA" if claims else
                            "aucune affirmation"})
    return items


def key_sheet(video: Path, times: dict[str, float], out: Path, width: int = 270) -> Path:
    """Les 6 images clés côte à côte."""
    imgs = [frame(video, t, out.parent / f"_cle_{k}.png", width) for k, t in times.items()]
    args = []
    for p in imgs:
        args += ["-i", str(p)]
    run_ffmpeg([*args, "-filter_complex", "".join(f"[{i}]" for i in range(len(imgs)))
                + f"hstack=inputs={len(imgs)}", str(out)])
    for p in imgs:
        p.unlink(missing_ok=True)
    return out


def critique(render: Path, out: Path, plan_dict: dict | None = None,
             before: Path | None = None, before_plan: dict | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    info = probe(render)
    files = {"planche": str(sheet(render, out / "planche.png"))}
    report: dict = {"rendu": str(render), "durée": round(info.duration, 2),
                    "format": f"{info.width}x{info.height}", "images": files}
    plan_dict = plan_dict or find_plan(render)
    plan = EditPlan.from_dict(plan_dict) if plan_dict else None
    m = metrics(render, plan)
    report["métriques"] = {k: v for k, v in m.items() if k != "énergie_audio_db"}
    report["courbe_audio_db"] = m["énergie_audio_db"]
    times = key_times(plan, m, info.duration)
    for k, t in times.items():
        files[f"clé_{k}"] = str(frame(render, t, out / f"cle_{k}.png"))
    files["images_clés"] = str(key_sheet(render, times, out / "images_cles.png"))
    report["grille"] = grade(plan, m)
    if report["grille"]:
        report["note_globale"] = round(sum(x["note"] for x in report["grille"])
                                       / len(report["grille"]), 1)
        # seulement les vraies faiblesses (< 8/10) : pas de faux problème pour remplir
        worst = [w for w in sorted(report["grille"], key=lambda x: x["note"]) if w["note"] < 8][:3]
        report["verdict"] = [{"problème": f"{w['ligne']} ({w['note']}/10) : {w['pourquoi']}",
                              "correction": w["correction"], "fichier": w["fichier"]}
                             for w in worst]
    if plan is not None:
        offsets = plan.offsets()
        for i, (seg, off) in enumerate(zip(plan.segments, offsets)):
            files[f"clip{i + 1}_milieu"] = str(frame(render, off + seg.out_duration / 2,
                                                     out / f"clip{i + 1}_milieu.png"))
        for j, t in enumerate(plan.junctions()):
            files[f"jonction{j + 1}_avant"] = str(frame(render, t - 0.15,
                                                        out / f"jonction{j + 1}_avant.png"))
            files[f"jonction{j + 1}_apres"] = str(frame(render, t + 0.15,
                                                        out / f"jonction{j + 1}_apres.png"))
        report["clips"] = [{"rôle": s.role or "-", "source": f"{s.start:.1f}-{s.end:.1f}s",
                            "durée": round(s.out_duration, 2),
                            "ralenti": bool(s.ramp) and s.ramp[2] < 1,
                            "accéléré": bool(s.ramp) and s.ramp[2] > 1,
                            "événements": [e["kind"] for e in s.events]}
                           for s in plan.segments]
        report["transitions"] = [{"type": n, "durée": d} for n, d in
                                 zip(plan.transitions, plan.overlaps())]
        report["style"] = {k: plan.style.get(k) for k in ("palette_name", "look", "transitions",
                                                          "layout") if plan.style}
        report["checklist"] = checklist(plan, info.duration)
        report["score_checklist"] = (f"{sum(1 for c in report['checklist'] if c['ok'])}/"
                                     f"{len(report['checklist'])}")
    else:
        report["checklist"] = "plan introuvable : passer --plan <rapport.json>"
    if before is not None:
        files["avant_planche"] = str(sheet(before, out / "avant_planche.png"))
        run_ffmpeg(["-i", str(out / "avant_planche.png"), "-i", str(out / "planche.png"),
                    "-filter_complex", "[0][1]scale2ref=oh*mdar:ih[a][b];[a][b]hstack",
                    str(out / "avant_apres.png")])
        files["avant_apres"] = str(out / "avant_apres.png")
        bplan_dict = before_plan or find_plan(before)
        bplan = EditPlan.from_dict(bplan_dict) if bplan_dict else None
        bm = metrics(before, bplan)
        btimes = key_times(bplan, bm, probe(before).duration)
        key_sheet(before, btimes, out / "avant_images_cles.png")
        run_ffmpeg(["-i", str(out / "avant_images_cles.png"), "-i", str(out / "images_cles.png"),
                    "-filter_complex", "[0][1]scale2ref=iw:ow/mdar[a][b];[a][b]vstack",
                    str(out / "avant_apres_images_cles.png")])
        files["avant_apres_images_clés"] = str(out / "avant_apres_images_cles.png")
        bgrid = {x["ligne"]: x["note"] for x in grade(bplan, bm)}
        report["avant_après"] = {
            "métriques": {k: [bm.get(k), m.get(k)] for k in m if k != "énergie_audio_db"},
            "grille": {x["ligne"]: [bgrid.get(x["ligne"]), x["note"]] for x in report["grille"]},
        }
    (out / "critique.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    (out / "critique.md").write_text(to_markdown(report), encoding="utf-8")
    return report


def to_markdown(rep: dict) -> str:
    lines = [f"# Critique de {Path(rep['rendu']).name}", "",
             f"Durée {rep['durée']} s · {rep['format']}"
             + (f" · note globale {rep['note_globale']}/10" if "note_globale" in rep else ""), ""]
    if rep.get("grille"):
        lines += ["| Ligne | Note | Pourquoi |", "|---|---|---|"]
        for g in rep["grille"]:
            flag = " (à vérifier sur les images)" if g["à_regarder"] else ""
            lines.append(f"| {g['ligne']} | {g['note']}/10 | {g['pourquoi']}{flag} |")
        lines += ["", "## Verdict : 3 problèmes les plus graves", ""]
        for i, v in enumerate(rep.get("verdict", []), 1):
            lines.append(f"{i}. {v['problème']} → {v['correction']} (`{v['fichier']}`)")
        if not rep.get("verdict"):
            lines.append("Aucune ligne sous 8/10 : regarder les images clés pour juger le reste.")
    if rep.get("avant_après"):
        lines += ["", "## Avant / après", "", "| Mesure | Avant | Après |", "|---|---|---|"]
        for k, (a, b) in rep["avant_après"]["grille"].items():
            lines.append(f"| {k} | {a} | {b} |")
        for k, (a, b) in rep["avant_après"]["métriques"].items():
            if not isinstance(a, dict) and not isinstance(b, dict):
                lines.append(f"| {k} | {a} | {b} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="critic", description=__doc__)
    p.add_argument("render")
    p.add_argument("--before")
    p.add_argument("--plan", help="rapport JSON (OUTPUT/reports) ou plan JSON")
    p.add_argument("--before-plan", help="rapport JSON de la version « avant »")
    p.add_argument("--out", help="dossier de sortie (défaut : data/critique/<date>_<vidéo>)")
    a = p.parse_args(argv)
    if not a.out:
        from ..config import Config

        a.out = str(Config().data_dir / "critique"
                    / f"{time.strftime('%Y%m%d_%H%M%S')}_{Path(a.render).stem}")
    def load(path: str | None, render: str | None) -> dict | None:
        if not path:
            return None
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("segments"):
            return data
        outs = data.get("outputs", [])
        return next((o["plan"] for o in outs if render and Path(o["path"]).name ==
                     Path(render).name), outs[0]["plan"] if len(outs) == 1 else None)

    rep = critique(Path(a.render), Path(a.out), load(a.plan, a.render),
                   Path(a.before) if a.before else None, load(a.before_plan, None))
    print(to_markdown(rep))
    print(f"Images et rapport : {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
