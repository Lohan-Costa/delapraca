from __future__ import annotations

import json
import logging
import os
import socket
import time
from pathlib import Path, PureWindowsPath

from .matcher import _duration_ok, _normalize_name
from .safepath import CaminhoInvalido, ensure_local_file, forma_windows

OPCOES_DURACAO = {"duration_tol_secs": 0.3, "duration_tol_rel": 0.01}

log = logging.getLogger("delapraca.media.banco")

ESQUEMA = "1"

_BANCO = "banco.json"
_MAPA = "mapa.json"
_FRAGMENTOS = "fragmentos"

_MAX_LINHA = 64 * 1024


def _chave_local(caminho) -> str:
    s = str(caminho).replace("/", os.sep)
    return s.casefold() if os.name == "nt" else s


def _partes(caminho) -> tuple[str, ...]:
    s = str(caminho)
    if forma_windows(s):
        return PureWindowsPath(s).parts
    return Path(s.replace("/", os.sep)).parts


def partir(caminho: str, raizes: dict[str, str]) -> tuple[str, str]:
    partes = _partes(caminho)
    chave = tuple(_chave_local(p) for p in partes)
    melhor_nome, melhor_n = "", -1
    for nome, base in (raizes or {}).items():
        if not base:
            continue
        b_partes = _partes(base)
        if not b_partes or len(b_partes) > len(partes):
            continue
        if chave[:len(b_partes)] == tuple(_chave_local(p) for p in b_partes):
            if len(b_partes) > melhor_n:
                melhor_nome, melhor_n = nome, len(b_partes)
    if melhor_n < 0:
        return "", ""
    return melhor_nome, "/".join(partes[melhor_n:])


def juntar(raiz: str, rel: str, raizes: dict[str, str]) -> str | None:
    base = (raizes or {}).get(raiz or "")
    if not base or not rel:
        return None
    return str(Path(base) / rel.replace("/", os.sep))


def assinatura(caminho) -> tuple[int, int] | None:
    try:
        st = os.stat(caminho)
    except OSError:
        return None
    return int(st.st_size), int(st.st_mtime)


class Banco:

    def __init__(self, pasta: Path, raizes: dict[str, str] | None = None,
                 autor: str = "", producao: str = ""):
        self.pasta = Path(pasta)
        self.raizes = dict(raizes or {})
        self.autor = autor or _autor_padrao()
        self.producao = producao
        self.midias: dict[str, dict] = {}
        self.casamentos: dict[str, list[dict]] = {}
        self._novas: list[dict] = []
        self._fragmentos_lidos = 0

    @classmethod
    def abrir(cls, pasta, raizes: dict[str, str] | None = None,
              autor: str = "") -> "Banco":
        p = Path(pasta)
        meta = _ler_json(p / _BANCO) or {}
        banco = cls(p, raizes=raizes, autor=autor,
                    producao=str((meta.get("_meta") or {}).get("producao") or ""))
        for nome, dados in (meta.get("raizes") or {}).items():
            banco.raizes.setdefault(nome, str((dados or {}).get("dica") or ""))

        for linha in _ler_jsonl_do_mapa(p / _MAPA):
            banco._absorver(linha)
        frag = p / _FRAGMENTOS
        if frag.is_dir():
            for arquivo in sorted(frag.glob("*.jsonl")):
                banco._fragmentos_lidos += 1
                for linha in _ler_jsonl(arquivo):
                    banco._absorver(linha)
        log.info("banco: %d mídias, %d casamentos, %d fragmento(s) em %s",
                 len(banco.midias), len(banco.casamentos), banco._fragmentos_lidos, p)
        return banco

    def _absorver(self, linha: dict) -> None:
        if not isinstance(linha, dict) or linha.get("esquema") != ESQUEMA:
            return
        tipo = linha.get("tipo")
        if tipo == "midia":
            chave = linha.get("chave")
            if chave and _mais_nova(linha, self.midias.get(chave)):
                self.midias[chave] = linha
        elif tipo == "casamento":
            nome = linha.get("nome_norm")
            if not nome:
                return
            lista = self.casamentos.setdefault(nome, [])
            alvo = _alvo(linha)
            for i, velha in enumerate(lista):
                if _alvo(velha) != alvo:
                    continue
                if _mais_nova(linha, velha):
                    linha = dict(linha)
                    linha["vezes"] = max(int(velha.get("vezes") or 1),
                                         int(linha.get("vezes") or 1))
                    lista[i] = linha
                return
            lista.append(linha)

    def candidatos_de(self, nome: str) -> list[dict]:
        return list(self.casamentos.get(_normalize_name(nome or "")) or [])

    def decidir(self, master, opcoes: dict | None = None) -> tuple[str, list[dict]]:
        linhas = self.candidatos_de(getattr(master, "nome", "") or "")
        if not linhas:
            return "nada", []

        e_audio = getattr(master, "e_audio", None)
        if e_audio is not None:
            linhas = [x for x in linhas
                      if x.get("e_audio") is None or bool(x.get("e_audio")) == bool(e_audio)]

        aprovados, neutros = [], []
        for linha in linhas:
            veredito = self.confere_duracao(master, linha, opcoes)
            if veredito is True:
                aprovados.append(linha)
            elif veredito is None:
                neutros.append(linha)
        candidatos = aprovados or neutros
        if not candidatos:
            return "nada", []

        vivos = [x for x in candidatos if self.revalidar(x)[0] == "ok"]
        if len(vivos) == 1:
            return "acerto", vivos
        if len(vivos) > 1:
            log.info("banco: %d candidatos para %r — deixando para a varredura",
                     len(vivos), getattr(master, "nome", ""))
            return "ambiguo", vivos
        return "nada", []

    def confere_duracao(self, master, linha: dict, opcoes: dict | None = None):
        agora = _duracao_do_master(master)
        antes = linha.get("dur_s")
        if not agora or not antes:
            return None
        return _duration_ok({"media_ref": {"source_duration_secs": agora}},
                            {"duration_ms": float(antes) * 1000.0},
                            opcoes or OPCOES_DURACAO)

    def caminho_de(self, linha: dict) -> str | None:
        if not linha:
            return None
        return juntar(linha.get("raiz", ""), linha.get("rel", ""), self.raizes) \
            or (linha.get("abs") or None)

    def revalidar(self, linha: dict) -> tuple[str, str | None]:
        caminho = self.caminho_de(linha)
        if not caminho:
            return "sumiu", None
        try:
            p = ensure_local_file(caminho)
        except CaminhoInvalido as e:
            log.warning("caminho recusado (%s): %r", e, caminho)
            return "sumiu", None
        atual = assinatura(p)
        if atual is None:
            return "sumiu", None
        if atual == (int(linha.get("size") or -1), int(linha.get("mtime") or -1)):
            return "ok", str(p)
        return "mudou", str(p)

    def midia_de(self, chave: str) -> dict | None:
        return self.midias.get(chave)

    def anotar_midia(self, caminho: str, meta: dict) -> dict | None:
        sig = assinatura(caminho)
        if sig is None:
            return None
        raiz, rel = partir(caminho, self.raizes)
        linha = {
            "esquema": ESQUEMA, "tipo": "midia",
            "chave": _chave_de_midia(raiz, rel, caminho),
            "raiz": raiz, "rel": rel, "abs": str(caminho),
            "size": sig[0], "mtime": sig[1],
            "meta": dict(meta or {}),
            "carimbo": _agora(), "por": self.autor,
        }
        self._novas.append(linha)
        self._absorver(linha)
        return linha

    def anotar_casamento(self, nome: str, caminho: str, *, metodo: str = "",
                         mob_id: str = "", umid: str = "",
                         dur_s: float | None = None,
                         e_audio: bool | None = None) -> dict | None:
        nome_norm = _normalize_name(nome or "")
        if not nome_norm or not caminho:
            return None
        raiz, rel = partir(caminho, self.raizes)
        sig = assinatura(caminho) or (0, 0)
        alvo = _alvo({"raiz": raiz, "rel": rel, "abs": str(caminho)})
        vezes = 1
        for velha in self.casamentos.get(nome_norm) or []:
            if _alvo(velha) == alvo:
                vezes = int(velha.get("vezes") or 1) + 1
                break
        linha = {
            "esquema": ESQUEMA, "tipo": "casamento",
            "nome_norm": nome_norm, "nome": nome,
            "raiz": raiz, "rel": rel, "abs": str(caminho),
            "size": sig[0], "mtime": sig[1],
            "mob_id": mob_id or "", "umid": umid or "",
            "dur_s": round(float(dur_s), 3) if dur_s else None,
            "e_audio": bool(e_audio) if e_audio is not None else None,
            "metodo": metodo or "", "vezes": vezes,
            "carimbo": _agora(), "por": self.autor,
        }
        self._novas.append(linha)
        self._absorver(linha)
        return linha

    def anotar_casamento_de_master(self, master, caminho: str, *,
                                   metodo: str = "") -> dict | None:
        return self.anotar_casamento(
            getattr(master, "nome", "") or "", caminho, metodo=metodo,
            mob_id=getattr(master, "mob_id", "") or "",
            dur_s=_duracao_do_master(master),
            e_audio=getattr(master, "e_audio", None))

    def gravar(self) -> int:
        if not self._novas:
            return 0
        try:
            frag = self.pasta / _FRAGMENTOS
            frag.mkdir(parents=True, exist_ok=True)
            alvo = frag / f"{_nome_de_arquivo(self.autor)}.jsonl"
            with open(alvo, "a", encoding="utf-8", newline="\n") as fh:
                for linha in self._novas:
                    fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
        except (OSError, ValueError) as e:
            log.warning("não consegui gravar no banco de mídias (%s) — seguindo", e)
            return 0
        n, self._novas = len(self._novas), []
        return n

    def consolidar(self) -> bool:
        try:
            self.pasta.mkdir(parents=True, exist_ok=True)
            alvo = self.pasta / _MAPA
            tmp = self.pasta / f"{_MAPA}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                for linha in list(self.midias.values()) + list(self.casamentos.values()):
                    fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
            os.replace(tmp, alvo)
            self._escrever_meta()
            return True
        except (OSError, ValueError) as e:
            log.warning("não consegui consolidar o banco (%s) — os fragmentos ficam", e)
            return False

    def _escrever_meta(self) -> None:
        meta = {
            "_meta": {
                "formato": "delapraca-banco-de-midias",
                "esquema": ESQUEMA,
                "producao": self.producao,
                "atualizado": _agora(),
                "nota": "Mapa de mídias compartilhado. Os fragmentos são a verdade; "
                        "mapa.json é o consolidado de leitura.",
            },
            "raizes": {nome: {"dica": base} for nome, base in self.raizes.items() if base},
        }
        tmp = self.pasta / f"{_BANCO}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.pasta / _BANCO)

    def estatisticas(self) -> dict:
        return {
            "pasta": str(self.pasta),
            "producao": self.producao,
            "midias": len(self.midias),
            "casamentos": len(self.casamentos),
            "fragmentos": self._fragmentos_lidos,
            "pendentes": len(self._novas),
            "raizes": dict(self.raizes),
        }


def _chave_de_midia(raiz: str, rel: str, caminho: str) -> str:
    return f"{raiz}/{rel}" if raiz and rel else _chave_local(caminho)


def _alvo(linha: dict) -> str:
    raiz, rel = linha.get("raiz") or "", linha.get("rel") or ""
    return f"{raiz}/{rel}" if raiz and rel else _chave_local(linha.get("abs") or "")


def _duracao_do_master(master) -> float | None:
    frames = getattr(master, "duracao_frames", None)
    taxa = getattr(master, "edit_rate", None)
    if frames and taxa:
        return frames / taxa
    return None


def _mais_nova(nova: dict, velha: dict | None) -> bool:
    if velha is None:
        return True
    return str(nova.get("carimbo") or "") >= str(velha.get("carimbo") or "")


def _agora() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _autor_padrao() -> str:
    maquina = ""
    try:
        maquina = socket.gethostname()
    except OSError:
        pass
    usuario = os.environ.get("USERNAME") or os.environ.get("USER") or "editor"
    return f"{maquina or 'maquina'}/{usuario}"


def _nome_de_arquivo(texto: str) -> str:
    limpo = "".join(c if (c.isalnum() or c in "-_.") else "-" for c in texto or "")
    return limpo.strip("-")[:80] or "maquina"


def _ler_json(caminho: Path) -> dict | None:
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dados if isinstance(dados, dict) else None


def _ler_jsonl(caminho: Path):
    try:
        with open(caminho, encoding="utf-8") as fh:
            for n, linha in enumerate(fh, 1):
                if not linha.strip() or len(linha) > _MAX_LINHA:
                    continue
                try:
                    yield json.loads(linha)
                except ValueError:
                    log.debug("linha %d ilegível em %s — pulando", n, caminho.name)
    except OSError as e:
        log.warning("não consegui ler %s (%s)", caminho, e)


def _ler_jsonl_do_mapa(caminho: Path):
    if caminho.is_file():
        yield from _ler_jsonl(caminho)
