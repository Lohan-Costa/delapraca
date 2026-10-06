from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from sessao import Estado

log = logging.getLogger("delapraca.sessao_export")

FORMATOS = {"carta_aberta": ".json", "drt": ".drt"}
EM_BREVE = ("prproj", "aaf", "xml", "otio")


class Cancelado(Exception):
    pass


def formato_valido(formato: str) -> str:
    if formato in FORMATOS:
        return formato
    if formato in EM_BREVE:
        raise ValueError(f"o formato {formato.upper()} ainda não está pronto — em breve")
    raise ValueError(f"formato desconhecido: {formato!r}")


def com_extensao(caminho: str, formato: str) -> str:
    ext = FORMATOS[formato]
    p = Path(caminho)
    return str(p) if p.suffix.lower() == ext else str(p.with_name(p.name + ext))


def mob_id_da_api(urn: str) -> str:
    from aaf.masters import chave_mob

    h = chave_mob(urn)
    return f"{h[:26]}-{h[26:32]}-{h[32:48]}-{h[48:60]}-{h[60:]}"


def resolver_midia(mc, leitura: dict, anotar=lambda *a, **k: None,
                   so_masters: set[str] | None = None) -> dict:
    from avb_bin.segmentos import caminho_confiavel
    from media import pmr

    masters = leitura["masters"]
    arquivos: dict[str, str] = {}
    offline: list[dict] = []

    anotar("lendo o índice da mídia gerenciada")
    indice = pmr.indice_de(pmr.raizes_de_midia())
    gerenciados = indice.por_arquivo or {}
    na_gerenciada = {k for k, m in masters.items()
                     if any(e in gerenciados for e in (m.get("essencias") or {}).values())}

    alvos = [(k, m) for k, m in masters.items() if so_masters is None or k in so_masters]
    for i, (chave, m) in enumerate(alvos, 1):
        anotar("identificando os originais", i, len(alvos), m["nome"])
        if chave in na_gerenciada:
            m["origem_do_arquivo"] = "gerenciada"
            continue
        caminho, _motivo = caminho_confiavel(m["caminho_na_bin"])
        if caminho and os.path.isfile(caminho):
            arquivos[chave], m["origem_do_arquivo"] = caminho, "bin"
            continue
        colunas: dict = {}
        try:
            colunas = mc.colunas_do_master(mob_id_da_api(chave)) or {}
        except Exception as e:
            log.info("MC não deu as colunas de %s: %s", m["nome"], e)
        m["colunas_mc"] = {k: (colunas.get(k) or "").strip()
                           for k in ("Source File", "Source Path", "Tape")}
        pelo_mc = None
        if colunas:
            try:
                pelo_mc = mc.arquivo_do_master(mob_id_da_api(chave), colunas=colunas)
            except Exception as e:
                log.info("MC não disse o arquivo de %s: %s", m["nome"], e)
        if pelo_mc:
            arquivos[chave], m["origem_do_arquivo"] = pelo_mc, "media composer"
        else:
            m["origem_do_arquivo"] = "offline"
            ident = m.get("identidade") or {}
            offline.append({"nome": m["nome"],
                            "reel": ident.get("reel") or "",
                            "tem_tc": ident.get("tc_inicio") is not None,
                            "arquivo": m["colunas_mc"]["Source File"]})
    return {"arquivos": arquivos, "gerenciados": gerenciados, "na_gerenciada": na_gerenciada,
            "offline": offline, "indice": indice}


class SessaoExport:
    def __init__(self, servico):
        self.servico = servico
        self.estado = Estado()
        self.leitura: dict | None = None
        self.casamentos: list = []
        self.arquivos: dict[str, str] = {}
        self.gerenciados: dict[str, str] = {}
        self.arrastado: dict = {}
        self.projeto = ""
        self._thread: threading.Thread | None = None
        self._cancelar = False
        self._lock = threading.Lock()

    @property
    def ocupada(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def instantaneo(self) -> dict:
        d = self.estado.to_dict()
        d["ocupada"] = self.ocupada
        d["sequencia"] = self.arrastado
        return d

    def nome_da_sequencia(self) -> str:
        return (self.arrastado.get("nome")
                or (self.leitura or {}).get("composition_name") or "")

    def cancelar(self) -> bool:
        if not self.ocupada:
            return False
        self._cancelar = True
        return True

    def preparar(self, mob_id: str, nome: str) -> None:
        with self._lock:
            if self.ocupada:
                raise RuntimeError("já há uma exportação em andamento")
            self.leitura, self.casamentos, self.arquivos = None, [], {}
            self.arrastado = {"mob_id": mob_id, "nome": nome}
            self._iniciar("preparando", self._preparar, mob_id)

    def gerar(self, formato: str, caminho: str, nome: str, comentario: str) -> None:
        with self._lock:
            if self.ocupada:
                raise RuntimeError("já há uma exportação em andamento")
            if self.leitura is None:
                raise ValueError("arraste uma timeline antes de exportar")
            self._iniciar("gerando", self._gerar, formato, caminho, nome, comentario)

    def _iniciar(self, fase, alvo, *args) -> None:
        resumo_anterior = self.estado.resultado if fase == "gerando" else {}
        self.estado = Estado(fase=fase, inicio=time.time())
        self.estado.resultado = dict(resumo_anterior)
        self._cancelar = False
        self._thread = threading.Thread(target=self._rodar, args=(alvo, args),
                                        name=f"delapraca-export-{fase}", daemon=True)
        self._thread.start()

    def _anotar(self, etapa: str, feito: int = 0, total: int = 0, detalhe: str = "") -> None:
        if self._cancelar:
            raise Cancelado("cancelado pelo editor")
        e = self.estado
        e.etapa, e.feito, e.total, e.detalhe = etapa, feito, total, str(detalhe)

    def _rodar(self, alvo, args) -> None:
        try:
            alvo(*args)
        except Cancelado:
            self.estado.fase = "ocioso"
            self.estado.etapa = "cancelado"
        except Exception as e:
            self.estado.fase = "erro"
            self.estado.erro = str(e) if isinstance(e, (LookupError, ValueError, RuntimeError)) \
                else f"{type(e).__name__}: {e}"
            log.exception("exportação falhou")

    def _preparar(self, mob_id: str) -> None:
        import relink_avb
        from avb_bin.segmentos import casamentos, ler_timeline_de_arquivo

        with self.servico.media_composer() as mc:
            self._anotar("procurando a bin da timeline")
            if not mc.esta_vivo():
                raise RuntimeError("o Media Composer não respondeu — ele está aberto?")
            self.projeto = Path((mc.projeto() or {}).get("path") or "").name
            bin_origem = mc.bin_do_mob(mob_id)
            if not bin_origem or not Path(bin_origem).is_file():
                raise LookupError("não achei a bin desta timeline no disco — ela está numa "
                                  "bin salva do projeto aberto?")
            self._anotar("salvando a bin antes de ler", detalhe=Path(bin_origem).name)
            if not relink_avb.garantir_bin_no_disco(mc, bin_origem):
                raise LookupError("a bin está vazia no disco — salve a bin no Media Composer "
                                  "e arraste de novo")

            self._anotar("lendo a timeline", detalhe=Path(bin_origem).name)
            leitura = ler_timeline_de_arquivo(bin_origem, mob_id=mob_id)

            midia = resolver_midia(mc, leitura, self._anotar)
            arquivos, offline = midia["arquivos"], midia["offline"]
            na_gerenciada, indice = midia["na_gerenciada"], midia["indice"]
            self.gerenciados = midia["gerenciados"]
            total = len(leitura["masters"])

        self.leitura = leitura
        self.arquivos = arquivos
        self.casamentos = casamentos(leitura, arquivos)
        segs = leitura["segments"]
        eventos = [s for s in segs if not s["is_gap"] and not s["is_transition"]]
        self.estado.resultado = {
            "sequencia": leitura["composition_name"],
            "bin": Path(bin_origem).name,
            "fps": leitura["timeline_fps"],
            "tc_inicial": leitura["timeline_start_tc"],
            "trilhas_video": len({s["track"] for s in segs if not s["is_audio"]}),
            "trilhas_audio": len({s["track"] for s in segs if s["is_audio"]}),
            "eventos": len(eventos),
            "masters": total,
            "masters_na_gerenciada": len(na_gerenciada),
            "indices_lidos": indice.indices_lidos,
            "pastas_sem_indice": indice.pastas_sem_indice,
            "masters_com_arquivo": len(arquivos) + len(na_gerenciada),
            "masters_offline": offline[:30],
            "masters_offline_total": len(offline),
            "offline_sem_tc": sum(1 for o in offline if not o["tem_tc"]),
            "avisos": leitura["avisos"],
        }
        self.estado.fase = "pronto"
        self.estado.etapa = ""

    def enviar(self, pasta: str, comentario: str, nome: str | None = None,
               canal: str | None = None) -> str:
        import magic_link

        with self._lock:
            if self.ocupada:
                raise RuntimeError("já há uma exportação em andamento")
            if self.leitura is None:
                raise ValueError("arraste uma timeline antes de enviar")
            envio = magic_link.novo_envio("timeline", canal)
            nome = (nome or "").strip() or self.nome_da_sequencia()
            caminho = magic_link.caminho_do_envio(pasta, nome, envio)
            self._iniciar("gerando", self._gerar, "carta_aberta", caminho,
                          nome, comentario, envio)
            return caminho

    def _gerar(self, formato: str, caminho: str, nome: str, comentario: str,
               envio: dict | None = None) -> None:
        from avb_bin.segmentos import montar_midia

        leitura = self.leitura
        self._anotar("escrevendo o arquivo", detalhe=Path(caminho).name)

        segmentos, casados, metadados = montar_midia(
            leitura, self.arquivos, str(Path(caminho).parent),
            {k: m.get("colunas_mc") or {} for k, m in leitura["masters"].items()},
            self.gerenciados)

        if formato == "carta_aberta":
            from carta_aberta.writer import build_carta_aberta

            saida = build_carta_aberta(leitura, casados, caminho, nome, comentario,
                                       arquivos=self.arquivos, gerenciados=self.gerenciados,
                                       envio=envio)
        else:
            from drp.writer import build_drt

            if not casados:
                raise ValueError("a timeline não tem nenhum clipe de mídia — o DRT sairia vazio")
            saida = build_drt(segmentos, casados, caminho,
                              timeline_name=nome, timeline_fps=leitura["timeline_fps"],
                              offline=metadados)
            saida["clipes_offline"] = sum(1 for c in casados if c.get("offline"))
            saida = {k: v for k, v in saida.items() if k != "clips"}
            if (comentario or "").strip():
                saida["comentario_ignorado"] = True
        self.estado.resultado = {**self.estado.resultado, "arquivo": caminho,
                                 "formato": formato, "saida": saida}
        self.estado.fase = "feito"
        self.estado.etapa = ""
