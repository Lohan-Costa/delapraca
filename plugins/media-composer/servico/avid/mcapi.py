from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePath

log = logging.getLogger("delapraca.avid.mcapi")

BACKEND_PADRAO = "[::1]:9100"

TEMPO_DE_ARQUIVO_S = 300.0

_SERVICO = "/mcapi.MCAPI/"

ASSENTAR_DEPOIS_DE_CARREGAR_S = 3.0
_ESPERAM_O_MONITOR = frozenset({"GetViewerMobs", "CloseBin"})
_trava_do_monitor = threading.Lock()
_carregou_em: float | None = None


def _esperar_o_monitor(metodo: str) -> None:
    global _carregou_em
    with _trava_do_monitor:
        if metodo == "LoadMobsIntoViewer":
            return
        if metodo not in _ESPERAM_O_MONITOR or _carregou_em is None:
            return
        falta = ASSENTAR_DEPOIS_DE_CARREGAR_S - (time.monotonic() - _carregou_em)
    if falta > 0:
        log.info("%s espera %.1f s: um monitor acabou de carregar", metodo, falta)
        time.sleep(falta)


def _marcar_carregamento(metodo: str) -> None:
    global _carregou_em
    if metodo == "LoadMobsIntoViewer":
        with _trava_do_monitor:
            _carregou_em = time.monotonic()


class McApiError(Exception):
    pass


class McApiIndisponivel(McApiError):
    pass


class McApiNaoImplementado(McApiError):
    pass


@dataclass
class BinItem:
    name: str
    mob_id: str
    selected: bool = False


@dataclass
class Master:

    name: str
    mob_id: str
    length_frames: int | None = None


def mob_id_valido(mob_id: str) -> bool:
    cru = (mob_id or "").strip()
    if cru[:2].lower() == "0x":
        cru = cru[2:]
    h = [c for c in cru if c in "0123456789abcdefABCDEF"]
    if any(c not in "0123456789abcdefABCDEF-. " for c in cru):
        return False
    return len(h) == 64


def mob_id_para_pyavb(mob_id_api: str) -> str:
    from avb.mobid import MobID

    cru = mob_id_api.strip()
    if cru[:2].lower() == "0x":
        cru = cru[2:]
    h = "".join(c for c in cru if c in "0123456789abcdefABCDEF").lower()
    if len(h) != 64:
        raise McApiError(f"MobID com tamanho inesperado ({len(h)} dígitos): {mob_id_api!r}")
    urn = "urn:smpte:umid:" + ".".join(h[i:i + 8] for i in range(0, 64, 8))
    return MobID(mobid=urn).bytes_le.hex()


class MediaComposer:

    def __init__(self, endereco: str = BACKEND_PADRAO, timeout: float = 20.0):
        self.endereco = endereco
        self.timeout = timeout
        self._canal = None
        self._projeto: dict | None = None

    def __enter__(self) -> "MediaComposer":
        import grpc

        self._canal = grpc.insecure_channel(self.endereco)
        return self

    def __exit__(self, *exc) -> None:
        if self._canal is not None:
            self._canal.close()
            self._canal = None

    def _msg(self, nome: str):
        from avid.proto import mcapi_types_pb2 as pb

        return getattr(pb, nome)

    def _enum(self, metodo: str, campo: str, nome: str) -> int:
        corpo = self._msg(f"{metodo}RequestBody")
        fd = corpo.DESCRIPTOR.fields_by_name.get(campo)
        if fd is None or fd.enum_type is None:
            raise McApiError(f"{metodo}.{campo} não é um campo de enum")
        valor = fd.enum_type.values_by_name.get(nome)
        if valor is None:
            validos = ", ".join(fd.enum_type.values_by_name)
            raise McApiError(f"{nome!r} não é válido para {metodo}.{campo} ({validos})")
        return valor.number

    def _pedido(self, metodo: str, **campos):
        req = self._msg(f"{metodo}Request")()
        corpo = self._msg(f"{metodo}RequestBody")(**campos)
        req.body.CopyFrom(corpo)
        req.header.SetInParent()
        return req

    def _chamar(self, metodo: str, _timeout: float | None = None, **campos):
        import grpc

        req = self._pedido(metodo, **campos)
        stub = self._canal.unary_unary(
            _SERVICO + metodo,
            request_serializer=type(req).SerializeToString,
            response_deserializer=self._msg(f"{metodo}Response").FromString,
        )
        _esperar_o_monitor(metodo)
        try:
            return stub(req, timeout=_timeout or self.timeout)
        except grpc.RpcError as e:
            raise self._traduzir(e, metodo) from None
        finally:
            _marcar_carregamento(metodo)

    def _fluxo(self, metodo: str, **campos):
        import grpc

        req = self._pedido(metodo, **campos)
        stub = self._canal.unary_stream(
            _SERVICO + metodo,
            request_serializer=type(req).SerializeToString,
            response_deserializer=self._msg(f"{metodo}Response").FromString,
        )
        try:
            return list(stub(req, timeout=self.timeout))
        except grpc.RpcError as e:
            raise self._traduzir(e, metodo) from None

    @staticmethod
    def _traduzir(e, metodo: str) -> McApiError:
        import grpc

        codigo = e.code()
        if codigo == grpc.StatusCode.UNIMPLEMENTED:
            return McApiNaoImplementado(
                f"{metodo} não existe nesta versão do Media Composer"
            )
        if codigo == grpc.StatusCode.UNAVAILABLE:
            return McApiIndisponivel(
                f"o Media Composer não está atendendo em {metodo} — ele está aberto?"
            )
        if codigo == grpc.StatusCode.DEADLINE_EXCEEDED:
            return McApiError(
                f"{metodo} não respondeu no prazo. O Media Composer pode estar "
                f"ocupado — ou a chamada é de fluxo e foi feita como unária."
            )
        return McApiError(f"{metodo} falhou: {codigo.name} — {e.details()}")

    def esta_vivo(self) -> bool:
        try:
            self.info()
            return True
        except McApiError:
            return False

    def info(self) -> dict:
        r = self._chamar("GetAppInfo")
        return {"app": r.body.app_name, "version": r.body.app_version,
                "sdk": r.body.sdk_version, "license": r.body.license_type}

    def abrir_bin(self, caminho: str) -> None:
        self._chamar("OpenBin", _timeout=TEMPO_DE_ARQUIVO_S, bin_path=caminho)

    def fechar_bin(self, caminho: str) -> None:
        self._chamar("CloseBin", _timeout=TEMPO_DE_ARQUIVO_S, bin_path=caminho)

    def projeto(self, recarregar: bool = False) -> dict:
        if self._projeto is not None and not recarregar:
            return self._projeto
        r = self._chamar("GetOpenProjectInfo")
        self._projeto = {"path": r.body.path, "type": r.body.project_type,
                         "width": r.body.raster_width, "height": r.body.raster_height}
        return self._projeto

    def caminho_relativo_da_bin(self, caminho_absoluto: str) -> str:
        import ntpath
        import os

        raiz = self.projeto().get("path") or ""
        from media.safepath import forma_windows
        win = forma_windows(raiz)
        try:
            rel = (ntpath if win else os.path).relpath(caminho_absoluto, raiz)
        except ValueError:
            return caminho_absoluto
        return rel if not rel.startswith("..") else caminho_absoluto

    def itens_da_bin(self, bin_relativo: str, flags=("masterClips",),
                     apenas_selecionados: bool = False) -> list[BinItem]:
        vals = [self._enum("GetListOfBinItems", "bin_flags", f) if isinstance(f, str)
                else f for f in flags]
        respostas = self._fluxo("GetListOfBinItems",
                                bin_relative_path=bin_relativo, bin_flags=vals,
                                only_selected_flag=apenas_selecionados)
        itens = []
        for r in respostas:
            if r.body.mob_name or r.body.mob_id:
                itens.append(BinItem(name=r.body.mob_name, mob_id=r.body.mob_id,
                                     selected=bool(r.body.mob_selected)))
        return itens

    def bins(self, flags=("AllTypes",), incluir_lixeira: bool = False) -> list[str]:
        vals = [self._enum("GetBins", "request_flag", f) if isinstance(f, str)
                else f for f in flags]
        caminhos = [r.body.absolute_path
                    for r in self._fluxo("GetBins", request_flag=vals)
                    if r.body.absolute_path]
        if incluir_lixeira:
            return caminhos
        return [c for c in caminhos if not self.esta_na_lixeira(c)]

    PASTA_DA_LIXEIRA = "trash"

    def esta_na_lixeira(self, caminho_absoluto: str) -> bool:
        rel = self.caminho_relativo_da_bin(caminho_absoluto)
        primeiro = rel.replace("\\", "/").split("/")[0]
        return primeiro.lower() == self.PASTA_DA_LIXEIRA

    def bins_abertas(self) -> list[str] | None:
        try:
            respostas = self._fluxo("GetListOfWindows")
        except McApiNaoImplementado:
            return None
        nomes = []
        for r in respostas:
            for w in r.body.windows:
                if w.type == "Bins" and w.visibility:
                    nomes.append(w.name[2:] if w.name.startswith("* ") else w.name)
        return nomes

    def selecao(self, on_progress=None, apenas_abertas: bool = True) -> dict[str, list[BinItem]]:
        caminhos = self.bins()
        abertas = self.bins_abertas() if apenas_abertas else None
        if abertas is not None:
            nomes = set(abertas)
            caminhos = [c for c in caminhos
                        if Path(c).stem in nomes or Path(c).name in nomes]

        fora: dict[str, list[BinItem]] = {}
        total = len(caminhos)
        for i, caminho in enumerate(caminhos, 1):
            nome = Path(caminho).stem
            if on_progress:
                on_progress(i, total, nome)
            try:
                selecionados = self.itens_de(caminho, apenas_selecionados=True)
            except McApiError:
                continue
            if selecionados:
                fora[nome] = selecionados
        return fora

    def colunas_do_master(self, mob_id_api: str) -> dict[str, str]:
        fora: dict[str, str] = {}
        for r in self._fluxo("GetMobInfo", mob_id=mob_id_api):
            nome = r.body.column_name.strip()
            if nome:
                fora[nome] = r.body.column_value
        return fora

    def arquivo_do_master(self, mob_id_api: str, colunas: dict | None = None) -> str | None:
        col = colunas if colunas is not None else self.colunas_do_master(mob_id_api)
        pasta = (col.get("Source Path") or "").strip()
        arquivo = (col.get("Source File") or "").strip()
        nome = (col.get("Name") or mob_id_api).strip()

        if arquivo:
            so_nome = PurePath(arquivo.replace("\\", "/")).name
            if so_nome != arquivo:
                log.warning("%s: 'Source File' vinha como CAMINHO (%r) — usando só o "
                            "nome (%r)", nome, arquivo, so_nome)
            arquivo = so_nome
        if not arquivo:
            log.debug("%s: sem 'Source File' — vai para a mídia gerenciada", nome)
            return None
        caminho = Path(pasta) / arquivo if pasta else Path(arquivo)
        return str(caminho) if caminho.exists() else None

    def duracao_do_master(self, mob_id_api: str) -> int | None:
        for r in self._fluxo("GetMobInfo", mob_id=mob_id_api):
            if r.body.column_name.strip() == "Frame Count Duration":
                try:
                    return int(r.body.column_value.strip())
                except (TypeError, ValueError):
                    return None
        return None

    def itens_de(self, caminho_absoluto: str, flags=("masterClips",),
                 apenas_selecionados: bool = False) -> list[BinItem]:
        rel = self.caminho_relativo_da_bin(caminho_absoluto)
        try:
            return self.itens_da_bin(rel, flags, apenas_selecionados)
        except McApiError:
            self.abrir_bin(caminho_absoluto)
            return self.itens_da_bin(rel, flags, apenas_selecionados)

    def masters(self, bins_absolutas: list[str], com_duracao: bool = True) -> list[Master]:
        achados: list[Master] = []
        for caminho in bins_absolutas:
            for item in self.itens_de(caminho):
                if not (item.name and item.mob_id):
                    continue
                achados.append(Master(
                    name=item.name,
                    mob_id=mob_id_para_pyavb(item.mob_id),
                    length_frames=self.duracao_do_master(item.mob_id) if com_duracao else None,
                ))
        return achados

    def bin_do_mob(self, mob_id_api: str) -> str | None:
        try:
            r = self._chamar("GetBinFromMob", mob_id=mob_id_api)
        except McApiError:
            return None
        caminho = getattr(getattr(r, "body", None), "absolute_path", "") or ""
        return caminho or None

    def carregar_no_monitor(self, mob_ids: list[str], destino: str = "Record") -> None:
        self._chamar("LoadMobsIntoViewer", mob_ids=list(mob_ids),
                     view_type=self._enum("LoadMobsIntoViewer", "view_type", destino))
