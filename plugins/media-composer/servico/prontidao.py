from __future__ import annotations

import subprocess
import threading
import time
import uuid

import plataforma

_CACHE: dict = {"quando": 0.0, "resultado": None}
_TRAVA = threading.Lock()
VALIDADE_S = 60.0

GESTO_MOTOR = ("Feche e abra o aplicativo De Lá Pra Cá. Se continuar, rode o ATUALIZAR do De Lá Pra Cá "
               "(ele reinstala o motor).")


def _binario(nome: str, exe: str | None, papel: str) -> dict:
    item = {"id": nome, "nome": f"{papel} ({nome})", "ok": False}
    if not exe:
        item["erro"] = f"o motor não achou o {nome}"
    else:
        try:
            r = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=8,
                               **plataforma.SEM_JANELA)
            linha = (r.stdout or "").splitlines()[0] if r.stdout else ""
            if r.returncode == 0 and linha:
                item.update(ok=True, detalhe=linha[:80], caminho=exe)
                return item
            item["erro"] = f"o {nome} não respondeu (código {r.returncode})"
        except (OSError, subprocess.SubprocessError) as e:
            item["erro"] = f"o {nome} não rodou: {type(e).__name__}"
    item["gesto"] = GESTO_MOTOR
    return item


def _pasta_gravavel() -> dict:
    item = {"id": "pasta", "nome": "pasta de trabalho do De Lá Pra Cá", "ok": False}
    try:
        pasta = plataforma.pasta_dados()
        pasta.mkdir(parents=True, exist_ok=True)
        teste = pasta / f".prontidao-{uuid.uuid4().hex}"
        teste.write_bytes(b"ok")
        teste.unlink()
        item.update(ok=True, detalhe=str(pasta))
    except OSError as e:
        item.update(erro=f"não consegui gravar na pasta de trabalho ({type(e).__name__})",
                    gesto="Confira o espaço livre do disco do sistema e as permissões da sua pasta de "
                          "usuário; depois clique em Conferir de novo.")
    return item


def _do_aplicativo(app, bancada: bool) -> dict:
    item = {"id": "aplicativo", "nome": "motor aberto pelo aplicativo De Lá Pra Cá", "ok": bool(app or bancada)}
    if app:
        item["detalhe"] = str(app)
    elif bancada:
        item["detalhe"] = "bancada de desenvolvimento"
    else:
        item.update(erro="este motor foi aberto sem o aplicativo",
                    gesto="Abra o aplicativo De Lá Pra Cá: ele assume o motor sozinho, e esta tela "
                          "continua em seguida.")
    return item


def conferir(servico=None, forcar: bool = False) -> dict:
    with _TRAVA:
        agora = time.monotonic()
        if not forcar and _CACHE["resultado"] and agora - _CACHE["quando"] < VALIDADE_S:
            return _CACHE["resultado"]
        from media import fftools
        itens = [
            _binario("ffprobe", fftools.ffprobe(), "leitor de mídia: TC e duração"),
            _binario("ffmpeg", fftools.ffmpeg(), "conversor de mídia: quadros e conferência"),
            _pasta_gravavel(),
        ]
        app = getattr(servico, "app_exe", None) if servico is not None else None
        if servico is not None:
            itens.append(_do_aplicativo(app, bool(getattr(servico, "bancada", False))))
        r = {"ok": all(i["ok"] for i in itens), "itens": itens,
             "aplicativo": str(app) if app else None,
             "quando": time.strftime("%H:%M:%S")}
        _CACHE.update(quando=agora, resultado=r)
        return r
