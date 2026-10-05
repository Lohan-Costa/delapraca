from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

CABECALHO = """\
# requirements.lock - versoes EXATAS do ambiente que roda hoje.
#
# ASCII PURO de proposito: o pip decodifica este arquivo com a codepage do sistema
# quando nao ha BOM, e um caractere fora do ASCII pode torna-lo ILEGIVEL no Windows.
# A explicacao completa esta em tools/lock.py, que gera este arquivo.
#
# Instalar:  pip install -r requirements.lock
# Regerar :  python tools/lock.py     (NUNCA com `>` do PowerShell - ver tools/lock.py)
#
# Guia de Desenvolvimento Seguro, secao 4: lockfile e' obrigatorio, e nenhuma versao
# publicada ha menos de 3 dias deve ser adotada (quarentena de cadeia de suprimentos).
#
# requirements.txt continua sendo a lista LEGIVEL (o que se pediu e por que);
# este e' o que se INSTALA.
"""


def main() -> int:
    congelado = subprocess.run(
        [sys.executable, "-m", "pip", "freeze", "--exclude-editable"],
        capture_output=True, text=True, check=True).stdout
    linhas = sorted(l.strip() for l in congelado.splitlines() if l.strip())

    fora = [l for l in linhas if not l.isascii()]
    if fora:
        print("nomes nao-ASCII no freeze:", fora, file=sys.stderr)
        return 1

    alvo = RAIZ / "requirements.lock"
    with open(alvo, "w", encoding="ascii", newline="\n") as fh:
        fh.write(CABECALHO + "\n".join(linhas) + "\n")
    print(f"{alvo.name}: {len(linhas)} pacotes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
