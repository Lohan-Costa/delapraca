from __future__ import annotations

import logging
import time
from pathlib import Path

from .mcapi import MediaComposer

log = logging.getLogger("delapraca.avid.acoes")

CMD_SAVE = 1004


def _corpo(resposta):
    return getattr(resposta, "body", None)


class Acoes:

    def __init__(self, mc: MediaComposer):
        self.mc = mc

    def comando(self, command_id: int):
        return self.mc._chamar("DoCommand", commandId=command_id)

    def salvar(self) -> None:
        self.comando(CMD_SAVE)

    def criar_bin(self, nome: str, subpasta: str = "") -> None:
        self.mc._chamar("CreateBin", folder_path=subpasta, bin_name=nome)

    def nome_de_bin_livre(self, pasta: str, base: str, *, sempre_numerar: bool = False) -> str:
        p = Path(pasta)
        if not sempre_numerar and not (p / f"{base}.avb").exists():
            return base
        n = 1 if sempre_numerar else 2
        while (p / f"{base} {n}.avb").exists():
            n += 1
        return f"{base} {n}"

    def link_file(self, caminho: str, bin_destino: str, preset: str = "") -> str | None:
        campos = {"file_path": caminho, "destination_bin": bin_destino}
        if preset:
            campos["link_settings_name"] = preset
        r = self.mc._chamar("LinkFile", **campos)
        corpo = _corpo(r)
        return getattr(corpo, "mob_id", None) or None

    def import_file(self, caminho: str, bin_destino: str, preset: str = "") -> str | None:
        from avid.mcapi import TEMPO_DE_ARQUIVO_S

        campos = {"file_path": caminho, "destination_bin": bin_destino}
        if preset:
            campos["import_settings_name"] = preset
        r = self.mc._chamar("ImportFile", _timeout=TEMPO_DE_ARQUIVO_S, **campos)
        corpo = _corpo(r)
        return getattr(corpo, "mob_id", None) or None

    def export_file(self, mob_id: str, pasta_destino: str, nome: str,
                    preset: str = "AAF", espera_s: float = 300.0) -> str | None:
        pasta = Path(pasta_destino)
        antes = {p: p.stat().st_mtime for p in pasta.iterdir()} if pasta.is_dir() else {}

        self.mc._chamar(
            "ExportFile",
            mob_id=mob_id,
            file_name=nome,
            export_settings_name=preset,
            destination_path=str(pasta),
        )
        if not pasta.is_dir():
            return None

        limite = time.monotonic() + espera_s
        saida: Path | None = None
        while time.monotonic() < limite:
            candidatos = [
                p for p in pasta.iterdir()
                if p.is_file() and not p.name.startswith("._")
                and (p not in antes or p.stat().st_mtime > antes[p])
            ]
            exatos = [p for p in candidatos if p.stem == nome]
            if exatos or candidatos:
                saida = max(exatos or candidatos, key=lambda p: p.stat().st_mtime)
                break
            time.sleep(0.4)

        if saida is None:
            return None

        anterior, estavel = -1, 0
        while time.monotonic() < limite and estavel < 3:
            atual = saida.stat().st_size
            estavel = estavel + 1 if atual == anterior and atual > 0 else 0
            anterior = atual
            time.sleep(0.3)

        return str(saida)

    def presets(self, tipo: str) -> list[str]:
        metodo = {
            "export": "GetListOfExportSettings",
            "import": "GetListOfImportSettings",
            "link": "GetListOfLinkSettings",
        }[tipo]
        nomes: list[str] = []
        for r in self.mc._fluxo(metodo):
            nomes.extend(getattr(_corpo(r), "setting_names", None) or [])
        return [n for n in dict.fromkeys(nomes) if n]
