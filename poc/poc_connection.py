"""
Prova de conceito — conexão SOMENTE LEITURA com a API REST do Whereby Embedded.

Garantias de segurança:
  * Apenas requisições GET (qualquer outro método levanta exceção).
  * Nenhum dado bruto é gravado em disco.
  * Nada pessoal é impresso: nomes, e-mails, IDs, roomName/roomUrl, externalId,
    comentários e user agents são substituídos por marcadores de tipo.
  * Respeita o rate limit (429 + Retry-After) e limita o número de páginas.

Uso:
    export WHEREBY_API_KEY=...        # ou arquivo .env (fora do Git)
    python poc/poc_connection.py
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone

import requests

try:  # python-dotenv é opcional
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BASE_URL = "https://api.whereby.dev/v1"
MAX_PAGES = int(os.getenv("POC_MAX_PAGES", "100"))   # 100 páginas x 100 itens
SAMPLE_ROOMS = int(os.getenv("POC_SAMPLE_ROOMS", "5"))
PAUSE_S = 0.7  # ~85 req/min, abaixo do limite de 100 pontos/min do plano Build

# Campos cujo VALOR nunca é exibido (apenas o tipo).
SENSITIVE = {
    "displayName", "roomName", "roomUrl", "hostRoomUrl", "viewerRoomUrl",
    "meetingId", "roomSessionId", "participantId", "userId", "deviceId",
    "externalId", "comment", "userAgent", "metadata", "email",
}


class ReadOnlySession(requests.Session):
    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        if method.upper() != "GET":
            raise RuntimeError(f"Bloqueado: a PoC é somente leitura ({method}).")
        return super().request(method, url, *args, **kwargs)


def make_session() -> ReadOnlySession:
    key = os.getenv("WHEREBY_API_KEY")
    if not key:
        sys.exit(
            "WHEREBY_API_KEY não definida. Gere uma chave em Embedded Dashboard > "
            "Configure > API e exporte-a como variável de ambiente (ver .env.example)."
        )
    s = ReadOnlySession()
    s.headers.update({"Authorization": f"Bearer {key}", "Accept": "application/json"})
    return s


def get(s: ReadOnlySession, path: str, params: dict | None = None) -> dict | list:
    for attempt in range(6):
        r = s.get(f"{BASE_URL}{path}", params=params, timeout=30)
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 2 ** attempt))
            print(f"  [429] rate limit — aguardando {wait:.0f}s", file=sys.stderr)
            time.sleep(wait)
            continue
        if r.status_code == 401:
            sys.exit("401: chave ausente, inválida ou expirada.")
        if r.status_code == 403:
            sys.exit(f"403 em {path}: a chave/plano não tem permissão para este recurso.")
        r.raise_for_status()
        time.sleep(PAUSE_S)
        return r.json()
    raise RuntimeError(f"Rate limit persistente em {path}")


def paginate(s, path, params=None, max_pages=MAX_PAGES):
    params = dict(params or {}, limit=100)
    items, pages, cursor = [], 0, None
    while pages < max_pages:
        if cursor:
            params["cursor"] = cursor
        page = get(s, path, params)
        items.extend(page.get("results", []))
        pages += 1
        cursor = page.get("cursor")
        if not cursor:
            return items, True
    return items, False  # truncado


def redact(obj):
    """Mantém a estrutura, troca valores sensíveis por marcadores de tipo."""
    if isinstance(obj, dict):
        return {
            k: (f"<{type(v).__name__} redacted>" if k in SENSITIVE and v is not None else redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact(x) for x in obj[:2]]
    return obj


def date_range(rows, field):
    vals = sorted(r[field] for r in rows if r.get(field))
    return (vals[0], vals[-1]) if vals else (None, None)


def keys_of(rows):
    c = Counter(k for r in rows for k in r)
    return {k: f"{n}/{len(rows)} preenchidos" for k, n in c.items()}


def main():
    s = make_session()
    print(f"== Whereby PoC (somente leitura) — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n")

    # 1) Salas com insights agregados
    rooms, complete = paginate(s, "/insights/rooms")
    print(f"[insights/rooms] salas: {len(rooms)}{'' if complete else ' (TRUNCADO por MAX_PAGES)'}")
    if rooms:
        print("  campos:", keys_of(rooms))
        print("  createdAt (mín, máx):", date_range(rooms, "createdAt"))
        print("  firstSessionStartedAt (mín, máx):", date_range(rooms, "firstSessionStartedAt"))
        tot_sessions = sum(r.get("totalSessions") or 0 for r in rooms)
        tot_part = sum(r.get("totalUniqueParticipants") or 0 for r in rooms)
        tot_pm = sum(r.get("totalParticipantMinutes") or 0 for r in rooms)
        with_sessions = sum(1 for r in rooms if (r.get("totalSessions") or 0) > 0)
        print(f"  salas com ≥1 sessão: {with_sessions}")
        print(f"  Σ totalSessions: {tot_sessions}")
        print(f"  Σ totalUniqueParticipants (por sala): {tot_part}")
        print(f"  Σ totalParticipantMinutes: {tot_pm}")
        print("  exemplo anonimizado:", redact(rooms[0]))

    # 2) Meetings (transient rooms ainda existentes)
    meetings, mcomplete = paginate(s, "/meetings")
    print(f"\n[meetings] reuniões retornadas: {len(meetings)}{'' if mcomplete else ' (TRUNCADO)'}")
    if meetings:
        print("  campos:", keys_of(meetings))
        print("  startDate (mín, máx):", date_range(meetings, "startDate"))
        print("  endDate (mín, máx):", date_range(meetings, "endDate"))
        print("  exemplo anonimizado:", redact(meetings[0]))

    # 3) Amostra: sessões e participantes de algumas salas
    sample = [r for r in rooms if (r.get("totalSessions") or 0) > 0][:SAMPLE_ROOMS]
    n_sess = n_part = 0
    roles, first_sess, first_part = Counter(), None, None
    left_missing = 0
    for r in sample:
        sess, _ = paginate(s, "/insights/room-sessions", {"roomName": r["roomName"]}, max_pages=3)
        n_sess += len(sess)
        first_sess = first_sess or (sess[0] if sess else None)
        for se in sess[:3]:
            parts, _ = paginate(
                s, "/insights/participants", {"roomSessionId": se["roomSessionId"]}, max_pages=3
            )
            n_part += len(parts)
            first_part = first_part or (parts[0] if parts else None)
            roles.update(p.get("roleName") for p in parts)
            left_missing += sum(1 for p in parts if not p.get("leftAt"))
    print(f"\n[amostra] {len(sample)} salas → {n_sess} sessões; {n_part} registros de participantes")
    if first_sess:
        print("  campos de sessão:", sorted(first_sess))
        print("  exemplo de sessão anonimizado:", redact(first_sess))
    if first_part:
        print("  campos de participante:", sorted(first_part))
        print("  exemplo de participante anonimizado:", redact(first_part))
        print("  roleName (contagem):", dict(roles))
        print("  participantes sem leftAt:", left_missing)

    # 4) Estimativa
    if rooms:
        avg_part = (n_part / max(1, min(n_sess, 3 * len(sample)))) if n_part else None
        print("\n[estimativa]")
        print(f"  sessões (Σ totalSessions): {tot_sessions}")
        if avg_part:
            print(f"  participantes/sessão (amostra): {avg_part:.2f}")
            print(f"  registros de participação estimados: ~{int(tot_sessions * avg_part)}")


if __name__ == "__main__":
    main()
