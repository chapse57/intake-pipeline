@echo off
cd /d "%~dp0"
if exist .git\packed-refs.lock del .git\packed-refs.lock
set PY=python
where python >nul 2>&1 || set PY=py
echo.
echo [1/3] Running the offline tests on this PC
%PY% -m pip install -q pytest >nul 2>&1
%PY% -m pytest -m offline -q -p no:cacheprovider
if errorlevel 1 (echo. & echo Tests failed - nothing was committed. Send a screenshot. & pause & exit /b 1)
echo.
echo [2/3] Files going up - secrets in deploy\*.env stay on this PC
git add -A
git status --short
echo.
echo [3/3] Commit and push
git commit -F .git\COMMIT_MSG_DOMAIN
git push
echo.
echo Done. Send a screenshot of this window.
pause
