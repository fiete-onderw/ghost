# Haalt het hulpprogramma "uv" op als het nog niet op deze laptop staat.
# Wordt aangeroepen door start.bat en start-demo.bat; je hoeft dit niet zelf te openen.
#
# uv installeert Python en de onderdelen van Ghost (vastgelegd in uv.lock) en start Ghost.
#
# Wat dit script doet:
#   1. uv downloaden van de officiele GitHub-pagina van uv, in een vaste versie.
#   2. Het bestand controleren met een vaste SHA256-controlecode. Klopt die niet,
#      dan wordt het bestand verwijderd en gaat er niets verder.
#   3. uv uitpakken in %LOCALAPPDATA%\Ghost\tools.
#
# Er wordt niets aan de rest van Windows veranderd: geen PATH, geen administratorrechten.
# Verwijderen: gooi %LOCALAPPDATA%\Ghost weg.
#
# LET OP: de versie hieronder moet gelijk zijn aan GHOST_UV_VERSION in start.bat
# en start-demo.bat. Een test (tests/test_start_scripts.py) controleert dat.
#
# Dit bestand bevat bewust alleen gewone letters (geen e-trema enz.), omdat
# Windows PowerShell 5.1 andere tekens soms verkeerd leest.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # maakt downloaden in PowerShell 5.1 veel sneller

$UvVersion = '0.11.32'
# Controlecodes van de officiele uv-downloads (uit de .sha256-bestanden op GitHub).
$UvHashes = @{
    'x86_64'  = 'acfde570451cfdb8689fa159a138ee805ba4e241c466432750302c86254b0984'
    'aarch64' = 'a7427ea0440bb826b6716d1837ff3d173b8e7d496cb09ee8f456b4e023a2fdcd'
}

function Say([string]$Text = '') { Write-Host $Text }
function Fail([string]$Text) {
    Write-Host ''
    Write-Host $Text -ForegroundColor Red
    exit 1
}

try {
    $Arch = $env:PROCESSOR_ARCHITEW6432
    if (-not $Arch) { $Arch = $env:PROCESSOR_ARCHITECTURE }
    if ($Arch -eq 'AMD64') {
        $UvArch = 'x86_64'
    } elseif ($Arch -eq 'ARM64') {
        $UvArch = 'aarch64'
        Write-Host 'Let op: dit is Windows op ARM. Ghost is daarop nog niet getest.' -ForegroundColor Yellow
    } else {
        Fail "Ghost werkt alleen op 64-bit Windows (gevonden: $Arch)."
    }

    $GhostData = Join-Path $env:LOCALAPPDATA 'Ghost'
    $ToolsDir = Join-Path $GhostData "tools\uv-$UvVersion"
    $UvExe = Join-Path $ToolsDir 'uv.exe'

    if (Test-Path $UvExe) { exit 0 }   # al eerder opgehaald

        Say "Eenmalig: hulpprogramma uv $UvVersion downloaden (ongeveer 25 MB)..."
        $Url = "https://github.com/astral-sh/uv/releases/download/$UvVersion/uv-$UvArch-pc-windows-msvc.zip"
        New-Item -ItemType Directory -Force -Path (Join-Path $GhostData 'tools') | Out-Null
        $Zip = Join-Path $GhostData "tools\uv-$UvVersion-download.zip"
        $Unpack = "$ToolsDir.uitpakken"
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $Url -OutFile $Zip -UseBasicParsing
        } catch {
            Fail "Downloaden van uv is mislukt. Controleer je internetverbinding en probeer het opnieuw.`n($($_.Exception.Message))"
        }
        $Hash = (Get-FileHash -Algorithm SHA256 -Path $Zip).Hash.ToLower()
        if ($Hash -ne $UvHashes[$UvArch]) {
            Remove-Item -Force $Zip
            Fail "Veiligheidscontrole mislukt: het gedownloade bestand is niet het officiele bestand van uv. Het is verwijderd. Probeer het later opnieuw."
        }
        if (Test-Path $Unpack) { Remove-Item -Recurse -Force $Unpack }
        if (Test-Path $ToolsDir) { Remove-Item -Recurse -Force $ToolsDir }
        Expand-Archive -Path $Zip -DestinationPath $Unpack -Force
        Move-Item -Path $Unpack -Destination $ToolsDir
        Remove-Item -Force $Zip
        if (-not (Test-Path $UvExe)) { Fail 'uv is gedownload maar uv.exe ontbreekt. Probeer het opnieuw.' }
        Say 'uv is klaar (controlecode klopt).'
        Say ''

    exit 0
} catch {
    Fail "Het ophalen van uv is mislukt:`n$($_.Exception.Message)"
}
