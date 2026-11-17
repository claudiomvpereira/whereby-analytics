"""Cliente SOMENTE LEITURA da API REST do Whereby Embedded.

- Bloqueia qualquer método diferente de GET.
- Respeita o rate limit compartilhado com a produção: pausa quando a cota
  restante (X-RateLimit-Remaining) cai abaixo de `reserve` (padrão 50%)
  e trata 429 usando Retry-After / X-RateLimit-Reset.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.whereby.dev/v1"


def _parse_reset(value: str | None) -> float | None:
    """'Tue Nov 17 2026 20:22:58 GMT+0000 (...)' -> segundos até o reset."""
    if not value:
        return None
    try:
        dt = datetime.strptime(value[:24], "%a %b %d %Y %H:%M:%S").replace(tzinfo=timezone.utc)
        return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
    except ValueError:
        return None


class WherebyClient:
    def __init__(self, api_key: str | None = None, pace_s: float = 2.5, reserve: float = 0.5):
        key = api_key or os.getenv("WHEREBY_API_KEY")
        if not key:
            raise SystemExit("WHEREBY_API_KEY não definida (veja .env.example).")
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {key}", "Accept": "application/json"})
        self.pace_s = pace_s
        self.reserve = reserve
        self.n_requests = 0

    def _throttle(self, r: requests.Response) -> None:
        try:
            limit = int(r.headers.get("X-RateLimit-Limit", 0))
            remaining = int(r.headers.get("X-RateLimit-Remaining", limit))
        except ValueError:
            limit = remaining = 0
        if limit and remaining < limit * self.reserve:
            wait = _parse_reset(r.headers.get("X-RateLimit-Reset")) or 60
            log.info("cota %s/%s abaixo da reserva — pausando %.0fs", remaining, limit, wait + 1)
            time.sleep(wait + 1)
        else:
            time.sleep(self.pace_s)

    def get(self, path: str, params: dict | None = None) -> dict:
        for attempt in range(8):
            try:
                r = self.s.get(f"{BASE_URL}{path}", params=params, timeout=60)
            except requests.RequestException as e:
                wait = min(300, 5 * 2**attempt)
                log.warning("erro de rede (%s) — nova tentativa em %ss", type(e).__name__, wait)
                time.sleep(wait)
                continue
            self.n_requests += 1
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After") or _parse_reset(r.headers.get("X-RateLimit-Reset")) or 60)
                log.warning("429 rate limit — aguardando %.0fs", wait)
                time.sleep(wait + 1)
                continue
            if r.status_code >= 500:
                wait = min(300, 5 * 2**attempt)
                log.warning("HTTP %s — nova tentativa em %ss", r.status_code, wait)
                time.sleep(wait)
                continue
            if r.status_code in (401, 403):
                raise SystemExit(f"HTTP {r.status_code} em {path}: verifique a API key/permissões.")
            r.raise_for_status()
            self._throttle(r)
            return r.json()
        raise RuntimeError(f"Falha persistente em GET {path}")

    def paginate(self, path: str, params: dict | None = None):
        """Itera sobre todos os itens de um endpoint paginado por cursor."""
        params = dict(params or {}, limit=100)
        while True:
            page = self.get(path, params)
            yield from page.get("results") or []
            cursor = page.get("cursor")
            if not cursor:
                return
            params["cursor"] = cursor
