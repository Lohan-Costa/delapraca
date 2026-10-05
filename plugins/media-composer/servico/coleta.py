from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

log = logging.getLogger("delapraca.coleta")

PASTA_NO_PROJETO = "zMagicLink"
BIN_DA_COLETA = "coleta"

CONTEUDO_DA_COPIA = 6292
ASSENTAR_S = 1.5


class ColetaFalhou(RuntimeError):
    pass


class Coleta:

    def __init__(self) -> None:
        self.sequencia: dict = {}
        self.itens: list[dict] = []
        self.ultima: tuple | None = None
        self.pendente: dict | None = None
        self._insistiu: tuple | None = None
        self._lock = threading.Lock()

    def estado(self) -> dict:
        from carta_aberta.insert import trilhas_da_selecao

        return {"sequencia": dict(self.sequencia), "itens": [dict(i) for i in self.itens],
                "trilhas": trilhas_da_selecao(self.itens),
                "pendente": dict(self.pendente) if self.pendente else None}

    def decidir(self, sequencia: dict, itens: list[dict], avisos: list[str]) -> dict:
        from carta_aberta.insert import chave, impressao_digital, trilhas_da_selecao

        with self._lock:
            if not itens:
                self.pendente = None
                texto = ("a última cópia não é desta sequência — selecione na timeline e "
                         "aperte Coletar de novo") if avisos else \
                        "nada selecionado na timeline"
                return {"status": "outra_sequencia" if avisos else "nada_novo", "texto": texto,
                        "avisos": avisos}

            impressao = impressao_digital(itens)
            ja = {chave(i) for i in self.itens} if self.sequencia.get("mob_id") == sequencia.get("mob_id") else set()
            novos = [i for i in itens if chave(i) not in ja]
            self.pendente = None
            if not novos:
                self._insistiu = None
                return {"status": "nada_novo", "avisos": avisos,
                        "texto": "esses clipes já estão na lista"}
            if impressao == self.ultima and self._insistiu != impressao:
                self._insistiu = impressao
                return {"status": "nada_novo", "avisos": avisos,
                        "texto": "nada novo selecionado. Se quer os mesmos clipes de novo, "
                                 "aperte Coletar outra vez"}
            self._insistiu = None

            troca = bool(self.itens) and self.sequencia.get("mob_id") != sequencia.get("mob_id")
            trilhas = trilhas_da_selecao(novos)
            if troca or len(trilhas) > 1:
                self.pendente = {"sequencia": dict(sequencia), "itens": novos,
                                 "impressao": impressao, "troca": troca, "trilhas": trilhas,
                                 "avisos": avisos}
                if troca:
                    texto = (f"a lista é de “{self.sequencia.get('nome') or 'outra sequência'}”. "
                             f"Trocar pela seleção de “{sequencia.get('nome') or 'esta sequência'}”?")
                else:
                    texto = (f"a seleção pega {len(trilhas)} trilhas ({', '.join(trilhas)}) — "
                             f"{len(novos)} clipe(s). É isso?")
                return {"status": "confirmar", "texto": texto, "trilhas": trilhas,
                        "itens": novos, "avisos": avisos}

            return self._somar(sequencia, novos, impressao, avisos)

    def confirmar(self, sim: bool) -> dict:
        with self._lock:
            p, self.pendente = self.pendente, None
            if not p:
                return {"status": "nada_pendente", "texto": "não há nada esperando confirmação"}
            if not sim:
                return {"status": "cancelado", "texto": "nada entrou na lista"}
            if p["troca"]:
                self.itens, self.sequencia = [], {}
            return self._somar(p["sequencia"], p["itens"], p["impressao"], p["avisos"])

    def _somar(self, sequencia: dict, novos: list[dict], impressao: tuple, avisos: list) -> dict:
        self.sequencia = dict(sequencia)
        self.itens.extend(novos)
        self.itens.sort(key=lambda i: (int(i["track"]), i["timeline_tc_in"]))
        self.ultima = impressao
        return {"status": "adicionado", "novos": len(novos), "avisos": avisos,
                "texto": f"{len(novos)} clipe(s) na lista"}

    def remover(self, chave_item: list | tuple) -> bool:
        from carta_aberta.insert import chave

        alvo = (int(chave_item[0]), str(chave_item[1]), str(chave_item[2]))
        with self._lock:
            antes = len(self.itens)
            self.itens = [i for i in self.itens if chave(i) != alvo]
            return len(self.itens) < antes

    def limpar(self) -> None:
        with self._lock:
            self.itens, self.pendente = [], None

def _comando(mc, command_id: int, limite_s: float = 15.0) -> None:
    from avid.mcapi import McApiError

    fim = time.monotonic() + limite_s
    while True:
        try:
            mc._chamar("DoCommand", commandId=command_id)
            return
        except McApiError as e:
            if "more than one command" not in str(e) or time.monotonic() > fim:
                raise
            time.sleep(0.25)


def _monitores(mc) -> dict[int, str]:
    return {m.view_type: m.mob_id for m in mc._chamar("GetViewerMobs").body.mobs}


def _bin_da_coleta(mc) -> Path:
    from avid.acoes import Acoes

    projeto = Path((mc.projeto() or {}).get("path") or "")
    if not projeto.is_dir():
        raise ColetaFalhou("não achei a pasta do projeto aberto no Media Composer")
    pasta = projeto / PASTA_NO_PROJETO
    pasta.mkdir(exist_ok=True)
    caminho = pasta / f"{BIN_DA_COLETA}.avb"
    if caminho.exists():
        _fechar(mc, caminho)
        time.sleep(0.5)
        try:
            os.remove(caminho)
        except OSError as e:
            raise ColetaFalhou(f"não consegui limpar a bin da coleta ({e})") from None
    Acoes(mc).criar_bin(BIN_DA_COLETA, subpasta=PASTA_NO_PROJETO)
    time.sleep(1.0)
    return caminho


def _fechar(mc, caminho: Path) -> None:
    from avid.mcapi import McApiError

    try:
        mc.fechar_bin(str(caminho))
    except McApiError:
        log.debug("CloseBin %s recusado (já fechada?)", caminho.name)


def _zerar_bin(mc, caminho: Path) -> None:
    from avid.acoes import Acoes

    try:
        _fechar(mc, caminho)
        time.sleep(0.5)
        if caminho.exists():
            os.remove(caminho)
        Acoes(mc).criar_bin(BIN_DA_COLETA, subpasta=PASTA_NO_PROJETO)
        time.sleep(1.0)
        _fechar(mc, caminho)
    except Exception:
        log.warning("não consegui zerar a bin da coleta", exc_info=True)


def capturar(mc) -> tuple[dict, dict, dict]:
    import relink_avb
    import teclado
    from avb_bin import segmentos as S
    from avid.acoes import Acoes
    from avid.mcapi import McApiError
    from relink_avb import esperar_bin_estabilizar

    antes = _monitores(mc)
    seq_mob = antes.get(1)
    if not seq_mob:
        raise ColetaFalhou("não há sequência na timeline — carregue a sequência no Record")
    bin_seq = mc.bin_do_mob(seq_mob)
    if not bin_seq or not Path(bin_seq).is_file():
        raise ColetaFalhou("a sequência da timeline não está numa bin salva do projeto")

    try:
        teclado.copiar_selecao()
    except teclado.TecladoIndisponivel as e:
        raise ColetaFalhou(str(e)) from None

    _comando(mc, CONTEUDO_DA_COPIA)
    time.sleep(ASSENTAR_S)
    depois = _monitores(mc)
    mudou = next((t for t in (0, 1) if depois.get(t) and depois.get(t) != antes.get(t)), None)
    if mudou is None:
        raise ColetaFalhou("não consegui abrir a seleção copiada — clique na timeline e tente "
                           "de novo")
    copia_mob = depois[mudou]

    caminho = _bin_da_coleta(mc)
    try:
        try:
            mc._chamar("CreateSubClip", destination_bin_path=str(caminho), mob_id=copia_mob,
                       use_clip_bounds=True, end_frame=-1, create_new_sequence=True)
        finally:
            if mudou == 1:
                mc.carregar_no_monitor([seq_mob], "Record")
        Acoes(mc).salvar()
        esperar_bin_estabilizar(caminho)
        from avb_export.reader import topen

        with topen(str(caminho)) as f:
            seqs = list(S.sequencias(f))
            if not seqs:
                raise ColetaFalhou("a seleção copiada veio vazia")
            alvo = next((m for m in seqs if (getattr(m, "name", "") or "").lower()
                         .startswith("clipboard contents")), None) or \
                max(seqs, key=lambda m: len(list(getattr(m, "tracks", None) or [])))
            copia = S.ler_timeline(f, alvo)
    except McApiError as e:
        raise ColetaFalhou(f"o Media Composer recusou gravar a seleção: {e}") from None
    finally:
        _zerar_bin(mc, caminho)

    relink_avb.garantir_bin_no_disco(mc, bin_seq)
    sequencia = S.ler_timeline_de_arquivo(bin_seq, mob_id=seq_mob)
    return ({"mob_id": seq_mob, "nome": sequencia.get("composition_name") or ""},
            copia, sequencia)
