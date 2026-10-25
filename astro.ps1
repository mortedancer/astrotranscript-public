# Windows install and process manager for AstroTranscript.
# Run:  astro.cmd install | start | stop | restart | status | console | logs | open
# Optional machine file astro.local.ps1 (not committed):
#   $AstroPython = "C:\Path\python.exe"
#   $AstroDataRoot = "D:\recordings"

param(
    [Parameter(Position = 0)]
    [string]$Command = "help",
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"
try { & chcp.com 65001 | Out-Null } catch {}
$OutputEncoding = [System.Text.Encoding]::UTF8
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$StateDir = Join-Path $Root ".astro"
$PidFile = Join-Path $StateDir "server.pid"
$OutLog = Join-Path $StateDir "server.out.log"
$ErrLog = Join-Path $StateDir "server.err.log"
$ListenHost = "127.0.0.1"
$Port = 8787
$Url = "http://${ListenHost}:${Port}"

$local = Join-Path $Root "astro.local.ps1"
if (Test-Path -LiteralPath $local) {
    . $local
}

function Say([string]$Text) {
    Write-Host $Text
}

function Fail([string]$Text) {
    Write-Host $Text
    exit 1
}

function Get-DataRoot {
    if ($env:ASTRO_DATA_ROOT) { return $env:ASTRO_DATA_ROOT }
    if ($script:AstroDataRoot) { return [string]$script:AstroDataRoot }
    return (Join-Path $Root "data")
}

function Resolve-PythonExe([string]$Hint) {
    if (-not $Hint) { return $null }
    if (Test-Path -LiteralPath $Hint) {
        return (Resolve-Path -LiteralPath $Hint).Path
    }
    return $null
}

function Resolve-BasePython {
    $hit = Resolve-PythonExe $env:ASTRO_PYTHON
    if (-not $hit) { $hit = Resolve-PythonExe $script:AstroPython }
    if ($hit) { return $hit }
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $exe = & py -3 -c "import sys; print(sys.executable)"
        if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.Trim() }
    }
    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) { return $python.Source }
    return $null
}

function Resolve-RunPython {
    $venv = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venv) { return (Resolve-Path -LiteralPath $venv).Path }
    return Resolve-BasePython
}

function Assert-Python310([string]$Exe) {
    & $Exe -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    if ($LASTEXITCODE -ne 0) {
        Fail "Нужен Python 3.10 или новее. Сейчас: $Exe"
    }
}

function Get-FfmpegPath {
    if ($env:ASTRO_FFMPEG -and (Test-Path -LiteralPath $env:ASTRO_FFMPEG)) {
        return (Resolve-Path -LiteralPath $env:ASTRO_FFMPEG).Path
    }
    $cmd = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Get-ListenerPids {
    $found = @()
    try {
        $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    } catch {
        $conns = @()
    }
    foreach ($conn in $conns) {
        $procId = [int]$conn.OwningProcess
        if ($found -contains $procId) { continue }
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$procId" -ErrorAction SilentlyContinue
        $line = [string]$proc.CommandLine
        if ($line -match "uvicorn" -and $line -match "backend\.app\.main") {
            $found += $procId
        }
    }
    return $found
}

function Test-Healthy {
    try {
        $health = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 2
        if ($health.ok) { return $health }
    } catch {
        return $null
    }
    return $null
}

function Show-LogTail([int]$Lines) {
    foreach ($file in @($OutLog, $ErrLog)) {
        if (-not (Test-Path -LiteralPath $file)) { continue }
        Say "---- $(Split-Path -Leaf $file) ----"
        Get-Content -LiteralPath $file -Tail $Lines -ErrorAction SilentlyContinue
    }
}

function Install-Astro {
    if (-not (Test-Path -LiteralPath (Join-Path $Root "backend\requirements.txt"))) {
        Fail "Запустите скрипт из каталога AstroTranscript."
    }
    $base = Resolve-BasePython
    if (-not $base) { Fail "Python не найден. Поставьте 3.10+ или укажите `$AstroPython в astro.local.ps1." }
    Assert-Python310 $base
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPy)) {
        Say "Создаю окружение .venv"
        & $base -m venv (Join-Path $Root ".venv")
        if ($LASTEXITCODE -ne 0) { Fail "Не удалось создать .venv" }
    }
    Say "Ставлю зависимости"
    & $venvPy -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { Fail "pip не обновился" }
    & $venvPy -m pip install -r (Join-Path $Root "backend\requirements.txt")
    if ($LASTEXITCODE -ne 0) { Fail "Не удалось поставить зависимости" }
    $ffmpeg = Get-FfmpegPath
    if ($ffmpeg) { Say "ffmpeg: $ffmpeg" }
    else { Say "ffmpeg не найден. Обработка и длинные волны потребуют ffmpeg в PATH или ASTRO_FFMPEG." }
    Say "Готово. Дальше: astro.cmd start"
}

function Start-Astro([switch]$Foreground) {
    $py = Resolve-RunPython
    if (-not $py) { Fail "Python не найден. Сначала выполните astro.cmd install" }
    Assert-Python310 $py
    $healthy = Test-Healthy
    if ($healthy) {
        Say "Уже запущен: $Url"
        Say "Данные: $($healthy.data_root)"
        return
    }
    $listeners = @(Get-ListenerPids)
    if ($listeners.Count -gt 0) {
        Fail "Порт $Port занят процессом $($listeners -join ', '), но /api/health не отвечает. astro.cmd stop или astro.cmd logs"
    }
    $data = Get-DataRoot
    New-Item -ItemType Directory -Force -Path $data, $StateDir | Out-Null
    if ($Foreground) {
        Say "AstroTranscript  $Url"
        Say "Данные: $data"
        $env:PYTHONPATH = $Root
        Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
        $env:ASTRO_DATA_ROOT = $data
        & $py -m uvicorn backend.app.main:app --host $ListenHost --port $Port
        exit $LASTEXITCODE
    }
    $savedPath = $env:PYTHONPATH
    $savedHome = $env:PYTHONHOME
    $hadHome = Test-Path Env:PYTHONHOME
    $savedRoot = $env:ASTRO_DATA_ROOT
    try {
        $env:PYTHONPATH = $Root
        Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue
        $env:ASTRO_DATA_ROOT = $data
        $proc = Start-Process -FilePath $py `
            -ArgumentList @("-m", "uvicorn", "backend.app.main:app", "--host", $ListenHost, "--port", "$Port") `
            -WorkingDirectory $Root `
            -WindowStyle Hidden `
            -RedirectStandardOutput $OutLog `
            -RedirectStandardError $ErrLog `
            -PassThru
    } finally {
        $env:PYTHONPATH = $savedPath
        if ($hadHome) { $env:PYTHONHOME = $savedHome } else { Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue }
        if ($null -eq $savedRoot) { Remove-Item Env:ASTRO_DATA_ROOT -ErrorAction SilentlyContinue } else { $env:ASTRO_DATA_ROOT = $savedRoot }
    }
    Set-Content -LiteralPath $PidFile -Value $proc.Id -Encoding ascii
    $deadline = (Get-Date).AddSeconds(25)
    do {
        $healthy = Test-Healthy
        if ($healthy) { break }
        if ($proc.HasExited) { break }
        Start-Sleep -Milliseconds 400
    } while ((Get-Date) -lt $deadline)
    if (-not $healthy) {
        Say "Сервер не ответил на $Url/api/health"
        Show-LogTail 40
        exit 1
    }
    Say "Запущен: $Url"
    Say "Данные: $($healthy.data_root)"
    Say "pid $($proc.Id)"
}

function Stop-Astro {
    $stopped = $false
    if (Test-Path -LiteralPath $PidFile) {
        $raw = (Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        $procId = 0
        if ([int]::TryParse([string]$raw, [ref]$procId) -and $procId -gt 0) {
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($proc) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                $stopped = $true
            }
        }
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    }
    foreach ($procId in @(Get-ListenerPids)) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        $stopped = $true
    }
    if ($stopped) { Say "Остановлен" }
    else { Say "Сервер не запущен" }
}

function Show-Status {
    $data = Get-DataRoot
    $py = Resolve-RunPython
    $ffmpeg = Get-FfmpegPath
    Say "Каталог: $Root"
    Say "Данные:  $data"
    if ($py) { Say "Python:  $py" } else { Say "Python:  не найден" }
    if ($ffmpeg) { Say "ffmpeg:  $ffmpeg" } else { Say "ffmpeg:  не найден" }
    $healthy = Test-Healthy
    if ($healthy) {
        Say "Состояние: запущен  $Url"
        Say "Корень данных с сервера: $($healthy.data_root)"
        exit 0
    }
    $listeners = @(Get-ListenerPids)
    if ($listeners.Count -gt 0) {
        Say "Состояние: порт $Port занят (pid $($listeners -join ', ')), health не отвечает"
        exit 1
    }
    Say "Состояние: остановлен"
    exit 1
}

switch ($Command.ToLowerInvariant()) {
    "install" { Install-Astro }
    "start" { Start-Astro }
    "stop" { Stop-Astro }
    "restart" { Stop-Astro; Start-Astro }
    "status" { Show-Status }
    "console" { Start-Astro -Foreground }
    "logs" {
        $lines = 80
        if ($Rest -and $Rest[0] -match "^\d+$") { $lines = [int]$Rest[0] }
        if (-not (Test-Path -LiteralPath $OutLog) -and -not (Test-Path -LiteralPath $ErrLog)) {
            Say "Журнала ещё нет. Он появится после astro.cmd start"
            exit 0
        }
        if ($Rest -contains "follow" -or $Rest -contains "-f") {
            Get-Content -LiteralPath @($OutLog, $ErrLog) -Tail $lines -Wait -ErrorAction SilentlyContinue
        } else {
            Show-LogTail $lines
        }
    }
    "open" { Start-Process $Url }
    default {
        Say @"
Управление AstroTranscript на Windows.

  astro.cmd install    создать .venv и поставить зависимости
  astro.cmd start      запустить сервер в фоне
  astro.cmd stop       остановить сервер
  astro.cmd restart    перезапустить
  astro.cmd status     проверить процесс и /api/health
  astro.cmd console    запустить в этом окне
  astro.cmd logs       последние строки журнала
  astro.cmd logs follow
  astro.cmd open       открыть $Url

Каталог данных: переменная ASTRO_DATA_ROOT, либо `$AstroDataRoot в astro.local.ps1,
иначе папка data рядом со скриптом.
Python: ASTRO_PYTHON, либо `$AstroPython в astro.local.ps1, иначе .venv, py -3 или python.
"@
        if ($Command -and $Command.ToLowerInvariant() -ne "help") { exit 1 }
    }
}
