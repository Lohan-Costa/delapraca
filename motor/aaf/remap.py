from __future__ import annotations

import logging
import shutil
from pathlib import Path

from .percurso import componentes

log = logging.getLogger("delapraca.aaf.remap")

_HEX = set("0123456789abcdefABCDEF")


def api_para_urn(mob_id: str) -> str:
    cru = mob_id.strip()
    if cru[:2].lower() == "0x":
        cru = cru[2:]
    if cru.lower().startswith("urn:smpte:umid:"):
        cru = cru[len("urn:smpte:umid:"):]
    h = "".join(c for c in cru if c in _HEX).lower()
    if len(h) != 64:
        raise ValueError(f"MobID com tamanho inesperado ({len(h)} dígitos): {mob_id!r}")
    return "urn:smpte:umid:" + ".".join(h[i:i + 8] for i in range(0, 64, 8))


def _chave(mob_id) -> str:
    s = str(mob_id).strip()
    baixo = s.lower()
    if baixo.startswith("urn:smpte:umid:"):
        s = s[len("urn:smpte:umid:"):]
    elif baixo.startswith("0x"):
        s = s[2:]
    return "".join(c for c in s if c in _HEX).lower()


def remapear_masters(
    aaf_entrada: str,
    aaf_saida: str,
    mapa: dict[str, str],
    *,
    renomear: dict[str, str] | None = None,
    achatar_subclipes: bool = True,
    achatar_group_clips: bool = True,
) -> dict:
    import aaf2
    from aaf2.mobid import MobID

    entrada, saida = Path(aaf_entrada), Path(aaf_saida)
    if entrada.resolve() == saida.resolve():
        raise ValueError("entrada e saída devem ser arquivos diferentes — o AAF de "
                         "origem nunca é modificado no lugar")
    if not entrada.is_file():
        raise FileNotFoundError(f"AAF não encontrado: {aaf_entrada}")
    if not mapa and not (achatar_subclipes or achatar_group_clips):
        raise ValueError("nada a fazer: mapa vazio e achatamento desligado")

    alvos = {_chave(velho): MobID(mobid=api_para_urn(novo)) for velho, novo in mapa.items()}
    nomes = {_chave(k): v for k, v in (renomear or {}).items()}

    saida.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(entrada, saida)

    mobs_trocados: list[dict] = []
    clips_trocados = 0
    vistos: set[str] = set()

    achatamento = {"subclipes": 0, "group_clips": 0, "orfaos_removidos": 0}

    with aaf2.open(str(saida), "rw") as f:
        if achatar_subclipes or achatar_group_clips:
            from .denest import achatar
            achatamento = achatar(f, subclipes=achatar_subclipes,
                                  group_clips=achatar_group_clips)

        todos = list(f.content.mobs)

        for mob in todos:
            for slot in mob.slots:
                for comp in componentes(slot.segment):
                    ref = getattr(comp, "mob_id", None)
                    if ref is None:
                        continue
                    k = _chave(ref)
                    if k in alvos:
                        comp.mob_id = alvos[k]
                        clips_trocados += 1

        for mob in todos:
            k = _chave(mob.mob_id)
            if k not in alvos:
                continue
            vistos.add(k)
            antes = mob.name
            mob.mob_id = alvos[k]
            if k in nomes:
                mob.name = nomes[k]
            mobs_trocados.append({
                "nome": antes, "nome_novo": mob.name,
                "de": k, "para": _chave(alvos[k]),
            })

    nao_achados = [m for m in alvos if m not in vistos]
    resultado = {
        "saida": str(saida),
        "masters_trocados": len(mobs_trocados),
        "referencias_trocadas": clips_trocados,
        "pedidos": len(alvos),
        "nao_encontrados": nao_achados,
        "subclipes_achatados": achatamento["subclipes"],
        "group_clips_achatados": achatamento["group_clips"],
        "orfaos_removidos": achatamento["orfaos_removidos"],
        "detalhe": mobs_trocados,
    }
    log.info("remap: %d/%d masters, %d referências (achatados: %d subclipes, %d grupos)",
             len(mobs_trocados), len(alvos), clips_trocados,
             achatamento["subclipes"], achatamento["group_clips"])
    return resultado
