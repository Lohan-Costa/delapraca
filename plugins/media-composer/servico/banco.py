from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import plataforma

log = logging.getLogger("delapraca.banco")

CONFIG = ".delapraca.json"


def caminho_da_config(pasta_projeto: str) -> Path:
    return Path(pasta_projeto) / CONFIG


def ler_config(pasta_projeto: str) -> dict:
    if not pasta_projeto:
        return {}
    try:
        dados = json.loads(caminho_da_config(pasta_projeto).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def gravar_config(pasta_projeto: str, dados: dict) -> bool:
    if not pasta_projeto:
        return False
    alvo = caminho_da_config(pasta_projeto)
    tmp = alvo.with_name(f"{CONFIG}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, alvo)
        return True
    except (OSError, ValueError) as e:
        log.warning("não consegui gravar %s (%s)", alvo, e)
        return False


def _guardar_caminho(pasta_projeto: str, pasta_banco: str) -> dict:
    entrada = {"abs": str(pasta_banco)}
    try:
        p_proj, p_banco = Path(pasta_projeto).resolve(), Path(pasta_banco).resolve()
        if p_proj.anchor and p_proj.anchor == p_banco.anchor:
            entrada["rel"] = os.path.relpath(p_banco, p_proj).replace(os.sep, "/")
    except (OSError, ValueError):
        pass
    return entrada


def _resolver_caminho(pasta_projeto: str, entrada: dict) -> str | None:
    rel = (entrada or {}).get("rel")
    if rel and pasta_projeto:
        candidato = Path(pasta_projeto) / rel.replace("/", os.sep)
        if (candidato / "banco.json").is_file():
            return str(candidato)
    absoluto = (entrada or {}).get("abs")
    if absoluto and (Path(absoluto) / "banco.json").is_file():
        return absoluto
    return None


def raizes_locais(pastas: list[str] | None = None) -> dict[str, str]:
    fora: dict[str, str] = {}
    for letra, rotulo in (plataforma.volumes_montados() or {}).items():
        if rotulo:
            fora.setdefault(rotulo, f"{letra}:\\")
    for pasta in pastas or []:
        p = Path(pasta)
        if str(p).startswith("\\\\") and len(p.parts) >= 1:
            raiz = p.parts[0]
            fora.setdefault(raiz.strip("\\").replace("\\", "-"), raiz)
    return fora


def estado(pasta_projeto: str, pastas: list[str] | None = None) -> dict:
    entrada = (ler_config(pasta_projeto) or {}).get("banco") or {}
    caminho = _resolver_caminho(pasta_projeto, entrada)
    if not caminho:
        return {"conectado": False,
                "aviso": ("o banco configurado neste projeto não foi encontrado"
                          if entrada else "")}

    from media import banco_midias

    banco = banco_midias.Banco.abrir(caminho, raizes=raizes_locais(pastas))
    est = banco.estatisticas()
    est.update({"conectado": True, "nome": banco.producao or Path(caminho).name})
    return est


def criar(pasta_projeto: str, caminho_escolhido: str) -> dict:
    from media import banco_midias

    escolhido = Path(caminho_escolhido)
    nome = escolhido.stem if escolhido.suffix.lower() == ".json" else escolhido.name
    nome = _nome_limpo(nome)
    if not nome:
        raise ValueError("dê um nome ao banco")

    pasta = escolhido.parent / nome
    if (pasta / "banco.json").is_file():
        raise ValueError(f"já existe um banco chamado “{nome}” nessa pasta")

    banco = banco_midias.Banco(pasta, raizes=raizes_locais(), producao=nome)
    if not banco.consolidar():
        raise RuntimeError("não consegui criar o banco nesse lugar")
    log.info("banco criado: %s", pasta)
    return conectar(pasta_projeto, str(pasta))


def conectar(pasta_projeto: str, caminho: str) -> dict:
    p = Path(caminho)
    if p.name.lower() == "banco.json":
        p = p.parent
    if not (p / "banco.json").is_file():
        raise ValueError("essa pasta não é um banco de mídias (falta o banco.json)")

    config = ler_config(pasta_projeto)
    config["banco"] = _guardar_caminho(pasta_projeto, str(p))
    if not gravar_config(pasta_projeto, config):
        raise RuntimeError("não consegui guardar a configuração na pasta do projeto")
    return estado(pasta_projeto)


def desconectar(pasta_projeto: str) -> dict:
    config = ler_config(pasta_projeto)
    config.pop("banco", None)
    gravar_config(pasta_projeto, config)
    return estado(pasta_projeto)


def abrir(pasta_projeto: str, pastas: list[str] | None = None):
    try:
        entrada = (ler_config(pasta_projeto) or {}).get("banco") or {}
        caminho = _resolver_caminho(pasta_projeto, entrada)
        if not caminho:
            return None
        from media import banco_midias

        return banco_midias.Banco.abrir(caminho, raizes=raizes_locais(pastas))
    except Exception:
        log.warning("não consegui abrir o banco deste projeto", exc_info=True)
        return None


def consultar(pasta_projeto: str, masters: list, pastas: list[str] | None = None) -> dict:
    vazio = {"acertos": 0, "ambiguos": 0, "arquivos": {}}
    if not masters:
        return vazio
    banco = abrir(pasta_projeto, pastas)
    if banco is None:
        return vazio

    arquivos: dict[str, str] = {}
    ambiguos = 0
    for master in masters:
        try:
            situacao, linhas = banco.decidir(master)
        except Exception:
            log.debug("banco não soube decidir %r", getattr(master, "nome", ""),
                      exc_info=True)
            continue
        if situacao == "ambiguo":
            ambiguos += 1
            continue
        if situacao != "acerto":
            continue
        caminho = banco.caminho_de(linhas[0])
        if not caminho:
            continue
        master.caminho_conhecido = caminho
        arquivos[caminho] = getattr(master, "nome", "")

    log.info("banco: %d master(s) já conhecidos, %d ambíguo(s)", len(arquivos), ambiguos)
    return {"acertos": len(arquivos), "ambiguos": ambiguos, "arquivos": arquivos}


def alimentar(pasta_projeto: str, trabalho, resultado: dict | None = None) -> int:
    try:
        banco = abrir(pasta_projeto, getattr(trabalho, "pastas", None))
        if banco is None:
            return 0
        decididos = trabalho.decididos()
        if not decididos:
            return 0
        recusados = {f.get("arquivo") for f in ((resultado or {}).get("falhas") or [])}
        escolhas = getattr(trabalho, "escolhas", {}) or {}

        n = 0
        for master in getattr(trabalho, "masters", []) or []:
            caminho = decididos.get(getattr(master, "mob_id", ""))
            if not caminho or caminho in recusados:
                continue
            metodo = "manual" if master.mob_id in escolhas else "relink"
            if banco.anotar_casamento_de_master(master, caminho, metodo=metodo):
                n += 1
        gravadas = banco.gravar()
        banco.consolidar()
        log.info("banco alimentado: %d casamento(s), %d linha(s) gravadas", n, gravadas)
        return n
    except Exception:
        log.warning("não consegui alimentar o banco — o relink não é afetado",
                    exc_info=True)
        return 0


def _nome_limpo(nome: str) -> str:
    limpo = "".join(c for c in (nome or "") if c.isalnum() or c in " -_.").strip()
    return limpo[:60]
