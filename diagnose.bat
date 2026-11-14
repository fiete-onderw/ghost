@echo off
rem ---------------------------------------------------------------------
rem  Ghost-diagnose maken met dubbelklikken.
rem
rem  Dit bestand doet drie dingen:
rem    1. Naar de map van Ghost gaan (de map waar dit bestand in staat).
rem    2. Het hulpprogramma "uv" zoeken. Dat installeert de onderdelen en
rem       start Python. Staat uv niet op deze laptop, dan haalt Ghost zelf
rem       een vaste, gecontroleerde versie op (scripts\installeer-uv.ps1).
rem    3. Een diagnose maken: een tekstbestand dat je kunt opsturen als Ghost
rem       bij jou niet goed werkt. Er staat geen naam, serienummer of exacte
rem       locatie in.
rem ---------------------------------------------------------------------
setlocal
title Ghost - diagnose
cd /d "%~dp0"
if not exist "%~dp0pyproject.toml" goto niet_uitgepakt

rem --- uv zoeken: eerst de gewone manier, daarna de standaardmap ---
set "UV=uv"
where uv >nul 2>nul
if not errorlevel 1 goto start
if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if exist "%USERPROFILE%\.local\bin\uv.exe" goto start

rem --- Geen uv gevonden: Ghost haalt zelf een vaste, gecontroleerde versie op ---
rem     Moet gelijk zijn aan $UvVersion in scripts\installeer-uv.ps1.
set "GHOST_UV_VERSION=0.11.32"
set "UV=%LOCALAPPDATA%\Ghost\tools\uv-%GHOST_UV_VERSION%\uv.exe"
rem     Python en de download-cache van deze uv ook in de Ghost-map houden.
set "UV_PYTHON_INSTALL_DIR=%LOCALAPPDATA%\Ghost\python"
set "UV_CACHE_DIR=%LOCALAPPDATA%\Ghost\cache"
if exist "%UV%" goto start
echo.
echo  Eenmalig: Ghost haalt het hulpprogramma uv op.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\installeer-uv.ps1"
if errorlevel 1 goto geen_uv
if exist "%UV%" goto start
goto geen_uv

:start
echo.
echo  Ghost maakt een diagnose...
echo.
echo  De allereerste keer kan dit een paar minuten duren: dan worden de
echo  onderdelen gedownload. Daarna start Ghost binnen een paar seconden.
echo.

"%UV%" run --frozen python -m ghost diagnose
set "GHOST_CODE=%errorlevel%"
set "GHOST_REPORT=%LOCALAPPDATA%\Ghost\logs\ghost-diagnose.txt"
if exist "%GHOST_REPORT%" explorer /select,"%GHOST_REPORT%"

echo.
if "%GHOST_CODE%"=="0" goto klaar
echo  Ghost is gestopt met een melding (code %GHOST_CODE%). Lees de tekst hierboven.
:klaar
echo  Druk op een toets om dit venster te sluiten.
pause >nul
exit /b %GHOST_CODE%

:geen_uv
echo.
echo  Ghost kon het hulpprogramma "uv" niet ophalen. Lees de melding hierboven.
echo.
echo  Zo los je het op:
echo    1. Controleer of je internet hebt.
echo    2. Dubbelklik opnieuw op dit bestand.
echo.
echo  Lukt het dan nog niet? Installeer uv zelf: open PowerShell en plak deze regel:
echo         powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
echo  Herstart daarna je laptop en dubbelklik opnieuw op dit bestand.
echo.
echo  Druk op een toets om dit venster te sluiten.
pause >nul
exit /b 1

:niet_uitgepakt
echo.
echo  Ghost kan zo niet starten: de ZIP is nog niet uitgepakt.
echo  Klik met de rechtermuisknop op het ZIP-bestand en kies "Alles uitpakken".
echo  Dubbelklik daarna op dit bestand in de uitgepakte map.
echo.
echo  Druk op een toets om dit venster te sluiten.
pause >nul
exit /b 1
