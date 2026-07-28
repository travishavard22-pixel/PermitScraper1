@echo off
REM ---------------------------------------------------------------
REM Local run WITHOUT the Apify CLI. Usage:
REM     run.bat            -> uses whatever is in storage\...\INPUT.json
REM     run.bat austin     -> loads inputs\austin.json first
REM     run.bat houston    -> loads inputs\houston.json first
REM ---------------------------------------------------------------
setlocal
set STORE=storage\key_value_stores\default

if not "%~1"=="" (
  if not exist "inputs\%~1.json" (
    echo ERROR: inputs\%~1.json not found.  Available:
    dir /b inputs
    exit /b 1
  )
  if not exist "%STORE%" mkdir "%STORE%"
  REM Only ONE INPUT file may exist in the store or the SDK refuses to start.
  del /q "%STORE%\INPUT*.json" 2>nul
  copy /y "inputs\%~1.json" "%STORE%\INPUT.json" >nul
  echo Using input: inputs\%~1.json
)

set APIFY_LOCAL_STORAGE_DIR=%CD%\storage
set CRAWLEE_STORAGE_DIR=%CD%\storage
python -m src
endlocal
