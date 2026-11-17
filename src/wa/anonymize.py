"""Pseudonimização com HMAC-SHA256 e numeração estável.

- O identificador original (nome normalizado, roomName, roomSessionId) vira
  HMAC(PSEUDONYM_SECRET, "<tipo>:<valor>"). Sem o segredo, não há como reverter.
- Cada HMAC recebe um rótulo sequencial ("Teacher 001", "Room 007", ...) que é
  gravado numa tabela PRIVADA (data/private/pseudonyms.sqlite). A tabela guarda
  apenas o HMAC, nunca o valor original, e garante que o mesmo participante
  receba sempre o mesmo rótulo em execuções futuras.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

LABEL_FORMAT = {
    "teacher": ("Teacher", 3),
    "student": ("Student", 3),
    "room": ("Room", 3),
    "session": ("Session", 5),
}


def get_secret() -> bytes:
    secret = os.getenv("PSEUDONYM_SECRET", "")
    if len(secret) < 32:
        raise SystemExit(
            "PSEUDONYM_SECRET ausente ou curto (mínimo 32 caracteres). "
            "Defina-o como variável de ambiente; veja .env.example."
        )
    return secret.encode()


def normalize_name(name: str | None) -> str | None:
    """Normaliza o displayName para que variações triviais virem a mesma pessoa.

    'Maria  Silva', 'maria silva ', 'María Silva' -> 'maria silva'.
    Nomes vazios ou ocultados pelo Whereby retornam None (participante não atribuível).
    """
    if not name:
        return None
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"\s+", " ", s).strip().lower()
    if not s or s in {"[[redacted]]", "guest", "convidado"}:
        return None
    return s


def hmac_key(secret: bytes, kind: str, value: str) -> str:
    return hmac.new(secret, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


class PseudonymRegistry:
    """Tabela privada HMAC -> rótulo, com numeração estável e incremental."""

    def __init__(self, path: Path, secret: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.secret = secret
        self.con = sqlite3.connect(path)
        self.con.execute(
            """CREATE TABLE IF NOT EXISTS pseudonyms (
                   kind TEXT NOT NULL, hmac TEXT NOT NULL, number INTEGER NOT NULL,
                   PRIMARY KEY (kind, hmac), UNIQUE (kind, number))"""
        )
        self.cache: dict[tuple[str, str], int] = {
            (k, h): n for k, h, n in self.con.execute("SELECT kind, hmac, number FROM pseudonyms")
        }

    def number(self, kind: str, value: str) -> int:
        h = hmac_key(self.secret, kind, value)
        key = (kind, h)
        if key not in self.cache:
            nxt = 1 + max((n for (k, _), n in self.cache.items() if k == kind), default=0)
            self.con.execute("INSERT INTO pseudonyms VALUES (?, ?, ?)", (kind, h, nxt))
            self.cache[key] = nxt
        return self.cache[key]

    def label(self, kind: str, value: str, label_kind: str | None = None) -> str:
        """`kind` define o espaço de numeração; `label_kind` apenas o prefixo exibido."""
        prefix, width = LABEL_FORMAT[label_kind or kind]
        return f"{prefix} {self.number(kind, value):0{width}d}"

    def commit(self) -> None:
        self.con.commit()
