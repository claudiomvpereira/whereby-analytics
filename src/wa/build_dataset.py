"""Gera os datasets PÚBLICOS (anonimizados e tratados) a partir do SQLite privado.

Fluxo: data/private/whereby.sqlite (bruto, com nomes)
        -> limpeza estrutural (duplicatas, ausentes, inconsistências)
        -> pseudonimização HMAC + generalização + remoção de campos pessoais
        -> data/public/*.parquet|csv + quality_report.json

Uso:
    PYTHONPATH=src python -m wa.build_dataset [--since 2026-05-17]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from wa.anonymize import PseudonymRegistry, get_secret, normalize_name

ROOT = Path(__file__).resolve().parents[2]
RAW_DB = ROOT / "data" / "private" / "whereby.sqlite"
MAPPING_DB = ROOT / "data" / "private" / "pseudonyms.sqlite"
PUBLIC_DIR = ROOT / "data" / "public"
TZ = "America/Sao_Paulo"
MAX_SESSION_H = 6        # sessões acima disso são consideradas inconsistentes
SHORT_SESSION_MIN = 10   # sessões abaixo disso são "muito curtas"
TEACHER_ROLES = {"host", "owner", "member"}
STUDENT_ROLES = {"visitor", "granted_visitor", "viewer", "granted_viewer"}


# --------------------------------------------------------------------------- carga
def load_raw(db: Path, since: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    con = sqlite3.connect(db)
    s = pd.DataFrame(
        [json.loads(r) for (r,) in con.execute("SELECT raw FROM sessions WHERE started_at >= ?", (since,))]
    )
    p = pd.DataFrame(
        [
            json.loads(r)
            for (r,) in con.execute(
                """SELECT p.raw FROM participants p JOIN sessions s USING (room_session_id)
                   WHERE s.started_at >= ?""",
                (since,),
            )
        ]
    )
    con.close()
    return s, p


def missing_report(df: pd.DataFrame) -> dict:
    return {c: int(df[c].isna().sum()) for c in df.columns if df[c].isna().any()}


# --------------------------------------------------------------------------- limpeza
def clean(s: pd.DataFrame, p: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    q: dict = {"raw": {"sessions": len(s), "participations": len(p)}}
    q["missing_raw"] = {"sessions": missing_report(s), "participations": missing_report(p)}

    for col in ("startedAt", "endedAt"):
        s[col] = pd.to_datetime(s[col], utc=True, errors="coerce")
    for col in ("joinedAt", "leftAt"):
        p[col] = pd.to_datetime(p[col], utc=True, errors="coerce")

    # Duplicatas
    n = len(s)
    s = s.drop_duplicates(subset="roomSessionId")
    q["duplicates_removed"] = {"sessions": n - len(s)}
    n = len(p)
    p = p.drop_duplicates(subset=["roomSessionId", "participantId", "joinedAt"])
    q["duplicates_removed"]["participations"] = n - len(p)

    # Sessões: em andamento, sem início, duração inválida
    inc = {}
    inc["sessions_without_start"] = int(s["startedAt"].isna().sum())
    inc["sessions_ongoing_without_end"] = int(s["endedAt"].isna().sum())
    s = s.dropna(subset=["startedAt", "endedAt"]).copy()
    s["duration_min"] = (s["endedAt"] - s["startedAt"]).dt.total_seconds() / 60
    bad = (s["duration_min"] <= 0) | (s["duration_min"] > MAX_SESSION_H * 60)
    inc["sessions_invalid_duration"] = int(bad.sum())
    s = s[~bad]

    # Participações: órfãs, sem saída, saída antes da entrada
    orphan = ~p["roomSessionId"].isin(s["roomSessionId"])
    inc["participations_orphan_or_excluded_session"] = int(orphan.sum())
    p = p[~orphan].copy()
    inc["participations_without_join"] = int(p["joinedAt"].isna().sum())
    p = p.dropna(subset=["joinedAt"])
    ends = p["roomSessionId"].map(s.set_index("roomSessionId")["endedAt"])
    no_left = p["leftAt"].isna()
    inc["participations_without_left_imputed"] = int(no_left.sum())
    p.loc[no_left, "leftAt"] = ends[no_left]          # imputação: saída = fim da sessão
    neg = p["leftAt"] < p["joinedAt"]
    inc["participations_left_before_join"] = int(neg.sum())
    p = p[~neg].copy()
    p["stay_min"] = (p["leftAt"] - p["joinedAt"]).dt.total_seconds() / 60
    q["inconsistencies"] = inc
    return s, p, q


# --------------------------------------------------------------------------- anonimização
def role_group(role: str | None) -> str:
    if role in TEACHER_ROLES:
        return "teacher"
    if role in STUDENT_ROLES:
        return "student"
    return "other"


def anonymize(s: pd.DataFrame, p: pd.DataFrame, reg: PseudonymRegistry) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    # Papel predominante de cada pessoa (pelo nome normalizado) -> Teacher/Student
    p["name_key"] = p["displayName"].map(normalize_name)
    p["role"] = p["roleName"].map(role_group)
    main_role = (
        p.dropna(subset=["name_key"])
        .groupby("name_key")["role"]
        .agg(lambda r: "teacher" if (r == "teacher").mean() >= 0.5 else "student")
    )

    def person_label(key):
        if key is None or (isinstance(key, float) and np.isnan(key)):
            return None
        return reg.label(main_role[key], key)

    p["person_id"] = p["name_key"].map(person_label)
    session_label = {sid: reg.label("session", sid) for sid in s.sort_values("startedAt")["roomSessionId"]}
    room_label = {rn: reg.label("room", rn) for rn in sorted(s["roomName"].unique())}
    reg.commit()

    # ---- sessões (públicas)
    local = s["startedAt"].dt.tz_convert(TZ)
    end_local = s["endedAt"].dt.tz_convert(TZ)
    slot_offset = local.dt.minute % 30 + local.dt.second / 60   # minutos após :00/:30
    slot_offset = np.where(slot_offset > 20, slot_offset - 30, slot_offset)  # perto do slot seguinte = adiantado
    seg = p.groupby("roomSessionId").agg(
        n_segments=("participantId", "size"),
        n_people=("person_id", "nunique"),
        n_unidentified_segments=("person_id", lambda x: x.isna().sum()),
    )
    seg_t = p[p["role"] == "teacher"].groupby("roomSessionId")["person_id"].nunique().rename("n_teachers")
    seg_s = p[p["role"] == "student"].groupby("roomSessionId")["person_id"].nunique().rename("n_students")
    sessions = pd.DataFrame(
        {
            "session_id": s["roomSessionId"].map(session_label).values,
            "room_id": s["roomName"].map(room_label).values,
            "date": local.dt.date.values,
            "year_week": local.dt.strftime("%G-W%V").values,
            "month": local.dt.strftime("%Y-%m").values,
            "weekday": local.dt.day_name().values,
            "start_hour": local.dt.hour.values,
            "end_hour": end_local.dt.hour.values,
            "slot_offset_min": np.round(slot_offset, 1),
            "duration_min": s["duration_min"].round(1).values,
            "api_unique_participants": s["totalUniqueParticipants"].values,
            "participant_minutes": s["totalParticipantMinutes"].values,
            "room_mode": s.get("roomMode", pd.Series(index=s.index, dtype=object)).values,
        }
    )
    extra = seg.join(seg_t, how="left").join(seg_s, how="left")
    extra.index = extra.index.map(session_label)
    sessions = sessions.merge(extra, left_on="session_id", right_index=True, how="left")
    for c in ("n_segments", "n_people", "n_teachers", "n_students", "n_unidentified_segments"):
        sessions[c] = sessions[c].fillna(0).astype(int)
    sessions["is_short"] = sessions["duration_min"] < SHORT_SESSION_MIN
    sessions = sessions.sort_values(["date", "start_hour", "session_id"]).reset_index(drop=True)

    # ---- participações (públicas): sem timestamps absolutos, só deslocamentos
    start_by_sid = s.set_index("roomSessionId")["startedAt"]
    p = p.sort_values(["roomSessionId", "joinedAt"])
    part = pd.DataFrame(
        {
            "session_id": p["roomSessionId"].map(session_label).values,
            "person_id": p["person_id"].values,
            "role": p["role"].values,
            "join_offset_min": ((p["joinedAt"] - p["roomSessionId"].map(start_by_sid)).dt.total_seconds() / 60)
            .round(1)
            .values,
            "stay_min": p["stay_min"].round(1).values,
        }
    )
    part["segment_n"] = part.groupby(["session_id", "person_id"], dropna=False).cumcount() + 1
    part["is_reconnection"] = part["segment_n"] > 1

    info = {
        "people": {
            "teachers": int(part.loc[part["person_id"].str.startswith("Teacher", na=False), "person_id"].nunique()),
            "students": int(part.loc[part["person_id"].str.startswith("Student", na=False), "person_id"].nunique()),
            "unidentified_segments": int(part["person_id"].isna().sum()),
        },
        "rooms": len(room_label),
    }
    return sessions, part, info


def rooms_table(sessions: pd.DataFrame) -> pd.DataFrame:
    return (
        sessions.groupby("room_id")
        .agg(
            n_sessions=("session_id", "size"),
            first_month=("month", "min"),
            last_month=("month", "max"),
            avg_duration_min=("duration_min", "mean"),
            avg_people=("n_people", "mean"),
            n_teachers=("n_teachers", "max"),
        )
        .round(1)
        .reset_index()
    )


# --------------------------------------------------------------------------- main
def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-05-17")
    ap.add_argument("--raw-db", type=Path, default=RAW_DB)
    ap.add_argument("--mapping-db", type=Path, default=MAPPING_DB)
    ap.add_argument("--out", type=Path, default=PUBLIC_DIR)
    args = ap.parse_args(argv)

    reg = PseudonymRegistry(args.mapping_db, get_secret())
    s, p = load_raw(args.raw_db, f"{args.since}T00:00:00.000Z")
    s, p, quality = clean(s, p)
    sessions, part, info = anonymize(s, p, reg)
    rooms = rooms_table(sessions)

    args.out.mkdir(parents=True, exist_ok=True)
    for name, df in (("sessions", sessions), ("participations", part), ("rooms", rooms)):
        df.to_parquet(args.out / f"{name}.parquet", index=False)
        df.to_csv(args.out / f"{name}.csv", index=False)

    quality["final"] = {"sessions": len(sessions), "participations": len(part), **info}
    quality["missing_final"] = {"sessions": missing_report(sessions), "participations": missing_report(part)}
    quality["period"] = {"since": args.since, "first_date": str(sessions["date"].min()), "last_date": str(sessions["date"].max())}
    (args.out / "quality_report.json").write_text(json.dumps(quality, indent=2, ensure_ascii=False, default=str))
    print(json.dumps(quality, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
