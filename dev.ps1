param(
    [switch]$Pack,
    [switch]$Limpo
)

$ErrorActionPreference = "Continue"

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"

function Info($m) { Write-Host "> $m"        -ForegroundColor Cyan }
function Ok($m)   { Write-Host "OK $m"       -ForegroundColor Green }
function Aviso($m){ Write-Host "!  $m"       -ForegroundColor Yellow }
function Nota($m) { Write-Host "   $m"       -ForegroundColor DarkGray }
function Morre($m){ Write-Host "X  ERRO: $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "+--------------------------------------+"
Write-Host "|      De La Pra Ca - Dev Mode         |"
Write-Host "|      Media Composer (Windows)        |"
Write-Host "+--------------------------------------+"
Write-Host ""

$RAIZ = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RAIZ
Info "Diretório: $RAIZ"

$VENV   = Join-Path $RAIZ ".venv-win"
$PY     = Join-Path $VENV "Scripts\python.exe"
$PORTA  = 7823

$LANCADOR = $null
foreach ($v in @("3.12", "3.13")) {
    $null = (& py "-$v" -c "import sys" 2>&1)
    if ($LASTEXITCODE -eq 0) { $LANCADOR = $v; break }
}
if (-not $LANCADOR) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { Morre "Python 3.12+ não encontrado. Instale de python.org (marque 'Add to PATH')." }
    $v = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
    if ([version]$v -lt [version]"3.12") { Morre "Python $v é antigo demais — preciso de 3.12+ (o numpy do requirements.lock não instala no 3.11)." }
    $LANCADOR = $null
    Ok "Python $v (do PATH)"
} else {
    Ok "Python $LANCADOR (via launcher py)"
}

$ff = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ff) {
    Ok "ffmpeg ($($ff.Source))"
} else {
    Aviso "ffmpeg não está no PATH."
    Nota "O relink não precisa dele hoje (o matcher não abre mídia), mas os"
    Nota "writers de saída precisam.  winget install Gyan.FFmpeg"
}

if ($Limpo -and (Test-Path $VENV)) {
    Info "Removendo o venv antigo..."
    Remove-Item -Recurse -Force $VENV
}

$venvOk = $false
if (Test-Path $PY) {
    & $PY -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $venvOk = $true }
    else { Aviso "o .venv-win existe mas não executa ou não é 3.12+ — recriando"; Remove-Item -Recurse -Force $VENV }
}

$venvNovo = $false
if (-not $venvOk) {
    Info "Criando o venv do Windows (.venv-win)..."
    if ($LANCADOR) { & py "-$LANCADOR" -m venv --copies $VENV }
    else           { & python -m venv --copies $VENV }
    if (-not (Test-Path $PY)) { Morre "não consegui criar o venv em $VENV" }
    $venvNovo = $true
    Ok "venv criado"
}

$CARIMBO = Join-Path $VENV ".deps-instaladas"
$precisa = $venvNovo -or (-not (Test-Path $CARIMBO))
if (-not $precisa) {
    $req = Get-Item (Join-Path $RAIZ "requirements.lock")
    $car = Get-Item $CARIMBO
    if ($req.LastWriteTime -gt $car.LastWriteTime) { $precisa = $true }
}
if ($precisa) {
    Info "Instalando dependências Python..."
    & $PY -m pip install --quiet -r (Join-Path $RAIZ "requirements.lock")
    if ($LASTEXITCODE -ne 0) { Morre "pip falhou" }
    New-Item -ItemType File -Path $CARIMBO -Force | Out-Null
    Ok "Dependências OK"
} else {
    Ok "Dependências já instaladas"
}

$AVPI = Join-Path $env:PROGRAMDATA "Avid\PanelSDKPlugins\com.ciclomedia.delapraca.mc.avpi"
$VER  = "?"
if (Test-Path (Join-Path $RAIZ "VERSION")) { $VER = (Get-Content (Join-Path $RAIZ "VERSION") -Raw).Trim() }

if ($Pack) {
    Info "Empacotando e instalando o painel..."
    & $PY (Join-Path $RAIZ "tools\pack.py")
    if ($LASTEXITCODE -ne 0) { Morre "pack.py falhou" }
    $VER = (Get-Content (Join-Path $RAIZ "VERSION") -Raw).Trim()
    Ok "Painel instalado — v$VER"
    Nota "Painel já aberto? botão direito na área vazia -> Reload"
    Nota "Painel novo? feche o MC, rode 'python tools\reload.py', reabra"
} elseif (Test-Path $AVPI) {
    Ok "Painel instalado (v$VER)"
} else {
    Aviso "O painel .avpi ainda não está instalado."
    Nota "Rode: .\dev.ps1 -Pack"
}

$ocupada = Get-NetTCPConnection -LocalPort $PORTA -State Listen -ErrorAction SilentlyContinue
if ($ocupada) {
    $pidDono = ($ocupada | Select-Object -First 1).OwningProcess
    Aviso "A porta $PORTA já está ocupada (pid $pidDono) — outra instância do serviço?"
    $r = Read-Host "  Encerrar aquele processo e continuar? [s/N]"
    if ($r -match '^[sSyY]') {
        Stop-Process -Id $pidDono -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
        Ok "Encerrado"
    } else {
        Morre "porta ocupada — encerre o outro serviço e tente de novo"
    }
}

$mc = Get-Process -Name "AvidMediaComposer" -ErrorAction SilentlyContinue
if ($mc) {
    Ok "Media Composer está aberto"
} else {
    Aviso "Media Composer fechado — o painel vai abrir, mas sem nada para dirigir."
    $orfaos = & $PY -c "import sys; sys.path.insert(0, r'$RAIZ\plugins\media-composer\servico'); import plataforma; print(','.join(map(str, plataforma.gateways_orfaos())))"
    if ($orfaos.Trim()) {
        Aviso "Há avid-api-gateway ÓRFÃO rodando (pid $orfaos)."
        Nota "Ele segura as portas do Panel SDK e faz o painel sumir do menu."
        Nota "Rode: python tools\reload.py"
    }
}

Write-Host ""
Write-Host "Tudo pronto. Subindo o serviço..." -ForegroundColor Green
Write-Host "  painel:  http://127.0.0.1:$PORTA/painel" -ForegroundColor Cyan
Write-Host "  no MC:   Tools > De La Pra Ca" -ForegroundColor Cyan
Write-Host "  (Ctrl+C para encerrar)" -ForegroundColor DarkGray
Write-Host ""

$env:PYTHONPATH = "$RAIZ\motor;$RAIZ\plugins\media-composer\servico"
if (-not $env:DLPC_LOG) { $env:DLPC_LOG = "info" }
& $PY (Join-Path $RAIZ "plugins\media-composer\servico\main.py") --bancada --log $env:DLPC_LOG
