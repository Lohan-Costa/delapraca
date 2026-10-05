from __future__ import annotations

import csv
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from datetime import datetime

import cache
import plataforma

log = logging.getLogger(__name__)

RE_ID = re.compile(r"^[0-9a-f]{32}$")
COR_MARCADOR = "Purple"
CSV = "conferencia.csv"
DADO_DO_MARCADOR = "dlpc-conferencia"


EM_USO_S = 180.0
INTERNO = "_interno"
_MAX_TIMELINE = 50
_MAX_PLANO = 40
_PONTA = {"entrada": "início", "saída": "fim", "saida": "fim"}


def pasta_raiz() -> str:
    return str(cache.sub(cache.CONFERENCIAS))


def _limpo(texto, maximo: int) -> str:
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", str(texto or "")).strip()
    t = re.sub(r"\s+", " ", t)[:maximo].rstrip(" .")
    return t or "sem nome"


def _livre(pasta: str, nome: str, ext: str = "") -> str:
    alvo, n = os.path.join(pasta, nome + ext), 2
    while os.path.exists(alvo):
        alvo = os.path.join(pasta, f"{nome} ({n}){ext}")
        n += 1
    return alvo


def _nome_do_quadro(p: dict, confere: bool) -> str:
    partes = [p["tc"].replace(":", "_").replace(";", "_"), f"V{p.get('trilha') or '?'}",
              _limpo(p.get("plano") or p.get("arquivo") or "", _MAX_PLANO),
              _PONTA.get(p.get("ponta") or "", p.get("ponta") or "ponto")]
    return ("" if confere else "NÃO CONFERE ") + " ".join(partes)


def _tc(q: int, nominal: int) -> str:
    s, f = divmod(max(0, int(q)), nominal)
    return f"{s // 3600:02d}:{s // 60 % 60:02d}:{s % 60:02d}:{f:02d}"


def _gravar(caminho: str, dado) -> None:
    tmp = f"{caminho}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(dado, fh, ensure_ascii=False)
    os.replace(tmp, caminho)


class Conferencias:

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fila: queue.Queue = queue.Queue()
        self._resultados: dict[str, dict[str, dict]] = {}
        self._planos: dict[str, dict] = {}
        self._pendentes: dict[str, int] = {}
        self._pastas: dict[str, str] = {}
        self._uso: dict[str, float] = {}
        threading.Thread(target=self._trabalhar, name="conferencia", daemon=True).start()

    def criar(self, contexto: dict, timeline: str) -> dict:
        from tools.conferir_conform import SEGUNDOS_POR_PONTO

        cid = uuid.uuid4().hex
        rotulo = f"{_limpo(timeline, _MAX_TIMELINE)} · {datetime.now().strftime('%Y-%m-%d %Hh%M')}"
        pasta = _livre(pasta_raiz(), rotulo)
        os.makedirs(os.path.join(pasta, INTERNO), exist_ok=True)
        nominal = int(contexto.get("nominal") or round(contexto.get("fps") or 24))
        inicio = int(contexto.get("inicio") or 0)
        pontos = []
        for p in contexto.get("pontos") or []:
            q = int(p["quadro"])
            pontos.append({"quadro": q, "tc": _tc(inicio + q, nominal), "ponta": p.get("ponta"),
                           "plano": p.get("plano"), "trilha": p.get("trilha"),
                           "arquivo": p.get("arquivo")})
        plano = {"id": cid, "timeline": timeline, "referencia": contexto["referencia"],
                 "trilha_referencia": contexto.get("trilha_referencia"),
                 "ref_na_timeline": int(contexto.get("ref_na_timeline") or 0),
                 "ref_entrada": int(contexto.get("ref_entrada") or 0),
                 "ref_fps": float(contexto.get("ref_fps") or contexto.get("fps") or 24),
                 "quadro_px": contexto.get("quadro_px") or [1920, 1080], "pontos": pontos}
        _gravar(os.path.join(pasta, INTERNO, "plano.json"), plano)
        with self._lock:
            self._planos[cid] = plano
            self._pastas[cid] = pasta
            self._resultados[cid] = {}
            self._uso[cid] = time.monotonic()
        return {"id": cid, "pontos": len(pontos), "segundos": round(len(pontos) * SEGUNDOS_POR_PONTO)}

    def _achar(self, cid: str) -> str | None:
        raiz = pasta_raiz()
        try:
            nomes = os.listdir(raiz)
        except OSError:
            return None
        for nome in nomes:
            plano = os.path.join(raiz, nome, INTERNO, "plano.json")
            try:
                with open(plano, encoding="utf-8") as fh:
                    if json.load(fh).get("id") == cid:
                        return os.path.join(raiz, nome)
            except (OSError, ValueError, AttributeError):
                continue
        return None

    def _plano(self, cid: str) -> dict:
        if not RE_ID.match(cid or ""):
            raise ValueError("conferência inválida")
        with self._lock:
            if cid in self._planos and os.path.isdir(self._pastas.get(cid, "")):
                self._uso[cid] = time.monotonic()
                return self._planos[cid]
        pasta = self._achar(cid)
        if not pasta:
            with self._lock:
                self._planos.pop(cid, None)
                self._pastas.pop(cid, None)
            raise LookupError("essa conferência não está mais nesta máquina — monte de novo")
        interno = os.path.join(pasta, INTERNO)
        try:
            with open(os.path.join(interno, "plano.json"), encoding="utf-8") as fh:
                plano = json.load(fh)
        except (OSError, ValueError):
            raise LookupError("essa conferência não está mais nesta máquina — monte de novo") from None
        try:
            with open(os.path.join(interno, "resultados.json"), encoding="utf-8") as fh:
                res = json.load(fh)
        except (OSError, ValueError):
            res = {}
        with self._lock:
            self._planos[cid] = plano
            self._pastas[cid] = pasta
            self._resultados[cid] = res if isinstance(res, dict) else {}
            self._uso[cid] = time.monotonic()
        return plano

    def pasta(self, cid: str) -> str:
        self._plano(cid)
        with self._lock:
            return self._pastas[cid]

    def em_uso(self) -> list[str]:
        agora = time.monotonic()
        with self._lock:
            return [self._pastas[c] for c in self._pastas
                    if self._pendentes.get(c, 0) or agora - self._uso.get(c, 0) < EM_USO_S]

    def iniciar(self, cid: str, do_comeco: bool = False) -> dict:
        plano = self._plano(cid)
        pasta = self.pasta(cid)
        interno = os.path.join(pasta, INTERNO)
        cache.tocar(pasta)
        if do_comeco:
            with self._lock:
                self._resultados[cid] = {}
            for d in (pasta, interno):
                for nome in os.listdir(d):
                    if nome.endswith(".jpg") or nome in ("resultados.json", CSV):
                        try:
                            os.remove(os.path.join(d, nome))
                        except OSError:
                            pass
        with self._lock:
            feitos = set(self._resultados.get(cid, {}))
        faltam = [{"quadro": p["quadro"], "tc": p["tc"], "plano": p.get("plano"),
                   "trilha": p.get("trilha"), "ponta": p.get("ponta"),
                   "imagem": os.path.join(interno, f"{p['quadro']}.jpg")}
                  for p in plano["pontos"] if str(p["quadro"]) not in feitos]
        return {"id": cid, "timeline": plano["timeline"], "total": len(plano["pontos"]),
                "trilha_referencia": plano.get("trilha_referencia"), "faltam": faltam,
                "pasta": pasta, **self._placar(cid)}

    def comparar(self, cid: str, quadros: list) -> int:
        plano = self._plano(cid)
        validos = {p["quadro"] for p in plano["pontos"]}
        n = 0
        for q in quadros or []:
            try:
                q = int(q)
            except (TypeError, ValueError):
                continue
            if q in validos:
                with self._lock:
                    self._pendentes[cid] = self._pendentes.get(cid, 0) + 1
                self._fila.put((cid, q))
                n += 1
        return n

    def _trabalhar(self) -> None:
        from tools.conferir_conform import comparar_um

        while True:
            cid, q = self._fila.get()
            pasta = plano = None
            try:
                plano = self._plano(cid)
                pasta = self.pasta(cid)
                img = os.path.join(pasta, INTERNO, f"{q}.jpg")
                if os.path.isfile(img):
                    ref_q = plano["ref_entrada"] + (q - plano["ref_na_timeline"])
                    r = comparar_um(img, plano["referencia"], ref_q, plano["ref_fps"],
                                    quadro_px=tuple(plano.get("quadro_px") or (1920, 1080)))
                    self._batizar(plano, pasta, q, img, r)
                else:
                    r = {"erro": "o Resolve não exportou o quadro"}
            except Exception as e:
                log.warning("conferência %s, quadro %s: %s", cid, q, e)
                r = {"erro": str(e)[:200]}
            with self._lock:
                self._resultados.setdefault(cid, {})[str(q)] = r
                self._pendentes[cid] = max(0, self._pendentes.get(cid, 1) - 1)
                fim = self._pendentes[cid] == 0
                copia = dict(self._resultados[cid])
            if pasta and plano and (fim or len(copia) % 25 == 0):
                try:
                    _gravar(os.path.join(pasta, INTERNO, "resultados.json"), copia)
                    self._planilha(plano, pasta, copia)
                except OSError as e:
                    log.warning("conferência %s: não gravou os resultados (%s)", cid, e)
            if fim:
                cache.talvez_aplicar_teto(protegidos=self.em_uso())

    def _batizar(self, plano: dict, pasta: str, q: int, img: str, r: dict) -> None:
        from tools.conferir_conform import LIMIAR

        p = next((x for x in plano["pontos"] if x["quadro"] == q), None)
        if p is None:
            return
        confere = not r.get("erro") and r.get("corr", 1.0) >= LIMIAR
        try:
            destino = _livre(pasta, _nome_do_quadro(p, confere), ".jpg")
            os.replace(img, destino)
            r["imagem"] = os.path.basename(destino)
        except OSError as e:
            log.info("conferência: o quadro %s ficou em _interno (%s)", q, e)

    def _planilha(self, plano: dict, pasta: str, res: dict) -> None:
        from tools.conferir_conform import LIMIAR

        tmp = os.path.join(pasta, f"{CSV}.{os.getpid()}.tmp")
        with open(tmp, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh, delimiter=";")
            w.writerow(["TC", "trilha", "plano", "ponta", "semelhança", "confere", "imagem"])
            for p in plano["pontos"]:
                r = res.get(str(p["quadro"]))
                if not r:
                    continue
                if r.get("erro"):
                    sem, conf = "", f"erro: {r['erro']}"
                else:
                    sem = f"{r.get('corr', 0):.3f}".replace(".", ",")
                    conf = "sim" if r.get("corr", 1.0) >= LIMIAR else "não"
                w.writerow([p["tc"], f"V{p.get('trilha') or '?'}",
                            p.get("plano") or p.get("arquivo") or "",
                            _PONTA.get(p.get("ponta") or "", p.get("ponta") or ""), sem, conf,
                            r.get("imagem") or ""])
        os.replace(tmp, os.path.join(pasta, CSV))

    def _placar(self, cid: str) -> dict:
        from tools.conferir_conform import LIMIAR

        with self._lock:
            res = dict(self._resultados.get(cid, {}))
            na_fila = self._pendentes.get(cid, 0)
        ruins = {k: v for k, v in res.items() if v.get("erro") or (v.get("corr", 1.0) < LIMIAR)}
        return {"comparados": len(res), "na_fila": na_fila, "conferem": len(res) - len(ruins),
                "nao_conferem": len(ruins)}

    def estado(self, cid: str) -> dict:
        plano = self._plano(cid)
        return {"id": cid, "total": len(plano["pontos"]), **self._placar(cid)}

    def marcas(self, cid: str) -> dict:
        from tools.conferir_conform import LIMIAR

        plano = self._plano(cid)
        with self._lock:
            res = dict(self._resultados.get(cid, {}))
        fora = []
        for p in plano["pontos"]:
            r = res.get(str(p["quadro"]))
            if not r:
                continue
            trk = f"Trk V{p.get('trilha')}"
            quem = p.get("plano") or p.get("arquivo") or ""
            if r.get("erro"):
                tipo, nota = "Não deu para conferir", r["erro"]
            elif r.get("corr", 1.0) < LIMIAR:
                desloca = (r.get("dx_px") or r.get("dy_px")) and r.get("corr_deslocada", 0) >= LIMIAR
                tipo = "Enquadramento diferente da referência" if desloca else "Não confere com a referência"
                nota = f"semelhança {r['corr']:.2f}".replace(".", ",")
                if desloca:
                    nota += f"; deslocada {r['dx_px']}×{r['dy_px']} px confere ({r['corr_deslocada']:.2f})".replace(".", ",")
            else:
                continue
            fora.append({"quadro": p["quadro"], "tc": p["tc"], "nome": tipo,
                         "nota": f"{trk} · {quem} · {p.get('ponta') or 'ponto'} em {p['tc']} · {nota}"})
        return {"cor": COR_MARCADOR, "dado": DADO_DO_MARCADOR, "marcas": fora,
                "trilha_referencia": plano.get("trilha_referencia")}

    def apagar(self, cid: str) -> None:
        pasta = self.pasta(cid)
        try:
            plataforma.apagar_arvore(pasta)
        except OSError as e:
            log.info("conferência %s: não apaguei tudo (%s)", cid, e)
        with self._lock:
            self._planos.pop(cid, None)
            self._pastas.pop(cid, None)
            self._resultados.pop(cid, None)


_UNICA: Conferencias | None = None


def conferencias() -> Conferencias:
    global _UNICA
    if _UNICA is None:
        _UNICA = Conferencias()
    return _UNICA
