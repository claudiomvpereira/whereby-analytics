"""Coleta incremental (somente leitura) de salas, sessões e participantes.

Os dados brutos — que contêm nomes reais — ficam APENAS em
data/private/whereby.sqlite (fora do Git). A coleta é retomável: salas já
listadas e sessões cujos participantes já foram baixados são puladas.

Uso:
    PYTHONPATH=src python -m wa.collect --since 2026-05-17
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wa.client import WherebyClient

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "private" / "whereby.sqlite"
LOG_PATH = ROOT / "data" / "private" / "collect.log"

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    room_name TEXT PRIMARY KEY,
    last_session_started_at TEXT,
    raw TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    sessions_done_since TEXT          -- checkpoint: sessões listadas a partir desta data
);
CREATE TABLE IF NOT EXISTS sessions (
    room_session_id TEXT PRIMARY KEY,
    room_name TEXT NOT NULL,
    started_at TEXT,
    ended_at TEXT,
    raw TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    participants_done INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS participants (
    room_session_id TEXT NOT NULL,
    participant_id TEXT NOT NULL,
    joined_at TEXT NOT NULL,
    raw TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (room_session_id, participant_id, joined_at)
);
CREATE TABLE IF NOT EXISTS runs (
    started_at TEXT, finished_at TEXT, since TEXT, n_requests INTEGER, status TEXT
);
"""

log = logging.getLogger("wa.collect")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def collect_rooms(cli: WherebyClient, con: sqlite3.Connection) -> int:
    n = 0
    for r in cli.paginate("/insights/rooms"):
        con.execute(
            """INSERT INTO rooms (room_name, last_session_started_at, raw, fetched_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(room_name) DO UPDATE SET
                 last_session_started_at=excluded.last_session_started_at,
                 raw=excluded.raw, fetched_at=excluded.fetched_at""",
            (r["roomName"], r.get("lastSessionStartedAt"), json.dumps(r), now()),
        )
        n += 1
    con.commit()
    return n


def collect_sessions(cli: WherebyClient, con: sqlite3.Connection, since: str) -> int:
    rooms = con.execute(
        """SELECT room_name FROM rooms
           WHERE last_session_started_at >= ?
             AND (sessions_done_since IS NULL OR sessions_done_since > ?)""",
        (since, since),
    ).fetchall()
    log.info("salas com atividade desde %s a listar: %d", since[:10], len(rooms))
    total = 0
    for i, (room_name,) in enumerate(rooms, 1):
        kept = 0
        for s in cli.paginate("/insights/room-sessions", {"roomName": room_name}):
            if (s.get("startedAt") or "") < since:
                continue
            con.execute(
                """INSERT INTO sessions (room_session_id, room_name, started_at, ended_at, raw, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(room_session_id) DO UPDATE SET
                     ended_at=excluded.ended_at, raw=excluded.raw, fetched_at=excluded.fetched_at""",
                (s["roomSessionId"], room_name, s.get("startedAt"), s.get("endedAt"), json.dumps(s), now()),
            )
            kept += 1
        con.execute("UPDATE rooms SET sessions_done_since=? WHERE room_name=?", (since, room_name))
        con.commit()
        total += kept
        log.info("salas %d/%d — +%d sessões (total %d)", i, len(rooms), kept, total)
    return total


def collect_participants(cli: WherebyClient, con: sqlite3.Connection, since: str) -> int:
    pending = con.execute(
        """SELECT room_session_id, ended_at FROM sessions
           WHERE participants_done = 0 AND started_at >= ? ORDER BY started_at""",
        (since,),
    ).fetchall()
    log.info("sessões pendentes de participantes: %d", len(pending))
    total = 0
    for i, (sid, ended_at) in enumerate(pending, 1):
        for p in cli.paginate("/insights/participants", {"roomSessionId": sid, "sortBy": "joinedAt:asc"}):
            con.execute(
                """INSERT OR REPLACE INTO participants
                   (room_session_id, participant_id, joined_at, raw, fetched_at) VALUES (?, ?, ?, ?, ?)""",
                (sid, p.get("participantId") or "", p.get("joinedAt") or "", json.dumps(p), now()),
            )
            total += 1
        # Sessão ainda em andamento (sem endedAt) será revisitada na próxima execução.
        con.execute(
            "UPDATE sessions SET participants_done=? WHERE room_session_id=?", (1 if ended_at else 0, sid)
        )
        con.commit()
        if i % 25 == 0 or i == len(pending):
            log.info("participantes: sessões %d/%d — %d registros (requisições: %d)",
                     i, len(pending), total, cli.n_requests)
    return total


def main(argv=None) -> None:
    default_since = (datetime.now(timezone.utc) - timedelta(days=183)).date().isoformat()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default=default_since, help="data inicial (UTC, AAAA-MM-DD)")
    ap.add_argument("--pace", type=float, default=2.5, help="pausa mínima entre requisições (s)")
    ap.add_argument("--reserve", type=float, default=0.5, help="fração da cota a deixar livre")
    args = ap.parse_args(argv)

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
    )
    since = f"{args.since}T00:00:00.000Z"
    cli = WherebyClient(pace_s=args.pace, reserve=args.reserve)
    con = connect()
    started = now()
    status = "error"
    try:
        log.info("== coleta desde %s ==", args.since)
        log.info("salas: %d", collect_rooms(cli, con))
        log.info("sessões novas/atualizadas: %d", collect_sessions(cli, con, since))
        log.info("participações: %d", collect_participants(cli, con, since))
        status = "ok"
    finally:
        con.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?)", (started, now(), args.since, cli.n_requests, status))
        con.commit()
        log.info("fim (%s) — %d requisições", status, cli.n_requests)


if __name__ == "__main__":
    main()
