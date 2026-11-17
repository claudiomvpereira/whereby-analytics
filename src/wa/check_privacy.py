"""Verificação de privacidade dos arquivos públicos (rodar antes de todo push).

Falha (exit 1) se qualquer arquivo em data/public/ contiver:
  * um valor original sensível presente no banco privado (displayName, roomName,
    roomSessionId, participantId, deviceId, userId, externalId, meetingId);
  * um nome de exibição como substring (para nomes com 4+ caracteres);
  * padrões genéricos: UUID, URL do Whereby, e-mail, token Bearer.

Uso:
    PYTHONPATH=src python -m wa.check_privacy [--public data/public]
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from wa.anonymize import normalize_name

ROOT = Path(__file__).resolve().parents[2]
SENSITIVE_KEYS = ("displayName", "roomName", "roomSessionId", "participantId", "deviceId",
                  "userId", "externalId", "meetingId", "roomUrl", "userAgent", "comment")
PATTERNS = {
    "uuid": re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I),
    "whereby_url": re.compile(r"whereby\.com/", re.I),
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "bearer": re.compile(r"bearer\s+[a-z0-9._-]{16,}", re.I),
}


# Nomes genéricos que podem coincidir com rótulos/colunas públicos sem identificar ninguém.
GENERIC = {"teacher", "student", "room", "session", "professor", "professora", "aluno", "aluna",
           "host", "guest", "visitor", "participant", "user", "teste", "test"}


def sensitive_values(db: Path) -> tuple[set[str], set[str]]:
    con = sqlite3.connect(db)
    exact, names = set(), set()
    for table in ("rooms", "sessions", "participants"):
        for (raw,) in con.execute(f"SELECT raw FROM {table}"):
            d = json.loads(raw)
            for k in SENSITIVE_KEYS:
                v = d.get(k)
                if isinstance(v, str) and v.strip():
                    exact.add(v.strip().lower())
                    if k == "displayName":
                        n = normalize_name(v)
                        if n and len(n) >= 4 and n not in GENERIC:
                            names.add(n)
    return exact, names


def public_strings(public: Path):
    for f in sorted(public.glob("*")):
        if f.suffix == ".parquet":
            df = pd.read_parquet(f)
            for c in df.columns:
                yield f.name, f"coluna:{c}", c
                if df[c].dtype == object or pd.api.types.is_string_dtype(df[c]):
                    for v in df[c].dropna().astype(str).unique():
                        yield f.name, c, v
        elif f.suffix in (".csv", ".json", ".md", ".txt"):
            yield f.name, "texto", f.read_text(errors="ignore")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--public", type=Path, default=ROOT / "data" / "public")
    ap.add_argument("--raw-db", type=Path, default=ROOT / "data" / "private" / "whereby.sqlite")
    args = ap.parse_args(argv)

    exact, names = sensitive_values(args.raw_db)
    problems = []
    for fname, where, value in public_strings(args.public):
        low = value.lower()
        if where != "texto" and low.strip() in exact:
            problems.append((fname, where, "valor original sensível"))
        for label, pat in PATTERNS.items():
            if pat.search(value):
                problems.append((fname, where, f"padrão {label}"))
        norm = normalize_name(value) or ""
        for n in names:
            if re.search(rf"\b{re.escape(n)}\b", norm):
                problems.append((fname, where, "nome de participante"))
                break
    # Nunca imprime o valor encontrado — só onde.
    if problems:
        print(f"FALHOU: {len(problems)} ocorrência(s) suspeita(s):")
        for p in sorted(set(problems))[:50]:
            print("  ", *p)
        return 1
    print(f"OK: nenhum dado identificável em {args.public} "
          f"({len(exact)} valores sensíveis e {len(names)} nomes verificados)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
