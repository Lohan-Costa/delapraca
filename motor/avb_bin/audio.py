from __future__ import annotations

import logging
import math
import os
import struct
import tempfile
from pathlib import Path

from avb_export import clone_generic as CG
from avb_export import media_bin as MB
from avb_export.relink import replace_field

log = logging.getLogger("delapraca.avb_bin.audio")

_MARCA_AMD = struct.pack("<H", 18) + b"_AMD_SOURCE_LENGTH" + struct.pack("<I", 8)

VOLUME_TEMPLATE = b"AVB-MEDIA"

EXTENSOES = {".wav", ".aif", ".aiff"}


def forma_avid(caminho: str) -> str:
    if len(caminho) > 2 and caminho[1] == ":" and caminho[2] in "\\/":
        return caminho[0] + "//" + caminho[3:].replace("\\", "/")
    return caminho.replace("\\", "/")


def rotulo_do_volume(caminho: str) -> str | None:
    if os.name != "nt" or len(caminho) < 2 or caminho[1] != ":":
        return None
    import ctypes

    letra = caminho[0].upper()
    buf = ctypes.create_unicode_buffer(261)
    fs = ctypes.create_unicode_buffer(261)
    sn, ml, fl = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        f"{letra}:\\", buf, 261, ctypes.byref(sn), ctypes.byref(ml),
        ctypes.byref(fl), fs, 261)
    if not ok or not buf.value:
        return None
    return f"{buf.value} ({letra}:)"


def _patch_amd_source_length(caminho_bin: Path, samples: int) -> int:
    d = bytearray(caminho_bin.read_bytes())
    n = i = 0
    while True:
        i = d.find(_MARCA_AMD, i)
        if i < 0:
            break
        struct.pack_into("<Q", d, i + len(_MARCA_AMD), samples)
        n += 1
        i += len(_MARCA_AMD) + 8
    if n:
        caminho_bin.write_bytes(bytes(d))
    return n


def _patch_caminho(caminho_bin: Path, original: str) -> int:
    alvo = forma_avid(original)
    if alvo == original:
        return 0
    d = caminho_bin.read_bytes()
    agulha = original.encode("latin-1", "replace")
    n = d.count(agulha)
    if n:
        caminho_bin.write_bytes(d.replace(agulha, alvo.encode("latin-1", "replace")))
    return n


def _patch_volume(caminho_bin: Path, volume: str) -> int:
    d = caminho_bin.read_bytes()
    novo = volume.encode("latin-1", "replace")
    d, n1 = replace_field(d, VOLUME_TEMPLATE, novo)
    d, n2 = replace_field(d, b"\x00\x00" + VOLUME_TEMPLATE, b"\x00\x00" + novo)
    caminho_bin.write_bytes(d)
    return n1 + n2


def e_audio(caminho: str) -> bool:
    return Path(caminho).suffix.lower() in EXTENSOES


TOLERANCIA_FRAMES = 2


def autorar_masters(arquivos, saida: str, fps: float | None = None,
                    minimos: dict[str, int] | None = None, progresso=None) -> dict:
    from avb_export.merge import materialize, serialize
    from avb_export.reader import topen
    import random

    arquivos = [str(a) for a in arquivos if e_audio(str(a))]
    if not arquivos:
        return {"saida": saida, "clipes": 0, "pulados": [], "sem_template": [],
                "corrigidos": {}, "mapa": {}, "esticados": [], "curtos_demais": []}

    templates = MB.load_bundled_templates()
    tmp = Path(tempfile.mkdtemp(prefix="dlpc_audio_"))
    clones: list[Path] = []
    mapa: dict[str, str] = {}
    pulados: list[tuple[str, str]] = []
    sem_template: list[str] = []
    contagem = {"amd": 0, "caminho": 0, "volume": 0}
    minimos = minimos or {}
    esticados: list[tuple[str, int]] = []
    curtos_demais: list[tuple[str, int]] = []
    total = len(arquivos)

    for i, caminho in enumerate(arquivos, 1):
        nome = Path(caminho).name
        if progresso:
            progresso(i - 1, total, nome)
        try:
            chave = MB._probe_key(caminho)
        except Exception as e:
            pulados.append((nome, f"ffprobe falhou: {e}"))
            continue
        tpl = templates.get(chave)
        if tpl is None:
            sem_template.append(f"{nome} {chave}")
            continue

        template_bin, clip, kind = tpl
        alvo = tmp / f"clone_{i}.avb"

        minimo = int(minimos.get(caminho) or 0)
        if minimo and fps:
            try:
                amostras, taxa = CG._audio_samples(caminho)
                real = math.ceil(amostras / taxa * float(fps))
                falta = minimo - real
                if falta > TOLERANCIA_FRAMES:
                    curtos_demais.append((nome, falta))
                    log.warning("%s é %d frames mais curto que a timeline pede — recusado",
                                nome, falta)
                    continue
                if falta > 0:
                    esticados.append((nome, falta))
            except Exception:
                minimo = 0

        try:
            CG.clone_generic(template_bin, clip, caminho, str(alvo), kind=kind, fps=fps,
                             master_len_min=minimo or None)
            samples, _sr = CG._audio_samples(caminho)
            contagem["amd"] += _patch_amd_source_length(alvo, samples)
            contagem["caminho"] += _patch_caminho(alvo, caminho)
            volume = rotulo_do_volume(caminho)
            if volume:
                contagem["volume"] += _patch_volume(alvo, volume)
        except Exception as e:
            pulados.append((nome, " ".join(str(e).split())[:160]))
            log.warning("falhou ao autorar %s: %s", nome, e)
            continue
        clones.append(alvo)
        try:
            with topen(str(alvo)) as f:
                for m in f.content.mobs:
                    if str(getattr(m, "mob_type", "")) == "MasterMob":
                        mapa[caminho] = str(m.mob_id)
                        break
        except Exception:
            log.debug("não consegui ler o MobID do clone de %s", nome, exc_info=True)

    if progresso:
        progresso(total, total, "")
    if not clones:
        return {"saida": saida, "clipes": 0, "pulados": pulados, "mapa": {},
                "sem_template": sem_template, "corrigidos": contagem,
                "esticados": esticados, "curtos_demais": curtos_demais}

    Path(saida).parent.mkdir(parents=True, exist_ok=True)
    with topen(str(clones[0])) as base:
        tem = {m.mob_id for m in base.content.mobs}
        for outro in clones[1:]:
            with topen(str(outro)) as f:
                for m in f.content.mobs:
                    if m.mob_id in tem:
                        continue
                    base.content.add_mob(materialize(base, serialize(m)))
                    tem.add(m.mob_id)
        for item in base.content.property_data["items"]:
            item.property_data["user_placed"] = (
                getattr(item.mob, "mob_type_id", None) == 2)
        base.content.uid = random.getrandbits(63)
        base.write(str(saida))

    resultado = {"saida": saida, "clipes": len(clones), "pulados": pulados,
                 "sem_template": sem_template, "corrigidos": contagem, "mapa": mapa,
                 "esticados": esticados, "curtos_demais": curtos_demais}
    log.info("áudio autorado: %s", resultado)
    return resultado
