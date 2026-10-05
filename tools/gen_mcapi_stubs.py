from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
PROTO = RAIZ / "avid" / "proto" / "mcapi_types.proto"


def main() -> int:
    if not PROTO.exists():
        print(f"proto não encontrado: {PROTO}", file=sys.stderr)
        return 1
    cmd = [sys.executable, "-m", "grpc_tools.protoc", f"-I{RAIZ}",
           f"--python_out={RAIZ}", str(PROTO.relative_to(RAIZ))]
    print(" ".join(cmd))
    r = subprocess.run(cmd, cwd=RAIZ)
    if r.returncode == 0:
        saida = PROTO.with_name("mcapi_types_pb2.py")
        print(f"gerado: {saida.relative_to(RAIZ)} ({saida.stat().st_size} bytes)")
    return r.returncode


if __name__ == "__main__":
    raise SystemExit(main())
