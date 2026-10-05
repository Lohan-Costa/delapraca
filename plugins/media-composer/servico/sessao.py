from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from relink import Cancelado, Progresso, Trabalho, aplicar, preparar

log = logging.getLogger("delapraca.sessao")


@dataclass
class Estado:

    fase: str = "ocioso"
    etapa: str = ""
    feito: int = 0
    total: int = 0
    detalhe: str = ""
    erro: str = ""
    resultado: dict = field(default_factory=dict)
    inicio: float = 0.0

    def to_dict(self) -> dict:
        d = {
            "fase": self.fase, "etapa": self.etapa, "feito": self.feito,
            "total": self.total, "detalhe": self.detalhe, "erro": self.erro,
            "resultado": self.resultado,
        }
        if self.inicio and self.fase in ("preparando", "aplicando"):
            d["decorrido"] = round(time.time() - self.inicio, 1)
        return d


class Sessao:

    def __init__(self, servico):
        self.servico = servico
        self.trabalho = Trabalho()
        self.estado = Estado()
        self._thread: threading.Thread | None = None
        self._progresso = Progresso()
        self._lock = threading.Lock()

    @property
    def ocupada(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def instantaneo(self) -> dict:
        d = self.estado.to_dict()
        d["ocupada"] = self.ocupada
        d["trabalho"] = self.trabalho.resumo() if self.trabalho.casamentos else {}
        d["achatar_subclipes"] = self.trabalho.achatar_subclipes
        d["achatar_group_clips"] = self.trabalho.achatar_group_clips
        return d

    def masters(self, so_revisao: bool = False) -> list[dict]:
        itens = []
        for c in self.trabalho.casamentos:
            d = c.to_dict()
            escolhido = self.trabalho.escolhas.get(c.master.mob_id)
            if escolhido:
                d["arquivo"] = escolhido
                d["metodo"] = "manual"
                d["confianca"] = 1.0
                d["precisa_de_revisao"] = False
            if so_revisao and not d["precisa_de_revisao"]:
                continue
            itens.append(d)
        itens.sort(key=lambda x: (not x["precisa_de_revisao"], -x["segmentos"]))
        return itens

    def cancelar(self) -> bool:
        if not self.ocupada:
            return False
        self._progresso.cancelar = True
        return True

    def preparar(self, aaf: str, pastas: list[str], sequence: str = "",
                 rota: str = "aaf", bin_origem: str = "",
                 so_importados: bool = False, so_offline: bool = False,
                 pasta_projeto: str = "") -> None:
        with self._lock:
            if self.ocupada:
                raise RuntimeError("já há um trabalho em andamento")
            self.trabalho = Trabalho(aaf=aaf, pastas=list(pastas), sequence=sequence,
                                     rota=rota, bin_origem=bin_origem,
                                     so_importados=so_importados, so_offline=so_offline,
                                     pasta_projeto=pasta_projeto)
            self._iniciar("preparando", self._preparar)

    def preparar_carta(self, carta, pastas: list[str], pedido: dict) -> None:
        with self._lock:
            if self.ocupada:
                raise RuntimeError("já há um trabalho em andamento")
            self.trabalho = Trabalho(rota="carta", carta=carta, pedido=dict(pedido),
                                     pastas=list(pastas), sequence=getattr(carta, "nome", "") or "")
            self._iniciar("preparando", self._preparar)

    def aplicar(self, pasta_projeto: str, nome_bin: str, pasta_trabalho: str) -> None:
        with self._lock:
            if self.ocupada:
                raise RuntimeError("já há um trabalho em andamento")
            if not self.trabalho.decididos():
                raise ValueError("nada decidido — não há o que relinkar")
            self._iniciar("aplicando", self._aplicar,
                          pasta_projeto, nome_bin, pasta_trabalho)

    def _iniciar(self, fase: str, alvo, *args) -> None:
        self.estado = Estado(fase=fase, inicio=time.time())
        self._progresso = Progresso(self._anotar)
        self._thread = threading.Thread(target=self._rodar, args=(alvo, args),
                                        name=f"delapraca-{fase}", daemon=True)
        self._thread.start()

    def _anotar(self, etapa: str, feito: int, total: int, detalhe: str) -> None:
        e = self.estado
        e.etapa, e.feito, e.total, e.detalhe = etapa, feito, total, str(detalhe)

    def _rodar(self, alvo, args) -> None:
        try:
            alvo(*args)
        except Cancelado as e:
            self.estado.fase = "ocioso"
            self.estado.etapa = "cancelado"
            log.info("%s", e)
        except Exception as e:
            self.estado.fase = "erro"
            self.estado.erro = f"{type(e).__name__}: {e}"
            log.exception("trabalho falhou")

    def _preparar(self) -> None:
        preparar(self.trabalho, self._progresso)
        self.estado.fase = "pronto"
        self.estado.etapa = ""

    def _aplicar(self, pasta_projeto: str, nome_bin: str, pasta_trabalho: str) -> None:
        with self.servico.media_composer() as mc:
            if not mc.esta_vivo():
                raise RuntimeError(
                    "o Media Composer não respondeu. Ele está aberto? "
                    "Há alguma janela dele esperando resposta?")
            Path(pasta_trabalho).mkdir(parents=True, exist_ok=True)
            if self.trabalho.rota == "avb":
                import relink_avb

                resultado = relink_avb.aplicar(
                    mc, self.trabalho, self.trabalho.bin_origem, pasta_trabalho,
                    nome_bin, self._progresso)
            else:
                resultado = aplicar(mc, self.trabalho, pasta_projeto, nome_bin,
                                    pasta_trabalho, self._progresso)

        import banco as banco_mod

        resultado["banco_aprendeu"] = banco_mod.alimentar(
            pasta_projeto, self.trabalho, resultado)
        self.estado.resultado = resultado
        self.estado.fase = "feito"
        self.estado.etapa = ""
