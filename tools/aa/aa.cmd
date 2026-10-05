@echo off
if defined AA_PYTHON (
  "%AA_PYTHON%" "%~dp0aa.py" %*
) else (
  where py >nul 2>nul
  if errorlevel 1 (
    python "%~dp0aa.py" %*
  ) else (
    py -3 "%~dp0aa.py" %*
  )
)
exit /b %errorlevel%
