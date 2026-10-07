@echo off
REM Lightweight Windows build: produces dist\pyfa\pyfa.exe (no Inno Setup installer).
REM Requires: Python 3.12+ x64 on PATH, run from repo root OR double-click from Explorer.
REM Dependencies come from pyproject.toml / uv.lock via uv (installed with pip if missing).
REM Optional: Windows 10 SDK UCRT path in pyfa.spec pathex; CROWDIN_API_KEY for progress.json.

setlocal
pushd "%~dp0..\.."

echo === pyfa: ensure uv ===
where uv >nul 2>&1
if errorlevel 1 (
  echo uv not found, installing via pip...
  python -m pip install --upgrade uv
  if errorlevel 1 goto :fail
)

echo === pyfa: sync locked dependencies ===
uv sync --frozen --no-default-groups --group packaging --group wx-binary
if errorlevel 1 goto :fail

echo === compile_lang ===
uv run --no-sync python scripts\compile_lang.py
if errorlevel 1 goto :fail

if defined CROWDIN_API_KEY (
  echo === dump_crowdin_progress ===
  uv run --no-sync python scripts\dump_crowdin_progress.py
  if errorlevel 1 goto :fail
) else (
  echo === skip dump_crowdin_progress (set CROWDIN_API_KEY to update locale\progress.json^) ===
)

echo === db_update ===
uv run --no-sync python db_update.py
if errorlevel 1 goto :fail

echo === PyInstaller ===
uv run --no-sync python -m PyInstaller --clean -y pyfa.spec
if errorlevel 1 goto :fail

if exist "dist\pyfa\pyfa.exe" (
  copy /y "dist_assets\win\pyfa.exe.manifest" "dist\pyfa\pyfa.exe.manifest" >nul
)

echo.
echo Build OK. Run:  dist\pyfa\pyfa.exe
popd
endlocal
exit /b 0

:fail
echo Build failed.
popd
endlocal
exit /b 1
