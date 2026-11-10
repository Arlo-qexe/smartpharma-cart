@echo off
rem Inicia el simulador del SPM en Windows. Uso: startspm.bat [--sin-gui] [--lock] [--test]
rem   --lock  al crear .venv instala las versiones exactas de requirements-lock.txt
rem   --test  corre las pruebas en vez de abrir la interfaz
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set GUI=1
set LOCK=0
set TEST=0
:args
if "%~1"=="" goto fin_args
if /i "%~1"=="--sin-gui" set GUI=0
if /i "%~1"=="--lock" set LOCK=1
if /i "%~1"=="--test" set TEST=1
shift
goto args
:fin_args

if exist ".venv\Scripts\python.exe" goto venv_listo
echo == creando .venv ==
where py >nul 2>nul
if errorlevel 1 goto crear_python
py -3 -m venv .venv
goto venv_creado
:crear_python
python -m venv .venv
:venv_creado
if exist ".venv\Scripts\python.exe" goto instalar
echo ERROR: no se pudo crear .venv. Instala Python 3.11 o superior desde python.org marcando "tcl/tk and IDLE" y "py launcher".
exit /b 1

:instalar
set REQ=requirements.txt
if "%LOCK%"=="1" set REQ=requirements-lock.txt
echo == instalando %REQ% ==
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -r "%REQ%"
if errorlevel 1 goto fallo_pip
goto venv_listo
:fallo_pip
rmdir /s /q .venv
echo ERROR: fallo pip install. Si usaste --lock y tu Python es de otra version, prueba sin --lock.
exit /b 1

:venv_listo
".venv\Scripts\python.exe" -c "import tkinter" 2>nul
if errorlevel 1 goto sin_tk
if not exist salida mkdir salida
if "%TEST%"=="1" goto pruebas
echo == cinematica de muestra ==
".venv\Scripts\python.exe" -m spm ik --inclinacion 12 --azimut 40 --torsion 5
if "%GUI%"=="1" ".venv\Scripts\python.exe" interfaz_spm.py
exit /b 0

:pruebas
".venv\Scripts\python.exe" -m unittest discover -s tests
exit /b %errorlevel%

:sin_tk
echo ERROR: falta tkinter. Reinstala Python marcando "tcl/tk and IDLE".
exit /b 1
