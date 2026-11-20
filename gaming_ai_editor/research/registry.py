"""Registre versionné des skills : activation, historique, rollback, quarantaine.

Statuts :
    CANDIDATE   découvert ou proposé, pas encore testé
    TESTING     en cours d'évaluation (bac à sable, benchmark)
    VALIDATED   tests réussis ; ou ancienne version active, gardée pour un retour arrière
    ACTIVE      utilisé en production (un seul par type)
    REJECTED    tests échoués ou pas meilleur que la version active
    QUARANTINE  retiré après une régression constatée (n'est plus jamais réactivé seul)

Une seule version active par « kind ». Les versions précédentes ne sont jamais
supprimées, ce qui permet de revenir en arrière à tout moment.
"""
from __future__ import annotations

import json
import time

from ..db import Database
from ..ffutil import BUILTIN_ENCODE_PROFILE

CANDIDATE, TESTING, VALIDATED = "CANDIDATE", "TESTING", "VALIDATED"
ACTIVE, REJECTED, QUARANTINE = "ACTIVE", "REJECTED", "QUARANTINE"
STATUSES = (CANDIDATE, TESTING, VALIDATED, ACTIVE, REJECTED, QUARANTINE)
_LEGACY = {"candidate": CANDIDATE, "validated": VALIDATED, "active": ACTIVE,
           "rejected": REJECTED, "retired": VALIDATED, "rolled_back": QUARANTINE}

BUILTINS = {
    "encode_profile": {"name": "builtin", "version": "1", "params": BUILTIN_ENCODE_PROFILE,
                       "license": "interne"},
    "scene_detector": {"name": "builtin-histogram", "version": "1",
                       "params": {"impl": "builtin"}, "license": "interne"},
    "motion_preset": {"name": "builtin", "version": "1",
                      "params": {"punch": 1.0, "shake": 1.0, "flash": 1.0}, "license": "interne"},
    "decode_accel": {"name": "auto", "version": "1", "params": {"hwaccel": "auto"},
                     "license": "interne"},
}


def _row(row: dict | None) -> dict | None:
    if row is None:
        return None
    row = dict(row)
    for key in ("params", "metrics"):
        try:
            row[key] = json.loads(row[key]) if row.get(key) else {}
        except json.JSONDecodeError:
            row[key] = {}
    return row


class SkillRegistry:
    def __init__(self, db: Database):
        self.db = db
        for old, new in _LEGACY.items():  # bases créées avant les statuts en majuscules
            self.db.execute("UPDATE skills SET status=? WHERE status=?", (new, old))
        self.ensure_builtin()

    def ensure_builtin(self) -> None:
        for kind, spec in BUILTINS.items():
            if not self.db.one("SELECT id FROM skills WHERE kind=? AND status='ACTIVE'", (kind,)):
                existing = self.db.one(
                    "SELECT id FROM skills WHERE kind=? AND name=? AND version=?",
                    (kind, spec["name"], spec["version"]))
                if existing:
                    self.db.update("skills", existing["id"], status=ACTIVE,
                                   activated_at=time.time())
                else:
                    self.db.insert("skills", kind=kind, name=spec["name"],
                                   version=spec["version"], status=ACTIVE,
                                   params=json.dumps(spec["params"]), license=spec["license"],
                                   origin="builtin", created_at=time.time(),
                                   activated_at=time.time())

    def active(self, kind: str) -> dict | None:
        return _row(self.db.one("SELECT * FROM skills WHERE kind=? AND status='ACTIVE' "
                                "ORDER BY activated_at DESC LIMIT 1", (kind,)))

    def get(self, skill_id: int) -> dict | None:
        return _row(self.db.one("SELECT * FROM skills WHERE id=?", (skill_id,)))

    def list(self) -> list[dict]:
        return [_row(r) for r in self.db.query("SELECT * FROM skills ORDER BY kind, id DESC")]

    def history(self, limit: int = 50) -> list[dict]:
        return self.db.query(
            "SELECT h.*, s.name AS name, s.version AS version FROM skill_history h "
            "LEFT JOIN skills s ON s.id = h.skill_id ORDER BY h.id DESC LIMIT ?", (limit,))

    def add_candidate(self, kind: str, name: str, version: str, params: dict,
                      license: str = "interne", origin: str = "researcher") -> int:
        existing = self.db.one("SELECT id FROM skills WHERE kind=? AND name=? AND version=?",
                               (kind, name, version))
        if existing:
            return existing["id"]
        return self.db.insert("skills", kind=kind, name=name, version=version,
                              status=CANDIDATE, params=json.dumps(params), license=license,
                              origin=origin, created_at=time.time())

    def record_result(self, skill_id: int, status: str, metrics: dict,
                      score: float | None = None) -> None:
        self.db.update("skills", skill_id, status=status, metrics=json.dumps(metrics),
                       score=score)

    def set_status(self, skill_id: int, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(status)
        self.db.update("skills", skill_id, status=status)

    def promote(self, skill_id: int, reason: str) -> None:
        """Active une version VALIDATED (l'ancienne active redevient VALIDATED).

        Refuse une version en QUARANTINE ou REJECTED, et une version dont le score
        mesuré (même métrique) est inférieur à celui de la version active.
        """
        skill = self.get(skill_id)
        if not skill:
            raise ValueError(f"skill {skill_id} inconnu")
        if skill["status"] in (QUARANTINE, REJECTED):
            raise ValueError(f"{skill['name']} est {skill['status']} : activation refusée")
        prev = self.active(skill["kind"])
        if prev and prev["id"] == skill_id:
            return
        metric = (skill.get("metrics") or {}).get("metric")
        if prev and metric and metric == (prev.get("metrics") or {}).get("metric") \
                and prev.get("score") is not None and skill.get("score") is not None \
                and skill["score"] < prev["score"]:
            raise ValueError("score inférieur à la version active : activation refusée")
        if prev:
            self.db.update("skills", prev["id"], status=VALIDATED)
        self.db.update("skills", skill_id, status=ACTIVE, activated_at=time.time())
        self.db.insert("skill_history", kind=skill["kind"], skill_id=skill_id,
                       prev_skill_id=prev["id"] if prev else None, action="promote",
                       reason=reason, at=time.time())
        self.db.log("skills", f"Skill activé : {skill['kind']} -> {skill['name']} "
                              f"v{skill['version']} ({reason})")

    def rollback(self, kind: str, reason: str = "rollback manuel",
                 quarantine: bool = True) -> dict | None:
        """Réactive la version qui était active avant la dernière promotion.

        La version retirée passe en QUARANTINE (régression) ou VALIDATED.
        """
        current = self.active(kind)
        if not current:
            return None
        last = self.db.one(
            "SELECT * FROM skill_history WHERE kind=? AND action='promote' AND skill_id=? "
            "ORDER BY id DESC LIMIT 1", (kind, current["id"]))
        if not last or not last["prev_skill_id"]:
            return None
        self.db.update("skills", current["id"], status=QUARANTINE if quarantine else VALIDATED)
        self.db.update("skills", last["prev_skill_id"], status=ACTIVE,
                       activated_at=time.time())
        self.db.insert("skill_history", kind=kind, skill_id=last["prev_skill_id"],
                       prev_skill_id=current["id"],
                       action="quarantine" if quarantine else "rollback", reason=reason,
                       at=time.time())
        restored = self.get(last["prev_skill_id"])
        self.db.log("skills", f"Rollback : {kind} revient à {restored['name']} "
                              f"v{restored['version']} ({reason})", "warn")
        return restored

    def quarantine_active(self, kind: str, reason: str) -> dict | None:
        """Régression constatée en production : la version active est mise en quarantaine."""
        current = self.active(kind)
        if not current or current.get("origin") == "builtin":
            return None
        return self.rollback(kind, f"régression en production : {reason}", quarantine=True)
