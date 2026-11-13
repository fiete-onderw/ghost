@echo off
rem ---------------------------------------------------------------------
rem  Ghost starten met dubbelklikken.
rem
rem  Dit bestand doet drie dingen:
rem    1. Naar de map van Ghost gaan (de map waar dit bestand in staat).
rem    2. Het hulpprogramma "uv" zoeken. Dat installeert de onderdelen en
rem       start Python.
rem    3. Ghost starten. De browser gaat daarna vanzelf open.
rem ---------------------------------------------------------------------
setlocal
title Ghost - laat dit venster open zolang je Ghost gebruikt
cd /d "%~dp0"

rem --- uv zoeken: eerst de gewone manier, daarna de standaardmap ---
set "UV=uv"
where uv >nul 2>nul
if not errorlevel 1 goto start
if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if exist "%USERPROFILE%\.local\bin\uv.exe" goto start
goto geen_uv

:start
echo.
echo  Ghost wordt gestart...
echo.
echo  De allereerste keer kan dit een paar minuten duren: dan worden de
echo  onderdelen gedownload. Daarna start Ghost binnen een paar seconden.
echo.

"%UV%" run --frozen python -m ghost web %*
set "GHOST_CODE=%errorlevel%"

echo.
if "%GHOST_CODE%"=="0" goto klaar
echo  Ghost is gestopt met een melding (code %GHOST_CODE%). Lees de tekst hierboven.
:klaar
echo  Druk op een toets om dit venster te sluiten.
pause >nul
exit /b %GHOST_CODE%

:geen_uv
echo.
echo  Het hulpprogramma "uv" is niet gevonden.
echo.
echo  Zo los je het op:
echo    1. Open PowerShell en plak deze regel:
echo         powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
echo    2. Sluit PowerShell en dit venster.
echo    3. Dubbelklik opnieuw op dit bestand.
echo.
echo  Werkt uv wel in PowerShell maar hier niet? Herstart dan je laptop.
echo.
echo  Druk op een toets om dit venster te sluiten.
pause >nul
exit /b 1
