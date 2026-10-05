# De Lá Pra Cá

Plugin de pós-produção para Avid Media Composer e DaVinci Resolve Studio: leva a timeline de
um programa para o outro e religa a mídia original de câmera.

## Instalar

Baixe o instalador na página de versões (Releases) deste repositório. Ele traz tudo: não é preciso
instalar Python, ffmpeg nem nada mais.

- Windows: o arquivo `.exe`. O instalador não tem assinatura digital, então o Windows mostra o aviso
  do SmartScreen: clique em "Mais informações" e depois em "Executar assim mesmo".
- Mac (Apple Silicon, M1 ou mais novo): o arquivo `.dmg`. Abra-o e arraste o De Lá Pra Cá para Aplicativos.
  O app não tem certificado da Apple, então a primeira abertura é bloqueada ("Não Foi Aberto"): clique
  em OK, abra Ajustes do Sistema › Privacidade e Segurança, role até o fim e clique em "Abrir Mesmo
  Assim". Só na primeira vez.

Depois de instalar, abra o De Lá Pra Cá e instale os plugins em Configurações › Integrações, com o
programa de edição fechado.

Precisa de: Windows 10/11 ou macOS (testado no 15), Avid Media Composer 25.6+ e/ou DaVinci Resolve
Studio 19+.

## Rodar a partir do código

Precisa de Python 3.12+, ffmpeg/ffprobe no PATH e Rust (para compilar o aplicativo em `app/`).

Windows: `.\dev.ps1` · macOS: `./dev.sh`

Instalar o painel no Media Composer: `python tools/pack.py`

## Licença

GPL-3.0 — ver `LICENSE`.

Idealizado por Lohan Costa, edt. Desenvolvido com Claude Code.
