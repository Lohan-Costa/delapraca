from __future__ import annotations

import hmac

PORTA = 7823

ROTAS_DO_SCRIPT = "/receber/"
CABECALHO_TOKEN = "X-DeLaPraCa-Token"

ORIGEM_PAINEL = "avpi://"

ROTAS_DO_LOADER = frozenset({"/health"})

ROTA_UI = "/painel"

MAX_CORPO_BYTES = 1 << 20


class Recusa:
    HOST_NAO_LOCAL = ("host não-local", 403)
    ORIGEM_WEB = ("origem não permitida", 403)
    SEM_CREDENCIAL = ("credencial ausente", 401)
    CORPO_GRANDE = ("corpo grande demais", 413)


def host_local(host: str | None) -> bool:
    h = (host or "").strip().lower()
    nome, _, porta = h.rpartition(":")
    if not nome or not porta.isdigit():
        nome = h
    return nome in ("127.0.0.1", "localhost")


def _e_origem_propria(origem: str) -> bool:
    o = origem.strip().rstrip("/").lower()
    return o in (f"http://127.0.0.1:{PORTA}", f"http://localhost:{PORTA}")


def portao(
    host: str | None,
    origin: str | None,
    sec_fetch_site: str | None,
    corpo_len: int | None,
    rota: str,
    token: str | None = None,
    token_esperado: str | None = None,
) -> tuple[str, int] | None:
    if not host_local(host):
        return Recusa.HOST_NAO_LOCAL

    origem_e_nossa = bool(origin) and _e_origem_propria(origin)

    same_origin = (sec_fetch_site or "").strip().lower() == "same-origin"

    if origin:
        o = origin.strip().lower()
        if o.startswith(("http://", "https://")) and not origem_e_nossa:
            return Recusa.ORIGEM_WEB

    loader = bool(origin) and origin.strip().lower() == ORIGEM_PAINEL and rota in ROTAS_DO_LOADER

    carga_da_ui = rota == ROTA_UI and origin is None

    script = (origin is None and rota.startswith(ROTAS_DO_SCRIPT) and bool(token)
              and bool(token_esperado)
              and hmac.compare_digest(token.strip().encode(), token_esperado.encode()))

    if not (loader or same_origin or origem_e_nossa or carga_da_ui or script):
        return Recusa.SEM_CREDENCIAL

    if corpo_len is not None and corpo_len > MAX_CORPO_BYTES:
        return Recusa.CORPO_GRANDE

    return None
