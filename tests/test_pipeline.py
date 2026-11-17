"""Teste de ponta a ponta com dados SINTÉTICOS (nomes inventados).

Garante que: (1) o pipeline gera os datasets; (2) nenhum nome/ID original
vaza; (3) os pseudônimos são estáveis entre execuções; (4) duplicatas e
inconsistências são tratadas.

Rodar:  PYTHONPATH=src python -m pytest -q tests   (ou: python tests/test_pipeline.py)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd  # noqa: E402

from wa import build_dataset, check_privacy  # noqa: E402
from wa.collect import connect  # noqa: E402

TEACHERS = ["Ana Fictícia", "Bruno Inventado"]
STUDENTS = ["Carla Exemplo", "Diego Teste Silva", "Elisa Modelo", "Fabio Ficção"]


def make_fake_db(path: Path) -> None:
    con = connect(path)
    t0 = datetime(2026, 6, 1, 21, 0, 3, tzinfo=timezone.utc)
    iso = lambda d: d.isoformat().replace("+00:00", "Z")  # noqa: E731
    for r in range(2):
        room = f"/aula-{TEACHERS[r].split()[0].lower()}-{uuid.uuid4()}"
        con.execute("INSERT INTO rooms (room_name, raw, fetched_at) VALUES (?, ?, 'x')",
                    (room, json.dumps({"roomName": room})))
        for k in range(5):
            start = t0 + timedelta(days=k, hours=r)
            end = start + timedelta(minutes=55 if k else 4)  # 1 sessão curta
            sid = str(uuid.uuid4())
            sess = {"roomSessionId": sid, "roomName": room, "roomMode": "group",
                    "startedAt": iso(start), "endedAt": iso(end),
                    "totalParticipantMinutes": 100, "totalUniqueParticipants": 3}
            con.execute("INSERT INTO sessions (room_session_id, room_name, started_at, ended_at, raw, fetched_at)"
                        " VALUES (?, ?, ?, ?, ?, 'x')", (sid, room, iso(start), iso(end), json.dumps(sess)))
            people = [(TEACHERS[r], "host")] + [(n, "granted_visitor") for n in STUDENTS[r * 2: r * 2 + 2]]
            for i, (name, role) in enumerate(people):
                j = start + timedelta(minutes=i * 2)
                part = {"roomSessionId": sid, "participantId": f"/{uuid.uuid4()}", "deviceId": str(uuid.uuid4()),
                        "userId": None, "roleName": role,
                        "displayName": name.upper() if k == 2 else name,  # variação de caixa
                        "joinedAt": iso(j), "leftAt": iso(end) if i else None,  # leftAt ausente
                        "externalId": None, "userAgent": None}
                for _ in range(2 if i == 1 else 1):  # duplicata proposital
                    con.execute("INSERT OR IGNORE INTO participants VALUES (?, ?, ?, ?, 'x')",
                                (sid, part["participantId"] + ("" if _ == 0 else "-dup"), iso(j), json.dumps(part)))
    con.commit()


def run_pipeline(tmp: Path):
    build_dataset.main(["--since", "2026-05-17", "--raw-db", str(tmp / "raw.sqlite"),
                        "--mapping-db", str(tmp / "map.sqlite"), "--out", str(tmp / "public")])
    return (pd.read_parquet(tmp / "public" / "sessions.parquet"),
            pd.read_parquet(tmp / "public" / "participations.parquet"))


def test_pipeline_end_to_end():
    os.environ["PSEUDONYM_SECRET"] = "x" * 40  # segredo de TESTE
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        make_fake_db(tmp / "raw.sqlite")
        s1, p1 = run_pipeline(tmp)

        assert len(s1) == 10
        assert s1["is_short"].sum() == 2
        assert set(p1["role"]) == {"teacher", "student"}
        # 2 professores e 4 alunos, apesar da variação de maiúsculas
        assert p1.loc[p1.role == "teacher", "person_id"].nunique() == 2
        assert p1.loc[p1.role == "student", "person_id"].nunique() == 4
        assert p1["person_id"].str.match(r"^(Teacher|Student) \d{3}$").all()
        assert s1["room_id"].str.match(r"^Room \d{3}$").all()
        assert (p1["stay_min"] >= 0).all()

        # Nenhum dado original vaza
        assert check_privacy.main(["--public", str(tmp / "public"), "--raw-db", str(tmp / "raw.sqlite")]) == 0

        # Pseudônimos estáveis entre execuções
        s2, p2 = run_pipeline(tmp)
        assert p1["person_id"].tolist() == p2["person_id"].tolist()
        assert s1["session_id"].tolist() == s2["session_id"].tolist()

        q = json.loads((tmp / "public" / "quality_report.json").read_text())
        assert q["inconsistencies"]["participations_without_left_imputed"] == 10


def test_privacy_check_detects_leak():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        make_fake_db(tmp / "raw.sqlite")
        (tmp / "public").mkdir()
        pd.DataFrame({"x": ["Carla Exemplo"]}).to_parquet(tmp / "public" / "leak.parquet")
        assert check_privacy.main(["--public", str(tmp / "public"), "--raw-db", str(tmp / "raw.sqlite")]) == 1


if __name__ == "__main__":
    test_pipeline_end_to_end()
    test_privacy_check_detects_leak()
    print("todos os testes passaram")
