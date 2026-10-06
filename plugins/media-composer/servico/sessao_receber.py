from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path

import canais
import plataforma

log = logging.getLogger("delapraca.receber")

SUFIXO = ".carta.json"
REGISTRO = "recebidos.jsonl"
MAX_ARQUIVOS = 500
MAX_AVULSOS = 20
RE_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ROTULO_INSERT = {s["insert"]: s["rotulo"].upper() for s in canais.SETORES.values()}
ROTULO_INSERT["insert"] = "INSERT"
FAMILIA = {"carta": "avid", "avb": "avid", "aaf": "aaf"}
ROTULO_FAMILIA = {"avid": "uma Carta ou bin do Avid", "aaf": "um AAF"}
EXTENSOES_RECEBER = ("json", "avb", "aaf", "xml", "fcpxml", "otio", "drt", "edl", "prproj")


def _dados() -> Path:
    p = plataforma.pasta_dados()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _recebidos() -> Path:
    import cache

    return cache.sub(cache.RECEBIDOS)


def _abrir_espaco(drt: Path) -> None:
    import cache
    from conferencia import conferencias

    cache.talvez_aplicar_teto(protegidos=[drt, *conferencias().em_uso()])


class Cancelado(Exception):
    pass


ETAPAS = ("lendo", "indexando", "casando", "midia", "montando")


class SessaoReceber:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[tuple, dict]] = {}
        self._por_id: dict[str, str] = {}
        self._tipo_por_id: dict[str, str] = {}
        self._copias: dict[str, list[str]] = {}
        self._ultima_procura: dict = {}
        self._avulsos: dict[str, tuple[str, str]] = {}
        self._analise: tuple | None = None
        self._lock = threading.Lock()
        self._progresso: dict = {"ativo": False}
        self._cancelar = threading.Event()

    def progresso(self) -> dict:
        return dict(self._progresso)

    def cancelar(self) -> bool:
        if not self._progresso.get("ativo"):
            return False
        self._cancelar.set()
        return True

    def _etapa(self, etapa: str, feitos: int = 0, total: int = 0, atual: str = "",
               detalhe: str = "", **extra) -> None:
        if self._cancelar.is_set():
            raise Cancelado("cancelado")
        self._progresso = {"ativo": True, "etapa": etapa, "indice": ETAPAS.index(etapa) + 1,
                           "etapas": len(ETAPAS) + 1, "feitos": feitos, "total": total,
                           "atual": os.path.basename(atual) if atual else "",
                           "pasta": os.path.dirname(atual) if atual else "", "detalhe": detalhe,
                           **extra}

    def trazidos(self) -> dict[str, str]:
        fora: dict[str, str] = {}
        try:
            for linha in (_dados() / REGISTRO).read_text(encoding="utf-8").splitlines():
                try:
                    r = json.loads(linha)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("ok") and isinstance(r.get("id"), str):
                    fora[r["id"]] = str(r.get("em") or "")
        except OSError:
            pass
        return fora

    def trazido(self, id_envio: str, ok: bool, detalhe: str = "",
                timeline: dict | None = None, substitui: str = "") -> dict:
        if not RE_ID.match(id_envio or ""):
            raise ValueError("envio inválido")
        linha = json.dumps({"id": id_envio, "ok": bool(ok), "detalhe": str(detalhe)[:300],
                            "em": datetime.now().astimezone().isoformat(timespec="seconds")},
                           ensure_ascii=False)
        with self._lock, open(_dados() / REGISTRO, "a", encoding="utf-8") as fh:
            fh.write(linha + "\n")
        caminho = self._por_id.get(id_envio)
        if not caminho:
            self.caixa()
            caminho = self._por_id.get(id_envio)
        tipo = self._tipo_por_id.get(id_envio, "timeline")
        partida = None
        if ok and caminho and tipo == "timeline":
            try:
                import partidas
                resumo = (self._cache.get(caminho) or (None, {}))[1] or {}
                partida = partidas.guardar(id_envio, caminho, resumo, timeline)
                if substitui and substitui != id_envio:
                    partidas.aposentar(substitui)
            except (OSError, ValueError) as e:
                log.warning("não consegui guardar a partida de %s: %s", id_envio, e)
        if not ok or not caminho or not canais.e_pendente(caminho):
            return {"arquivado": None, "partida": partida}
        try:
            novo = canais.arquivar(caminho, tipo)
        except OSError as e:
            log.warning("não consegui arquivar %s: %s", caminho, e)
            return {"arquivado": None,
                    "aviso": "a timeline entrou, mas não consegui mover a Carta para "
                             f"'importado' ({e.strerror or e}) — ela fica na fila"}
        self._por_id[id_envio] = novo
        if caminho in self._cache:
            self._cache[novo] = self._cache[caminho]
        for copia in self._copias.pop(id_envio, []):
            try:
                canais.arquivar(copia, tipo)
            except OSError as e:
                log.warning("não consegui arquivar a cópia %s: %s", copia, e)
        return {"arquivado": novo, "partida": partida}

    def partidas_para(self, id_envio: str = "", timeline: dict | None = None) -> dict:
        import partidas
        t = timeline if isinstance(timeline, dict) else {}
        dela = partidas.da_timeline(str(t.get("uid") or ""), str(t.get("nome") or "")) if t else []
        resto: list[dict] = []
        if id_envio:
            caminho = self._caminho(id_envio)
            resumo = (self._cache.get(caminho) or (None, {}))[1] or {}
            resto = partidas.sugerir({**resumo, "id": id_envio})
        elif not dela:
            resto = [{**x, "motivo": "recente"} for x in partidas.listar()]
        vistos = {x["id"] for x in dela}
        return {"partidas": dela + [x for x in resto if x["id"] not in vistos]}

    def carta_da_versao(self, versao) -> tuple:
        import partidas
        from carta_aberta.reader import ler_carta

        v = versao if isinstance(versao, dict) else {}
        if v.get("partida"):
            pid = str(v["partida"])
            return ler_carta(partidas.carta(pid)), partidas.familia(pid)
        if v.get("avulso"):
            chave = str(v["avulso"])
            if not RE_ID.match(chave):
                raise ValueError("arquivo inválido")
            with self._lock:
                guardado = self._avulsos.get(chave)
            if guardado is None:
                raise LookupError("escolha o arquivo de novo (o serviço foi reiniciado ou abriu muitos)")
            tipo, caminho = guardado
            carta = _carta_avulsa(tipo, caminho, str(v.get("mob_id") or ""))
            return carta, FAMILIA[tipo]
        if v.get("envio"):
            return ler_carta(self._caminho(str(v["envio"]))), "avid"
        raise ValueError("escolha a versão antiga e a nova")

    def _duas_versoes(self, antiga, nova) -> tuple:
        velha, fam_a = self.carta_da_versao(antiga)
        nova_c, fam_n = self.carta_da_versao(nova)
        if fam_a != fam_n:
            raise ValueError(f"a versão antiga é {ROTULO_FAMILIA.get(fam_a, fam_a)} e a nova é "
                             f"{ROTULO_FAMILIA.get(fam_n, fam_n)}: use o mesmo formato nas duas")
        return velha, nova_c, fam_n

    def comparar_versoes(self, antiga, nova, contar_audio: bool = False, trabalho: str = "",
                         contagem_api: dict | None = None, inicio: int | None = None) -> dict:
        import atualizacao
        import atualizar
        import partidas

        velha, nova_c, _familia = self._duas_versoes(antiga, nova)
        devolvido = atualizacao.devolvido_por({**velha.arquivos, **nova_c.arquivos})
        r = atualizar.comparar(velha.resultado, nova_c.resultado, contar_audio=contar_audio,
                               devolvido=devolvido, faixas=partidas.faixas())
        mudados = [p for p in r["planos"] if p["classe"] not in (atualizar.IGUAL, atualizar.DESLOCADO)]
        fora = {**{k: v for k, v in r.items() if k != "planos"}, "mudados": mudados[:500],
                "faixas": list(partidas.faixas()), "nome": _nome_da_timeline(nova_c, nova_c.tipo)}
        if trabalho:
            recebidos = _recebidos()
            arq = atualizacao.arquivo_de_trabalho(trabalho, recebidos)
            _ini, _itens, _no, itens_c = atualizacao.ler_trabalho(velha, arq, contagem_api or {}, inicio)
            e = atualizacao.encaixe(velha, itens_c, {**velha.arquivos, **nova_c.arquivos})
            fora["encaixe"] = {**e, "texto": "" if e["bate"] else atualizacao.texto_nao_bate(e)}
        return fora

    def preparar_versoes(self, antiga, nova, trabalho: str, contagem_api: dict,
                         sinais: dict | None = None, inicio: int | None = None) -> dict:
        import atualizacao
        import codigo_de_cores

        recebidos = _recebidos()
        recebidos.mkdir(exist_ok=True)
        trabalho = atualizacao.arquivo_de_trabalho(trabalho, recebidos)
        velha, nova_c, familia = self._duas_versoes(antiga, nova)
        plano = atualizacao.preparar(velha, nova_c, trabalho, contagem_api, recebidos,
                                     lambda carta: self._midia_da_carta(carta, recebidos),
                                     sinais=sinais, codigo=codigo_de_cores.ler(), inicio=inicio)
        fora = {**plano, "nome": _nome_da_timeline(nova_c, nova_c.tipo)}
        if not (isinstance(nova, dict) and nova.get("envio")):
            registro = Path(plano["drt"]).stem
            (recebidos / f"{registro}.nova.json").write_text(json.dumps(
                {"doc": nova_c.doc, "familia": familia,
                 "resumo": {"nome": fora["nome"],
                            "sequencia": nova_c.resultado.get("composition_name") or "",
                            "mob_id": str(nova_c.resultado.get("sequence_mob_id") or "")}},
                ensure_ascii=False), encoding="utf-8")
            fora["registro"] = registro
        return fora

    def atualizada(self, nova, timeline: dict | None, substitui: str = "", registro: str = "") -> dict:
        import partidas

        v = nova if isinstance(nova, dict) else {}
        if v.get("envio"):
            r = self.trazido(str(v["envio"]), True, "atualizar", timeline=timeline,
                             substitui=substitui)
            if r.get("partida") is None:
                raise LookupError("a timeline foi atualizada, mas a Carta nova não está mais na pasta "
                                  "do canal: esta máquina não guardou de onde ela veio")
            return r
        if not RE_ID.match(registro or ""):
            raise ValueError("registro inválido")
        guardado = _recebidos() / f"{registro}.nova.json"
        try:
            d = json.loads(guardado.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise LookupError("a Carta da versão nova não está mais nesta máquina") from None
        partida = partidas.guardar_doc(d["doc"], d.get("resumo") or {}, timeline, d.get("familia") or "avid")
        if substitui and substitui != partida["id"]:
            partidas.aposentar(substitui)
        return {"arquivado": None, "partida": partida}

    def _midia_da_carta(self, carta, pasta: Path) -> tuple[list, list]:
        from avb_bin.segmentos import caminho_confiavel, montar_midia
        from media import pmr

        def existe_aqui(c: str) -> str | None:
            seguro, _motivo = caminho_confiavel(c or "")
            return seguro if seguro and os.path.isfile(seguro) else None

        gerenciados = dict(pmr.indice_de(pmr.raizes_de_midia()).por_arquivo or {})
        for urn, mxf in carta.gerenciados.items():
            if urn not in gerenciados and (aqui := existe_aqui(mxf)):
                gerenciados[urn] = aqui
        arquivos = {k: aqui for k, c in carta.arquivos.items() if (aqui := existe_aqui(c))}
        colunas = {k: {**c, "Source Path": _pasta_de_referencia(c.get("Source Path"))}
                   for k, c in carta.colunas_por_mob.items() if isinstance(c, dict)}
        segs, casados, _metas = montar_midia(carta.resultado, arquivos, str(pasta), colunas, gerenciados)
        return segs, casados

    def _caminho(self, id_envio: str) -> str:
        if not RE_ID.match(id_envio or ""):
            raise ValueError("envio inválido")
        caminho = self._por_id.get(id_envio)
        if not caminho:
            self.caixa()
            caminho = self._por_id.get(id_envio)
        if not caminho:
            raise LookupError("esse envio não está mais na pasta do canal — procure de novo")
        return caminho

    def caixa(self, canais_cfg: dict | None = None) -> dict:
        if canais_cfg is None:
            canais_cfg = self._ultima_procura or canais.canais_da_maquina()
        self._ultima_procura = dict(canais_cfg)
        self._copias = {}
        trazidos = self.trazidos()
        canais_fora, envios, invalidas, cortado = [], [], [], False
        vistos: set[str] = set()
        for setor, s in canais.SETORES.items():
            item = {"setor": setor, "rotulo": s["rotulo"], "de": s["de"],
                    "pasta": canais_cfg.get(setor), "conectado": False}
            canais_fora.append(item)
            pasta = canais.pasta_do_canal(canais_cfg, setor)
            if not pasta:
                continue
            try:
                canais.garantir_pastas(pasta, setor)
            except OSError as e:
                item["erro"] = f"não consegui preparar a pasta do canal: {e.strerror or e}"
                continue
            item["conectado"] = True
            for para in canais.lados(setor):
                de = setor if para == canais.EDICAO else canais.EDICAO
                caixa = Path(pasta) / canais.caixa_nome(para)
                try:
                    pendentes, ruins, cortou = self._ler_pasta(caixa)
                    importados: list[tuple[dict, str]] = []
                    for sub in sorted(set(canais.SUBPASTA.values())):
                        lidos, ruins2, _c = self._ler_pasta(caixa / canais.IMPORTADO / sub)
                        importados += lidos
                        ruins += ruins2
                except RuntimeError as e:
                    item["erro"] = str(e)
                    continue
                invalidas += ruins
                cortado = cortado or cortou
                for resumo, caminho in pendentes + importados:
                    if resumo["id"] in vistos:
                        if canais.e_pendente(caminho):
                            self._copias.setdefault(resumo["id"], []).append(caminho)
                        continue
                    vistos.add(resumo["id"])
                    self._por_id[resumo["id"]] = caminho
                    self._tipo_por_id[resumo["id"]] = resumo["tipo"]
                    envios.append({**resumo, "canal": setor, "de": de, "para": para,
                                   "importado": not canais.e_pendente(caminho),
                                   "trazido_em": trazidos.get(resumo["id"])})
        envios.sort(key=lambda r: r.get("enviado_em") or "", reverse=True)
        return {"canais": canais_fora, "envios": envios, "invalidas": invalidas,
                "cortado": cortado, "lados": canais.ROTULO_LADO}

    def _ler_pasta(self, pasta: Path) -> tuple[list[tuple[dict, str]], list[dict], bool]:
        from carta_aberta.reader import LIMITE_BYTES, CartaInvalida, ler_carta

        validas, invalidas = [], []
        with self._lock:
            try:
                candidatos = []
                for p in pasta.iterdir():
                    if not p.name.endswith(SUFIXO) or p.name.startswith("._"):
                        continue
                    try:
                        if p.is_symlink() or not p.is_file():
                            continue
                        candidatos.append((p, p.stat()))
                    except OSError:
                        continue
            except FileNotFoundError:
                return [], [], False
            except OSError as e:
                raise RuntimeError(f"não consegui ler a pasta do canal: {e.strerror or e}") from None
            candidatos.sort(key=lambda x: x[1].st_mtime_ns, reverse=True)
            cortado = len(candidatos) > MAX_ARQUIVOS
            for p, st in candidatos[:MAX_ARQUIVOS]:
                assinatura = (st.st_size, st.st_mtime_ns)
                guardado = self._cache.get(str(p))
                if guardado and guardado[0] == assinatura:
                    resumo = guardado[1]
                else:
                    if st.st_size > LIMITE_BYTES:
                        resumo = {"arquivo": p.name, "erro": "carta grande demais"}
                    else:
                        try:
                            resumo = _resumir(ler_carta(p), p.name)
                        except CartaInvalida as e:
                            resumo = {"arquivo": p.name, "erro": str(e)}
                        except Exception:
                            log.warning("carta ilegível no canal: %s", p.name, exc_info=True)
                            resumo = {"arquivo": p.name, "erro": "carta ilegível"}
                    self._cache[str(p)] = (assinatura, resumo)
                if resumo.get("erro"):
                    invalidas.append(resumo)
                else:
                    validas.append((resumo, str(p)))
        return validas, invalidas, cortado

    def carta_do_pedido(self, pedido: dict):
        from carta_aberta.reader import ler_carta

        if pedido.get("avulso"):
            chave = str(pedido.get("avulso") or "")
            if not RE_ID.match(chave):
                raise ValueError("arquivo inválido")
            guardado = self._avulsos.get(chave)
            if guardado is None:
                raise LookupError("abra o arquivo de novo")
            tipo, caminho = guardado
            return _carta_avulsa(tipo, caminho, str(pedido.get("mob_id") or ""))
        id_envio = str(pedido.get("id") or "")
        if not RE_ID.match(id_envio):
            raise ValueError("envio inválido")
        caminho = self._por_id.get(id_envio)
        if not caminho:
            self.caixa()
            caminho = self._por_id.get(id_envio)
        if not caminho:
            raise LookupError("esse envio não está mais no canal — procure de novo")
        return ler_carta(caminho)

    def preparar(self, id_envio: str, originais: dict | None = None,
                 raizes: list[str] | None = None, notas: bool = False,
                 sinais: dict | None = None) -> dict:
        if not RE_ID.match(id_envio or ""):
            raise ValueError("envio inválido")
        with self._lock:
            if self._progresso.get("ativo"):
                raise RuntimeError("já estou trazendo outra timeline — espere ela terminar")
            self._cancelar.clear()
            self._progresso = {"ativo": True, "etapa": "lendo", "indice": 1,
                               "etapas": len(ETAPAS) + 1, "feitos": 0, "total": 0}
        try:
            return self._preparar(id_envio, originais, raizes, notas, sinais)
        finally:
            self._progresso = {"ativo": False}
            self._cancelar.clear()

    def _preparar(self, id_envio: str, originais: dict | None = None,
                  raizes: list[str] | None = None, notas: bool = False,
                  sinais: dict | None = None) -> dict:
        from carta_aberta.reader import ler_carta

        self._etapa("lendo")
        caminho = self._por_id.get(id_envio)
        if not caminho:
            self.caixa()
            caminho = self._por_id.get(id_envio)
        if not caminho:
            raise LookupError("esse envio não está mais no canal — procure de novo")
        carta = ler_carta(caminho)
        if (carta.envio or {}).get("id") != id_envio:
            raise LookupError("a carta mudou desde a última procura — procure de novo")
        return self._montar(carta, id_envio, originais, raizes, notas=notas, sinais=sinais)

    def abrir(self, caminho: str) -> dict:
        from carta_aberta.reader import CartaInvalida, ler_carta

        p = Path(caminho or "")
        if not p.is_absolute() or not p.is_file():
            raise ValueError("esse arquivo não existe ou não está acessível agora")
        nome = p.name.lower()
        if nome.endswith(".avb"):
            seqs = _sequencias_da_bin(p)
            if not seqs:
                raise ValueError(f"a bin {p.name} não tem nenhuma sequência")
            return {"tipo": "avb", "arquivo": p.name,
                    "chave": self._guardar_avulso("avb", p), "sequencias": seqs}
        if nome.endswith(".aaf"):
            carta = _carta_do_aaf(str(p))
            return {"tipo": "aaf", "arquivo": p.name, "chave": self._guardar_avulso("aaf", p),
                    "nome": _nome_da_timeline(carta, carta.tipo), **_leitura_rapida(carta)}
        if nome.endswith(".prproj"):
            from prproj.leitura import Leitor
            from prproj.projeto import Projeto
            try:
                seqs = Leitor(Projeto(str(p))).sequencias_de_montagem()
            except Exception as e:
                log.warning("prproj ilegível %s: %s: %s", p, type(e).__name__, e)
                raise ValueError(f"não consegui ler {p.name} como projeto do Premiere") from None
            if not seqs:
                raise ValueError(f"o projeto {p.name} não tem nenhuma sequência de montagem")
            return {"tipo": "prproj", "arquivo": p.name, "chave": self._guardar_avulso("prproj", p),
                    "sequencias": [{"mob_id": x["uid"] or x["nome"], "nome": x["nome"],
                                    "duracao": _duracao(x.get("duracao_s") or 0),
                                    "planos": x["planos"]} for x in seqs],
                    "pastas": pastas_do_projeto(str(p))}
        if nome.endswith(".json"):
            try:
                carta = ler_carta(p)
            except CartaInvalida as e:
                raise ValueError(f"{p.name}: {e}") from None
            return {"tipo": "carta", "arquivo": p.name, "chave": self._guardar_avulso("carta", p),
                    "nome": _nome_da_timeline(carta, carta.tipo), "envio": carta.tipo,
                    **_leitura_rapida(carta)}
        ext = p.suffix.lower().lstrip(".")
        if ext in EXTENSOES_RECEBER:
            raise ValueError(f"o {ext.upper()} ainda não — por enquanto abro a Carta Aberta (.json), a bin "
                             "do Avid (.avb), o AAF e o projeto do Premiere (.prproj)")
        raise ValueError("abro a Carta Aberta (.json), a bin do Avid (.avb), o AAF e o projeto do "
                         "Premiere (.prproj)")

    def _guardar_avulso(self, tipo: str, caminho: Path) -> str:
        import uuid

        chave = str(uuid.uuid4())
        with self._lock:
            self._avulsos[chave] = (tipo, str(caminho))
            while len(self._avulsos) > MAX_AVULSOS:
                self._avulsos.pop(next(iter(self._avulsos)))
        return chave

    def analisar_prproj(self, chave: str, sequencia: str, pastas: list[str]) -> dict:
        from avb_bin.segmentos import caminho_confiavel
        from tools.prproj_para_drt import analisar

        if not RE_ID.match(chave or ""):
            raise ValueError("arquivo inválido")
        with self._lock:
            guardado = self._avulsos.get(chave)
            if guardado is None or guardado[0] != "prproj":
                raise LookupError("abra o projeto de novo")
            if self._progresso.get("ativo"):
                raise RuntimeError("já estou trazendo outra timeline — espere ela terminar")
            self._cancelar.clear()
            self._progresso = {"ativo": True, "etapa": "lendo", "indice": 1,
                               "etapas": len(ETAPAS) + 1, "feitos": 0, "total": 0}
        try:
            self._etapa("lendo", sequencia=sequencia, atual=guardado[1],
                        detalhe=f"lendo a sequência “{sequencia}” do projeto do Premiere")
            try:
                a = analisar(guardado[1], sequencia, list(pastas),
                             progresso=lambda f, t, atual="": self._etapa(
                                 "casando", f, t, sequencia=sequencia, atual=atual,
                                 detalhe="procurando o original deste proxy nas pastas escolhidas"),
                             confiavel=caminho_confiavel)
            except KeyError:
                raise ValueError("essa sequência não está mais no projeto — abra-o de novo") from None
            with self._lock:
                self._analise = (chave, sequencia, tuple(pastas), a)
            presentes = set(a["casamento"])
            return {"sequencia": a["leitura"]["nome"], "pastas": list(pastas), **a["resumo"],
                    "ignorados": [x for x in ignorados_do_projeto(guardado[1]) if x in presentes]}
        finally:
            self._progresso = {"ativo": False}
            self._cancelar.clear()

    def _montar_prproj(self, chave: str, caminho: str, sequencia: str,
                       sinais: dict | None, notas: bool = False) -> dict:
        import uuid

        import codigo_de_cores
        from drp.writer import _audio_meta, _media_meta
        from tools.prproj_para_drt import faltam_sem_ignorados, montar

        with self._lock:
            a = self._analise
        if not a or a[0] != chave or a[1] != sequencia:
            raise LookupError("analise a sequência de novo antes de montar")
        _chave, _seq, pastas, analise = a
        arquivos = sorted({c["original"] for c in analise["casamento"].values()
                           if c.get("estado") == "original" and c.get("original")}
                          | {p for p in analise["casamento"] if p and os.path.isfile(p)})
        sons: list[str] = []
        if (sinais or {}).get("audio", True) is not False:
            sons = sorted({x["media_ref"]["local_path"] for x in analise["leitura"]["segmentos"]
                           if x.get("is_audio") and x.get("media_ref")
                           and x["media_ref"].get("local_path")} - set(arquivos))
        fila = [(f, _audio_meta) for f in sons if os.path.isfile(f)] + [(f, _media_meta) for f in arquivos]
        lidos = self._ler_em_paralelo(fila, sequencia)
        travar_se_o_motor_nao_le([(f, m) for f, ler, m in lidos if ler is _media_meta])
        res0 = analise["resumo"]
        self._etapa("montando", sequencia=sequencia,
                    detalhe=f"escrevendo a timeline do Resolve (DRT): {res0.get('planos', 0)} planos de "
                            f"vídeo{', o áudio' if (sinais or {}).get('audio', True) is not False else ''}"
                            f", efeitos, marcadores e títulos")
        saida = _recebidos()
        saida.mkdir(exist_ok=True)
        drt = saida / f"{uuid.uuid4()}.drt"
        _abrir_espaco(drt)
        sinais = sinais or {}
        ignorar = {x for x in (sinais.get("ignorar") or []) if x in analise["casamento"]}
        r = montar(caminho, sequencia, str(drt), list(pastas), analise=analise,
                   marcar=bool(sinais.get("marcar")), colorir=bool(sinais.get("colorir")),
                   codigo_de_cores=codigo_de_cores.ler(), audio=sinais.get("audio", True) is not False,
                   referencia=sinais.get("referencia"),
                   som_da_referencia=None if sinais.get("som_referencia") == "nenhum"
                   else (sinais.get("som_referencia") or "A"),
                   compostos=sinais.get("compound", True) is not False,
                   andamento=lambda f, t, a, det: self._etapa("montando", feitos=f, total=t, atual=a,
                                                           detalhe=det, sequencia=sequencia),
                   ignorar=ignorar, notas=notas)
        lembrar_pastas(caminho, list(pastas))
        de_outras = [x for x in ignorados_do_projeto(caminho) if x not in analise["casamento"]]
        lembrar_ignorados(caminho, de_outras + sorted(ignorar))
        res = analise["resumo"]
        self._etapa("montando", sequencia=sequencia, feitos=1, total=1)
        log.info("prproj %s › %s: %s — %d planos, %d no original, %d composições do Fusion",
                 os.path.basename(caminho), sequencia, drt, res["planos"], res["no_original"],
                 len(r["fusion"]))
        return {"drt": str(drt), "nome": analise["leitura"]["nome"], "tipo": "prproj",
                "pasta_no_resolve": "De Lá Pra Cá", "clipes": res["planos"],
                "offline": len(r.get("offline") or []), "midias": 0, "nos_originais": res["no_original"],
                "na_gerenciada": 0, "titulos": 0, "notas": (r.get("drt") or {}).get("notas") or 0,
                "marcadores": (r.get("drt") or {}).get("marcadores") or 0,
                "segmentos_coloridos": (r.get("drt") or {}).get("segmentos_coloridos") or 0,
                "faltam": faltam_sem_ignorados(analise["casamento"], ignorar),
                "ignorados": len(ignorar),
                "audio": {**(res.get("audio") or {}), **(r.get("audio") or {})},
                "referencia": (os.path.basename(sinais["referencia"]) if sinais.get("referencia")
                               else (res.get("referencia") or {}).get("arquivo")),
                "fusion": [{k: g[k] for k in ("trilha", "quadro", "arquivo", "plano")}
                           for g in r["fusion"] if g.get("plano")],
                "fusion_recusadas": [f'{g["arquivo"]} ({g.get("erro") or "não montou"})'
                                     for g in r["fusion"] if not g.get("plano")],
                "compostos": r.get("compostos") or [],
                "conferencia": _conferencia_de(r, analise["leitura"]["nome"]),
                "avisos": ((r.get("drt") or {}).get("avisos") or [])
                          + [x["texto"] for x in (r.get("avisos") or [])][:20]}

    def preparar_avulso(self, chave: str, mob_id: str = "", originais: dict | None = None,
                        raizes: list[str] | None = None, notas: bool = False,
                        sinais: dict | None = None) -> dict:
        if not RE_ID.match(chave or ""):
            raise ValueError("arquivo inválido")
        with self._lock:
            guardado = self._avulsos.get(chave)
            if guardado is None:
                raise LookupError("abra o arquivo de novo")
            if self._progresso.get("ativo"):
                raise RuntimeError("já estou trazendo outra timeline — espere ela terminar")
            self._cancelar.clear()
            self._progresso = {"ativo": True, "etapa": "lendo", "indice": 1,
                               "etapas": len(ETAPAS) + 1, "feitos": 0, "total": 0}
        try:
            self._etapa("lendo")
            tipo, caminho = guardado
            if tipo == "prproj":
                return self._montar_prproj(chave, caminho, mob_id, sinais, notas=notas)
            carta = _carta_avulsa(tipo, caminho, mob_id)
            import uuid
            return self._montar(carta, str(uuid.uuid4()), originais, raizes, notas=notas,
                                sinais=sinais)
        finally:
            self._progresso = {"ativo": False}
            self._cancelar.clear()

    def _montar(self, carta, id_saida: str, originais: dict | None = None,
                raizes: list[str] | None = None, notas: bool = False,
                sinais: dict | None = None) -> dict:
        from avb_bin.segmentos import caminho_confiavel, montar_midia
        from carta_aberta.insert import filtrar_insert
        from drp.writer import build_drt
        from media import pmr

        def existe_aqui(c: str) -> str | None:
            seguro, _motivo = caminho_confiavel(c or "")
            return seguro if seguro and os.path.isfile(seguro) else None

        self._etapa("indexando", sequencia=carta.nome or "",
                    detalhe="procurando nesta máquina a mídia do Avid (índice das pastas Avid MediaFiles)")
        locais = pmr.indice_de(pmr.raizes_de_midia()).por_arquivo or {}
        gerenciados = dict(locais)
        for urn, mxf in carta.gerenciados.items():
            if urn not in gerenciados and (aqui := existe_aqui(mxf)):
                gerenciados[urn] = aqui
        arquivos = {k: aqui for k, c in carta.arquivos.items() if (aqui := existe_aqui(c))}
        colunas = {k: {**c, "Source Path": _pasta_de_referencia(c.get("Source Path"))}
                   for k, c in carta.colunas_por_mob.items() if isinstance(c, dict)}
        preferir = originais_validos(originais, raizes)

        saida = _recebidos()
        saida.mkdir(exist_ok=True)
        drt = saida / f"{id_saida}.drt"
        _abrir_espaco(drt)
        tipo = carta.tipo
        try:
            resultado = carta.resultado
            if tipo in ROTULO_INSERT:
                resultado = filtrar_insert(resultado, carta.selecao or [])
            nome = _nome_da_timeline(carta, tipo)
            self._etapa("casando", sequencia=nome,
                        detalhe="casando cada clipe da timeline com um arquivo desta máquina")
            segs, casados, metas = montar_midia(resultado, arquivos, str(saida), colunas,
                                                gerenciados, preferir=preferir)
            if not casados:
                raise ValueError("a timeline não tem nenhum clipe de mídia — nada a trazer")
            self._ler_midia(segs, casados, nome)
            self._etapa("montando", sequencia=nome,
                        detalhe=f"escrevendo a timeline do Resolve (DRT): {len(segs)} segmentos, "
                                "efeitos, títulos e marcadores")
            sinais = sinais or {}
            lista, codigo = None, None
            if notas or sinais.get("marcar") or sinais.get("colorir"):
                import codigo_de_cores
                from notas import das_notas
                lista = das_notas(resultado)
                codigo = codigo_de_cores.ler()
            r = build_drt(segs, casados, str(drt), timeline_name=nome,
                          timeline_fps=float(resultado.get("timeline_fps") or 25.0), offline=metas,
                          notas=lista if notas else None, sinais=lista,
                          marcar=bool(sinais.get("marcar")), colorir=bool(sinais.get("colorir")),
                          codigo_de_cores=codigo,
                          andamento=lambda f, t, a, det: self._etapa("montando", feitos=f, total=t,
                                                                  atual=a, detalhe=det, sequencia=nome))
        except ValueError:
            raise
        except (TypeError, KeyError, AttributeError, IndexError, OverflowError) as e:
            log.warning("carta %s com dados inválidos: %s: %s", id_saida, type(e).__name__, e)
            raise ValueError("a carta tem dados inválidos — peça para enviar de novo") from None
        midias = _gravar_midias(drt, casados)
        offline = sum(1 for c in casados if c.get("offline"))
        nos_originais = sum(1 for c in casados if c.get("matched_path") in set(preferir.values()))
        na_gerenciada = sum(1 for c in casados if c.get("gerenciada"))
        log.info("recebido %s (%s): %s — %d clipes, %d nos originais, %d na gerenciada, "
                 "%d offline", id_saida, tipo, drt, len(casados), nos_originais, na_gerenciada,
                 offline)
        self._etapa("montando", sequencia=nome, feitos=1, total=1)
        return {"drt": str(drt), "nome": nome, "tipo": tipo,
                "pasta_no_resolve": "Inserts" if tipo in ROTULO_INSERT else "De Lá Pra Cá",
                "clipes": len(casados), "offline": offline, "midias": midias,
                "nos_originais": nos_originais, "na_gerenciada": na_gerenciada,
                "clipes_video": r.get("clips_generated"), "clipes_audio": r.get("audio_clips"),
                "titulos": r.get("titulos") or 0,
                "notas": r.get("notas") or 0,
                "marcadores": r.get("marcadores") or 0,
                "segmentos_coloridos": r.get("segmentos_coloridos") or 0,
                "avisos": ((r.get("avisos") or [])
                           + [a.get("texto") for a in (resultado.get("avisos") or [])])[:20]}


    def _ler_midia(self, segs: list, casados: list, nome: str) -> None:
        from drp.writer import _audio_meta, _media_meta

        video: set[str] = set()
        audio: set[str] = set()
        for c in casados:
            p = c.get("matched_path") if c else None
            if not p or c.get("offline") or c.get("rejected"):
                continue
            i = c.get("segment_index")
            s = segs[i] if isinstance(i, int) and 0 <= i < len(segs) else {}
            (audio if (s or {}).get("is_audio") else video).add(p)
        fila = [(p, _media_meta) for p in sorted(video)] + [(p, _audio_meta) for p in sorted(audio)]
        lidos = self._ler_em_paralelo(fila, nome)
        travar_se_o_motor_nao_le([(p, m) for p, ler, m in lidos if ler is _media_meta])

    def _ler_em_paralelo(self, fila: list, nome: str) -> list:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from drp.writer import SONDAGENS_SIMULTANEAS, _media_meta

        def detalhe(ler) -> str:
            return ("lendo o timecode, a taxa e o quadro do arquivo" if ler is _media_meta
                    else "lendo os canais e a duração do som")

        fora: list = []
        if fila:
            self._etapa("midia", feitos=0, total=len(fila), sequencia=nome, atual=fila[0][0],
                        detalhe=detalhe(fila[0][1]))
            ex = ThreadPoolExecutor(max_workers=min(SONDAGENS_SIMULTANEAS, len(fila)))
            try:
                futuros = {ex.submit(ler, p): (p, ler) for p, ler in fila}
                for fut in as_completed(futuros):
                    p, ler = futuros[fut]
                    fora.append((p, ler, fut.result()))
                    self._etapa("midia", feitos=len(fora), total=len(fila), sequencia=nome, atual=p,
                                detalhe=detalhe(ler))
            finally:
                ex.shutdown(wait=True, cancel_futures=True)
        self._etapa("midia", feitos=len(fila), total=len(fila), sequencia=nome)
        return fora


def _conferencia_de(relatorio: dict, timeline: str) -> dict | None:
    ctx = relatorio.get("conferencia")
    if not ctx or not ctx.get("pontos"):
        return None
    try:
        from conferencia import conferencias
        return conferencias().criar(ctx, timeline)
    except Exception as e:
        log.warning("conferência não preparada: %s", e)
        return None


def travar_se_o_motor_nao_le(lidos: list[tuple[str, tuple]]) -> None:
    if not lidos:
        return
    falhos = [c for c, (nb, fps, _tc) in lidos if not nb or not fps]
    if len(falhos) < 3 or len(falhos) * 2 < len(lidos):
        return
    import prontidao
    pr = prontidao.conferir(forcar=True)
    ruins = [i for i in pr["itens"] if not i["ok"]]
    if not ruins:
        log.info("%d de %d mídias sem TC/duração pelo ffprobe, com o motor pronto: formato que ele não "
                 "lê (BRAW/R3D/ARRIRAW?) — segue", len(falhos), len(lidos))
        return
    raise RuntimeError(f"O motor não conseguiu ler {len(falhos)} de {len(lidos)} mídias (TC e duração): "
                       f"{'; '.join(i['erro'] for i in ruins)}. Nada foi importado — a timeline sairia "
                       f"offline. {ruins[0].get('gesto') or ''}".rstrip())


RECEBIDOS_DIAS = 7
_DE_PASSAGEM = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
                          r"\.(drt|midias\.json|nova\.json|relatorio\.json)$")


PASTAS_POR_PROJETO = 50


def _arquivo_pastas() -> Path:
    return _dados() / "prproj_pastas.json"


def pastas_do_projeto(prproj: str) -> list[str]:
    try:
        d = json.loads(_arquivo_pastas().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    lista = d.get(os.path.normcase(os.path.abspath(prproj))) if isinstance(d, dict) else None
    return [x for x in lista if isinstance(x, str)] if isinstance(lista, list) else []


def lembrar_pastas(prproj: str, pastas: list[str]) -> None:
    try:
        try:
            d = json.loads(_arquivo_pastas().read_text(encoding="utf-8"))
            d = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            d = {}
        chave = os.path.normcase(os.path.abspath(prproj))
        d.pop(chave, None)
        d[chave] = list(pastas)
        while len(d) > PASTAS_POR_PROJETO:
            d.pop(next(iter(d)))
        _arquivo_pastas().write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log.warning("não gravei as pastas do projeto: %s", e)


def _arquivo_ignorados() -> Path:
    return _dados() / "prproj_ignorados.json"


def ignorados_do_projeto(prproj: str) -> list[str]:
    try:
        d = json.loads(_arquivo_ignorados().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    lista = d.get(os.path.normcase(os.path.abspath(prproj))) if isinstance(d, dict) else None
    return [x for x in lista if isinstance(x, str)] if isinstance(lista, list) else []


def lembrar_ignorados(prproj: str, ignorados: list[str]) -> None:
    try:
        try:
            d = json.loads(_arquivo_ignorados().read_text(encoding="utf-8"))
            d = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            d = {}
        chave = os.path.normcase(os.path.abspath(prproj))
        d.pop(chave, None)
        if ignorados:
            d[chave] = sorted(set(ignorados))
        while len(d) > PASTAS_POR_PROJETO:
            d.pop(next(iter(d)))
        _arquivo_ignorados().write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        log.warning("não gravei os ignorados do projeto: %s", e)


def _duracao(segundos: float) -> str:
    s = int(round(segundos))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def _leitura_rapida(carta) -> dict:
    from aaf.parser import frames_to_tc

    r = carta.resultado if isinstance(carta.resultado, dict) else {}
    try:
        fps = float(r.get("timeline_fps") or 0) or 25.0
    except (TypeError, ValueError):
        fps = 25.0
    if not 1.0 <= fps <= 1000.0:
        fps = 25.0
    por_trilha: dict[int, int] = {}
    planos = 0
    segmentos = r.get("segments")
    for s in segmentos if isinstance(segmentos, list) else []:
        try:
            if not isinstance(s, dict) or s.get("is_transition"):
                continue
            t = int(s.get("track") or 0)
            por_trilha[t] = por_trilha.get(t, 0) + max(0, int(s.get("duration_frames") or 0))
        except (TypeError, ValueError):
            continue
        if not s.get("is_audio") and not s.get("is_gap") and not s.get("is_effect"):
            planos += 1
    n = max(por_trilha.values(), default=0)
    return {"duracao": frames_to_tc(n, round(fps)) if n > 0 else "",
            "fps": round(fps, 3),
            "video": sum(1 for t in por_trilha if t < 1000),
            "audio": sum(1 for t in por_trilha if t >= 1000),
            "planos": planos}


def limpar_recebidos(dias: float = RECEBIDOS_DIAS, agora: float | None = None) -> int:
    import time

    pasta = _recebidos()
    if not pasta.is_dir():
        return 0
    limite = (agora if agora is not None else time.time()) - dias * 86400
    n = 0
    for p in pasta.iterdir():
        try:
            if _DE_PASSAGEM.match(p.name) and p.is_file() and p.stat().st_mtime < limite:
                p.unlink()
                n += 1
        except OSError:
            continue
    return n


def _sequencias_da_bin(caminho: Path) -> list[dict]:
    from aaf.parser import frames_to_tc
    from avb_bin.segmentos import sequencias
    from avb_export.reader import topen

    fora = []
    try:
        with topen(str(caminho)) as f:
            for m in sequencias(f):
                fps = float(getattr(m, "edit_rate", 0) or 0) or 25.0
                n = int(getattr(m, "length", 0) or 0)
                fora.append({"mob_id": str(m.mob_id), "nome": getattr(m, "name", "") or "",
                             "duracao": frames_to_tc(n, round(fps)) if n > 0 else ""})
    except Exception:
        log.warning("bin ilegível: %s", caminho, exc_info=True)
        raise ValueError(f"não consegui ler a bin {caminho.name} — ela é do Media Composer?") from None
    return sorted(fora, key=lambda s: s["nome"].casefold())


def _carta_avulsa(tipo: str, caminho: str, mob_id: str = ""):
    if tipo == "avb":
        return _carta_da_bin(caminho, mob_id)
    if tipo == "aaf":
        return _carta_do_aaf(caminho)
    return _ler_carta_avulsa(caminho)


def _carta_do_aaf(caminho: str):
    from aaf.leitura import ler_timeline_aaf
    from carta_aberta.reader import ler_carta
    from carta_aberta.writer import montar

    try:
        leitura = ler_timeline_aaf(caminho)
    except Exception:
        log.warning("AAF ilegível: %s", caminho, exc_info=True)
        raise ValueError(f"não consegui ler o AAF {Path(caminho).name} — ele é do Media Composer?") from None
    if not any(not s.get("is_gap") for s in leitura.get("segments") or []):
        raise ValueError(f"o AAF {Path(caminho).name} não tem nenhum clipe")
    arquivos = {k: m["caminho_na_bin"] for k, m in (leitura.get("masters") or {}).items()
                if m.get("caminho_na_bin")}
    doc = montar(leitura, [], leitura.get("composition_name") or "", arquivos=arquivos)
    doc["origem"]["leitura"] = "aaf (Abrir arquivo)"
    return ler_carta(doc)


def _carta_da_bin(caminho: str, mob_id: str):
    from avb_bin.segmentos import ler_timeline_de_arquivo
    from carta_aberta.reader import ler_carta
    from carta_aberta.writer import montar

    try:
        leitura = ler_timeline_de_arquivo(caminho, mob_id=mob_id or None)
    except LookupError:
        raise LookupError("essa sequência não está mais na bin — abra o arquivo de novo") from None
    except Exception:
        log.warning("bin ilegível: %s", caminho, exc_info=True)
        raise ValueError(f"não consegui ler a bin {Path(caminho).name}") from None
    arquivos = {k: m["caminho_na_bin"] for k, m in (leitura.get("masters") or {}).items()
                if m.get("caminho_na_bin")}
    doc = montar(leitura, [], leitura.get("composition_name") or "", arquivos=arquivos)
    doc["origem"]["leitura"] = "avb (Abrir arquivo)"
    return ler_carta(doc)


def _ler_carta_avulsa(caminho: str):
    from carta_aberta.reader import CartaInvalida, ler_carta

    try:
        return ler_carta(caminho)
    except CartaInvalida as e:
        raise ValueError(f"{Path(caminho).name}: {e}") from None


def _gravar_midias(drt: Path, casados: list) -> int:
    vistos: list[str] = []
    depois: list[str] = []
    for c in casados:
        p = (c or {}).get("matched_path")
        if not p or c.get("offline") or c.get("rejected"):
            continue
        p = os.path.abspath(p)
        if p in vistos or p in depois or not os.path.isfile(p):
            continue
        (depois if _canais_de_audio(p) in (1, 2) else vistos).append(p)
    lista = drt.with_suffix(".midias.json")
    lista.write_text(json.dumps({"drt": drt.name, "midias": vistos, "depois": depois},
                                ensure_ascii=False), encoding="utf-8")
    return len(vistos)


def _canais_de_audio(caminho: str) -> int | None:
    import struct

    ext = os.path.splitext(caminho)[1].lower()
    if ext not in (".wav", ".bwf", ".aif", ".aiff"):
        return None
    try:
        with open(caminho, "rb") as f:
            cab = f.read(12)
            riff = cab[:4] in (b"RIFF", b"RF64", b"BW64") and cab[8:12] == b"WAVE"
            if not riff and not (cab[:4] == b"FORM" and cab[8:12] in (b"AIFF", b"AIFC")):
                return None
            for _ in range(64):
                bloco = f.read(8)
                if len(bloco) < 8:
                    return None
                nome = bloco[:4]
                tam = struct.unpack("<I" if riff else ">I", bloco[4:])[0]
                if nome == (b"fmt " if riff else b"COMM"):
                    d = f.read(4)
                    return struct.unpack("<H", d[2:4])[0] if riff else struct.unpack(">h", d[:2])[0]
                f.seek(tam + (tam & 1), 1)
    except (OSError, struct.error):
        return None
    return None


def masters_da_carta(carta) -> list:
    from collections import Counter

    from aaf.masters import Master
    from avb_bin.segmentos import caminho_confiavel

    resultado = carta.resultado or {}
    usos: Counter = Counter()
    audio: dict[str, bool] = {}
    for s in resultado.get("segments") or []:
        chave = s.get("mob_id")
        if not chave or s.get("is_gap") or s.get("is_transition"):
            continue
        usos[chave] += 1
        audio[chave] = audio.get(chave, True) and bool(s.get("is_audio"))

    fora = []
    for chave, m in (resultado.get("masters") or {}).items():
        if not isinstance(m, dict):
            continue
        colunas = carta.colunas_por_mob.get(chave) or m.get("colunas_mc") or {}
        arquivo_original = str(colunas.get("Source File") or "").strip()
        nome = (Path(arquivo_original.replace("\\", "/")).stem if arquivo_original
                else str(m.get("nome") or ""))
        fps = m.get("fps") or (m.get("identidade") or {}).get("fps")
        master = Master(nome=nome or chave, mob_id=chave, mob_id_urn=chave,
                        e_audio=audio.get(chave, False), segmentos=usos.get(chave, 0),
                        duracao_frames=m.get("duracao_frames"),
                        edit_rate=float(fps) if fps else None)
        ama = carta.arquivos.get(chave)
        seguro, _motivo = caminho_confiavel(ama or "")
        if seguro and os.path.isfile(seguro):
            master.caminho_conhecido = seguro
        fora.append(master)
    return fora


def originais_validos(originais: dict | None, raizes: list[str] | None) -> dict[str, str]:
    if not originais or not raizes:
        return {}
    bases = [os.path.normcase(os.path.abspath(r)).rstrip("\\/") + os.sep for r in raizes if r]
    fora: dict[str, str] = {}
    for chave, caminho in originais.items():
        if not isinstance(caminho, str) or not caminho or "://" in caminho:
            continue
        absoluto = os.path.normcase(os.path.abspath(caminho))
        if any(absoluto.startswith(b) for b in bases) and os.path.isfile(caminho):
            fora[chave] = caminho
    return fora


def _pasta_de_referencia(valor) -> str:
    from avb_bin.segmentos import caminho_confiavel

    texto = valor.strip() if isinstance(valor, str) else ""
    if not texto or "://" in texto:
        return ""
    seguro, _motivo = caminho_confiavel(texto)
    return texto if seguro else ""


def _resumir(carta, arquivo: str) -> dict:
    envio = carta.envio or {}
    doc = carta.doc
    return {"id": str(envio.get("id") or ""), "tipo": carta.tipo, "arquivo": arquivo,
            "nome": carta.nome, "sequencia": carta.resultado.get("composition_name") or "",
            "mob_id": str(carta.resultado.get("sequence_mob_id") or ""),
            "comentario": carta.comentario,
            "aplicativo": str((doc.get("origem") or {}).get("aplicativo") or ""),
            "remetente": envio.get("remetente") or {},
            "enviado_em": str(envio.get("enviado_em") or doc.get("gerado_em") or ""),
            "segmentos": (doc.get("resumo") or {}).get("segmentos"),
            "clipes_insert": len(carta.selecao or []),
            "avisos": len(doc.get("avisos") or []), **_leitura_rapida(carta)} if envio.get("id") else \
        {"arquivo": arquivo, "erro": "não é um envio do Magic Link (sem 'envio')"}


def _nome_da_timeline(carta, tipo: str) -> str:
    return carta.nome or carta.resultado.get("composition_name") or "timeline"
