"""Relecture critique d'un rendu (skill edit-critic).

    python -m gaming_ai_editor.tools.critic <rendu.mp4> [--before <ancien.mp4>]
                                            [--plan <rapport.json>] --out <dossier>

Produit de quoi juger avec les yeux (planche d'images, images à chaque jonction et au
milieu de chaque clip) et les contrôles automatiques de la checklist anti-robot.
Les points subjectifs (accroche lisible, texte qui masque l'action...) restent à
vérifier en regardant les images.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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


def critique(render: Path, out: Path, plan_dict: dict | None = None,
             before: Path | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    info = probe(render)
    files = {"planche": str(sheet(render, out / "planche.png"))}
    report: dict = {"rendu": str(render), "durée": round(info.duration, 2),
                    "format": f"{info.width}x{info.height}", "images": files}
    plan_dict = plan_dict or find_plan(render)
    if plan_dict:
        plan = EditPlan.from_dict(plan_dict)
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
    (out / "critique.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="critic", description=__doc__)
    p.add_argument("render")
    p.add_argument("--before")
    p.add_argument("--plan", help="rapport JSON (OUTPUT/reports) ou plan JSON")
    p.add_argument("--out", required=True)
    a = p.parse_args(argv)
    plan = None
    if a.plan:
        data = json.loads(Path(a.plan).read_text(encoding="utf-8"))
        plan = data.get("segments") and data or next(
            (o["plan"] for o in data.get("outputs", [])
             if Path(o["path"]).name == Path(a.render).name), None)
    rep = critique(Path(a.render), Path(a.out), plan, Path(a.before) if a.before else None)
    print(json.dumps({k: rep[k] for k in rep if k != "images"}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
