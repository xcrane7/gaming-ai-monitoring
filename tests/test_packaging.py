"""Emballage YouTube : validation de la réponse IA, chapitres, repli local, miniature, fichier."""
import json
from pathlib import Path

from gaming_ai_editor import packaging
from gaming_ai_editor.ai.providers import ClaudeDirector, CostGuard
from gaming_ai_editor.editor.plan import EditPlan, Segment
from gaming_ai_editor.ffutil import probe

GOOD = {
    "titles": ["Valorant : le clutch 1 contre 3 que personne n'a vu venir",
               "J'étais seul contre trois… et ça a marché (Valorant)",
               "Valorant – la manche la plus folle de ma soirée"],
    "description": "Une manche perdue d'avance, un dernier joueur debout.\nRegarde jusqu'au bout.",
    "chapter_titles": ["Le premier duel", "La remontée", "Le clutch"],
    "hashtags": ["#valorant", "#gaming", "#clutch"],
    "thumbnail": {"text": "1 CONTRE 3", "idea": "le viseur sur le dernier ennemi"},
    "short_hook": "Il restait seul contre trois",
}


def long_plan(source="a.mp4") -> EditPlan:
    """Plan long de 70 s : 6 clips, dont deux trop rapprochés et un trop près de la fin."""
    segs = [Segment(source, 0, 12, 0.4, "mouvement", 6),
            Segment(source, 20, 32, 0.9, "pic sonore", 26,
                    events=[{"t": 26, "label": "kill"}]),
            Segment(source, 40, 52, 0.7, "série x3", 45),
            Segment(source, 55, 59, 0.5, "action intense", 57),
            Segment(source, 70, 82, 0.6, "action", 76),  # 4 s après le précédent : sauté
            Segment(source, 90, 98, 0.3, "mouvement", 94)]  # finit dans les 10 dernières s
    return EditPlan("long", 640, 360, 30, segments=segs)


def test_chapters_follow_the_real_plan():
    plan = long_plan()
    slots = packaging.chapter_slots(plan)
    times = [s["seconds"] for s in slots]
    assert times[0] == 0
    assert all(b - a >= 10 for a, b in zip(times, times[1:]))
    assert all(plan.duration - t >= 10 for t in times[1:])
    assert slots[1]["why"] == "kill", "l'étiquette de l'événement donne le sens du chapitre"
    pkg = packaging.local_package("valorant_ranked", "Valorant", slots, "kill", "")
    titles = [c["title"] for c in pkg["chapters"]]
    assert titles[0] == "Intro" and titles[1] == "L'élimination"
    assert "Série de 3 actions" in titles
    assert len(set(titles)) == len(titles) and not any(t.startswith("Moment") for t in titles)
    assert [c["t"] for c in pkg["chapters"]][:2] == ["00:00", "00:12"]
    # titres coupés sur un mot entier ; sans nom de jeu, pas de nom de fichier dans les titres
    assert packaging.fit("Fps Prototype Gameplay : ses meilleurs moments que personne "
                         "n'attendait") == "Fps Prototype Gameplay : ses meilleurs moments que personne"
    anon = packaging.local_package("fps_prototype_gameplay", "", slots, "", "")
    assert not any("Prototype" in t for t in anon["titles"]) and anon["hashtags"][0] == "#gaming"
    assert all(len(t) <= 65 for t in anon["titles"])


def test_validation_accepts_good_and_rejects_bad_answers():
    data, errors = packaging.validate(GOOD, 3)
    assert errors == [] and data["chapter_titles"][2] == "Le clutch"
    bad = {**GOOD, "titles": ["VALORANT INCROYABLE CLUTCH DE FOU MALADE", *GOOD["titles"][1:]]}
    assert any("majuscules" in e for e in packaging.validate(bad, 3)[1])
    assert any("chapter_titles" in e
               for e in packaging.validate({**GOOD, "chapter_titles": ["Moment 1", "a", "b"]}, 3)[1])
    assert any("chapter_titles" in e for e in packaging.validate(GOOD, 4)[1])
    assert any("hashtags" in e
               for e in packaging.validate({**GOOD, "hashtags": ["gaming", "#a b"]}, 3)[1])
    assert packaging.validate("pas du json", 3)[1]


def test_claude_gets_one_retry_then_local_fallback(cfg, monkeypatch):
    calls = []

    def fake_cli(command, prompt, timeout, extra=None, cwd=None):
        calls.append(prompt)
        return json.dumps(answers[len(calls) - 1])

    monkeypatch.setattr(packaging, "run_cli", fake_cli)
    monkeypatch.setattr(ClaudeDirector, "available", lambda self: True)
    director = ClaudeDirector(cfg, CostGuard(cfg), lambda *a: None)
    slots = packaging.chapter_slots(long_plan())
    answers = [{**GOOD, "titles": ["court"] * 3}, GOOD]
    data = packaging.ask_claude(director, "x", "Valorant", slots, ["- kill"], 70, "fr",
                                lambda *a: None)
    assert data["source"] == "claude" and len(calls) == 2
    assert "refusée" in calls[1] and "caractères" in calls[1], "l'erreur est renvoyée à Claude"
    calls.clear()
    answers = [{"titles": []}, {"titles": []}]
    assert packaging.ask_claude(director, "x", "", slots, [], 70, "fr", lambda *a: None) is None
    assert len(calls) == 2, "une seule nouvelle tentative"


def test_build_writes_metadata_and_thumbnail_without_ai(cfg, media):
    plan = long_plan(str(media["short"]))
    plan.segments = [s for s in plan.segments if s.end <= 58]
    out = {"format": "long", "path": str(media["short"]), "plan": plan.to_dict()}
    analyses = [{"highlights": [{"label": "kill", "score": 0.9,
                                 "meta": {"ai_why": "élimination au sniper"}},
                                {"label": "menu"}, {"label": "fail", "score": 0.4}]}]
    pkg = packaging.build(cfg, CostGuard(cfg), lambda *a: None, "valorant_soiree", [out],
                          analyses, cfg.output_dir / "reports")
    path = Path(pkg["metadata_path"])
    assert path == cfg.output_dir / "reports" / "valorant_soiree.metadata.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["source"] == "local" and len(saved["titles"]) == 3
    assert all(len(t) <= 65 for t in saved["titles"])
    assert 3 <= len(saved["hashtags"]) <= 5 and saved["thumbnail"]["text"] == "ÉLIMINÉ"
    assert saved["chapters"][0] == {"t": "00:00", "title": "Intro"}
    assert "00:00 Intro" in saved["description_full"]
    best = max(plan.segments, key=lambda s: s.score)
    expected = plan.offsets()[plan.segments.index(best)] + best.map_time(best.peak)
    assert abs(saved["thumbnail"]["frame_time"] - expected) < 0.05, "miniature au pic"
    assert saved["thumbnail"]["source_time"] == best.peak, "image propre de la source"
    assert all(" " not in t[-1:] and not t.endswith("'") for t in saved["titles"])
    thumb = Path(saved["thumbnail"]["path"])
    assert thumb.suffix == ".png" and (probe(thumb).width, probe(thumb).height) == (1280, 720)
    assert packaging.best_moment_label([{"highlights": [{"label": "action"},
                                                         {"label": "fail"}]}]) == ""
    # nom du jeu : option > profil de jeu > rien
    cfg.data["games"] = {"valorant": {"match": ["valorant"]}}
    assert packaging.game_name(cfg, "valorant_soiree") == "Valorant"
    cfg.data["youtube"]["game_name"] = "Apex Legends"
    assert packaging.game_name(cfg, "valorant_soiree") == "Apex Legends"


def test_dashboard_lists_packages_and_serves_only_output_thumbnails(cfg, tmp_path):
    from gaming_ai_editor.dashboard.server import package_thumbnail, packages

    reports = cfg.output_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    inside = cfg.output_dir / "youtube" / "a_thumbnail.png"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_bytes(b"png")
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"png")
    for name, thumb in (("a", inside), ("b", outside)):
        (reports / f"{name}.metadata.json").write_text(json.dumps(
            {"titles": ["t"] * 3, "thumbnail": {"path": str(thumb)}}), encoding="utf-8")
    listed = {p["id"]: p for p in packages(cfg)}
    assert set(listed) == {"a", "b"}
    assert package_thumbnail(cfg, "a") == inside.resolve()
    assert package_thumbnail(cfg, "b") is None, "pas de fichier hors de OUTPUT"
    assert package_thumbnail(cfg, "../a") is None
