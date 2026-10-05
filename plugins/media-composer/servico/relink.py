from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from aaf.masters import chave_mob, masters_em_uso
from aaf.remap import remapear_masters
from avid.acoes import Acoes
from media.indexer import index_folder
from media.matcher_masters import casar_masters, resumo

log = logging.getLogger("delapraca.relink")


class Cancelado(Exception):
    pass


@dataclass
class Progresso:

    callback: object = None
    cancelar: bool = False

    def __call__(self, etapa: str, feito: int, total: int, detalhe: str = "") -> None:
        if self.cancelar:
            raise Cancelado(f"cancelado durante {etapa}")
        if self.callback:
            try:
                self.callback(etapa, feito, total, detalhe)
            except Exception:
                log.debug("callback de progresso falhou", exc_info=True)


@dataclass
class Trabalho:

    aaf: str = ""
    sequence: str = ""
    rota: str = "aaf"
    bin_origem: str = ""
    carta: object = None
    pedido: dict = field(default_factory=dict)
    pastas: list[str] = field(default_factory=list)
    masters: list = field(default_factory=list)
    casamentos: list = field(default_factory=list)
    pasta_projeto: str = ""
    do_banco: dict = field(default_factory=dict)
    achatar_subclipes: bool = True
    achatar_group_clips: bool = True
    so_importados: bool = False
    so_offline: bool = False
    escolhas: dict[str, str] = field(default_factory=dict)
    filtrados: dict = field(default_factory=dict)

    def decididos(self) -> dict[str, str]:
        final = {c.master.mob_id: c.arquivo for c in self.casamentos if c.resolvido}
        final.update(self.escolhas)
        return {k: v for k, v in final.items() if v}

    def resumo(self) -> dict:
        r = resumo(self.casamentos) if self.casamentos else {}
        r["decididos"] = len(self.decididos())
        r["sequence"] = self.sequence
        r["aaf"] = self.aaf
        r["rota"] = self.rota
        r["bin_origem"] = self.bin_origem
        r["filtrados"] = self.filtrados
        r["do_banco"] = self.do_banco.get("acertos", 0)
        r["ambiguos_no_banco"] = self.do_banco.get("ambiguos", 0)
        return r


def traduzir_progresso_do_indice(info: dict) -> tuple[str, int, int, str]:
    if info.get("phase") == "scan":
        achados, vistos = info.get("found", 0), info.get("scanned", 0)
        return ("varrendo as pastas", 0, 0, f"{achados} mídia(s) em {vistos} arquivos")
    atual = info.get("current") or ""
    return ("lendo os metadados", info.get("i", 0), info.get("total", 0) or 0,
            atual.replace("\\", "/").rsplit("/", 1)[-1] if atual else "")



def exportar_aaf(mc, mob_id_sequence: str, pasta_saida: str, nome: str,
                 preset: str = "AAF") -> str:
    Path(pasta_saida).mkdir(parents=True, exist_ok=True)
    saida = Acoes(mc).export_file(mob_id_sequence, pasta_saida, nome, preset=preset)

    if not saida:
        raise RuntimeError(
            f"o preset “{preset}” não produziu arquivo nenhum. "
            f"Escolha outro em opções avançadas, ou ajuste esse preset no Media Composer "
            f"(Export Settings) para exportar AAF."
        )
    if Path(saida).suffix.lower() != ".aaf":
        raise RuntimeError(
            f"o preset “{preset}” exportou {Path(saida).name} — não é AAF. "
            f"Presets são por-projeto e o nome não garante o formato: escolha outro em "
            f"opções avançadas, ou ajuste esse no Media Composer."
        )
    return saida


def filtrar_masters(masters: list, so_importados: bool, so_offline: bool) -> tuple[list, dict]:
    if not (so_importados or so_offline):
        return masters, {"fora_por_ama": 0, "fora_por_online": 0}

    mantidos, fora_ama, fora_online = [], 0, 0
    for m in masters:
        if so_importados and getattr(m, "e_ama", None) is True:
            fora_ama += 1
            continue
        if so_offline and getattr(m, "esta_offline", None) is False:
            fora_online += 1
            continue
        mantidos.append(m)
    contagem = {"fora_por_ama": fora_ama, "fora_por_online": fora_online}
    log.info("filtros: %d de %d masters mantidos %s",
             len(mantidos), len(masters), contagem)
    return mantidos, contagem


def preparar(trabalho: Trabalho, progresso: Progresso | None = None) -> Trabalho:
    p = progresso or Progresso()

    origem = trabalho.bin_origem if trabalho.rota == "avb" else trabalho.aaf
    p("lendo a timeline", 0, 3, Path(origem).name if origem else "")
    t0 = time.time()
    if trabalho.rota == "carta":
        from sessao_receber import masters_da_carta

        trabalho.masters = masters_da_carta(trabalho.carta)
    elif trabalho.rota == "avb":
        from avb_bin.masters import masters_de_arquivo

        trabalho.masters = masters_de_arquivo(trabalho.bin_origem)
    else:
        trabalho.masters = masters_em_uso(trabalho.aaf)
    log.info("%d masters em %.1fs", len(trabalho.masters), time.time() - t0)

    trabalho.masters, trabalho.filtrados = filtrar_masters(
        trabalho.masters, trabalho.so_importados, trabalho.so_offline)
    if not trabalho.masters:
        raise RuntimeError(
            "nenhum master sobrou depois dos filtros — desmarque “só importados” ou "
            "“só offline” para relinkar a timeline inteira.")

    import banco as banco_mod

    do_banco = banco_mod.consultar(trabalho.pasta_projeto, trabalho.masters,
                                   trabalho.pastas)
    trabalho.do_banco = do_banco
    conhecidos = dict(do_banco["arquivos"])
    for m in trabalho.masters:
        c = getattr(m, "caminho_conhecido", None)
        if c:
            conhecidos.setdefault(c, m.nome)
    if conhecidos:
        p("consultando o banco", 1, 3, f"{len(conhecidos)} já conhecido(s)")

    faltam = [m for m in trabalho.masters if not getattr(m, "caminho_conhecido", None)]

    if faltam and trabalho.pastas:
        p("varrendo as pastas", 1, 3, f"{len(trabalho.pastas)} pasta(s)")
        t0 = time.time()

        def _passo(info):
            p(*traduzir_progresso_do_indice(info))

        indice = index_folder(trabalho.pastas, progress=_passo,
                              name_hints=[m.nome for m in faltam])
        log.info("índice: %d arquivos em %.1fs",
                 len(indice.get("files") or []), time.time() - t0)
    elif not faltam:
        log.info("banco cobriu os %d masters — varredura dispensada",
                 len(trabalho.masters))
        indice = {"files": [], "total_files": 0}
    else:
        log.info("%d master(s) fora do banco e nenhuma pasta apontada — ficam sem mídia",
                 len(faltam))
        indice = {"files": [], "total_files": 0}

    ja = {a.get("path") for a in indice["files"]}
    for caminho, nome in conhecidos.items():
        if caminho not in ja:
            indice["files"].append({"path": caminho, "filename": Path(caminho).name,
                                    "extension": Path(caminho).suffix.lower()})
    indice["total_files"] = len(indice["files"])

    p("casando", 2, 3, f"{len(trabalho.masters)} masters")
    trabalho.casamentos = casar_masters(
        trabalho.masters, indice,
        progresso=lambda f, t, n: p("casando", f, t, n))

    p("pronto", 3, 3, "")
    return trabalho


def aplicar(mc, trabalho: Trabalho, pasta_projeto: str, nome_bin: str,
            pasta_trabalho: str, progresso: Progresso | None = None) -> dict:
    p = progresso or Progresso()
    acoes = Acoes(mc)
    decididos = trabalho.decididos()
    if not decididos:
        raise ValueError("nada decidido — não há o que relinkar")

    por_mob = {m.mob_id: m for m in trabalho.masters}
    bin_rel = f"{nome_bin}.avb"

    p("criando a bin", 0, 1, nome_bin)
    acoes.criar_bin(nome_bin)

    mapa: dict[str, str] = {}
    renomear: dict[str, str] = {}
    falhas: list[dict] = []
    total = len(decididos)
    t0 = time.time()

    for i, (mob_id, caminho) in enumerate(sorted(decididos.items())):
        p("trazendo a mídia", i, total, Path(caminho).name)
        try:
            novo = acoes.link_file(caminho, bin_rel)
        except Exception as e:
            falhas.append({"arquivo": caminho, "erro": str(e)})
            log.warning("LinkFile falhou em %s: %s", caminho, e)
            continue
        if not novo:
            falhas.append({"arquivo": caminho, "erro": "LinkFile não devolveu mob_id"})
            continue
        mapa[mob_id] = novo
        renomear[mob_id] = Path(caminho).stem

    log.info("LinkFile: %d/%d em %.1fs (%.0f ms cada)",
             len(mapa), total, time.time() - t0,
             (time.time() - t0) * 1000 / max(total, 1))

    if not mapa:
        raise RuntimeError("nenhum arquivo pôde ser trazido para o Media Composer")

    p("salvando", total, total, "")
    acoes.salvar()

    p("reescrevendo o AAF", 0, 1, "")
    saida = str(Path(pasta_trabalho) / f"{nome_bin}.aaf")
    r = remapear_masters(
        trabalho.aaf, saida, mapa, renomear=renomear,
        achatar_subclipes=trabalho.achatar_subclipes,
        achatar_group_clips=trabalho.achatar_group_clips,
    )

    p("importando no Media Composer", 0, 1, nome_bin)
    acoes.import_file(saida, bin_rel)

    resultado = {
        "bin": nome_bin,
        "aaf": saida,
        "masters_online": len(mapa),
        "masters_pedidos": total,
        "falhas": falhas,
        "subclipes_achatados": r["subclipes_achatados"],
        "group_clips_achatados": r["group_clips_achatados"],
        "orfaos_removidos": r["orfaos_removidos"],
        "masters_remapeados": r["masters_trocados"],
        "referencias_trocadas": r["referencias_trocadas"],
        "segmentos_cobertos": sum(por_mob[m].segmentos for m in mapa if m in por_mob),
    }
    p("pronto", 1, 1, "")
    log.info("relink aplicado: %s", resultado)
    return resultado
