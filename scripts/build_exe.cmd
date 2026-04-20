@echo off
REM OpsNavHelper.exe 빌드 스크립트 (Windows 전용)
REM 사전 조건: pip install playwright==1.49.1 pyinstaller

setlocal
cd /d "%~dp0"

echo ==========================================
echo OpsNavHelper.exe 빌드
echo ==========================================
echo.

REM 기존 빌드 산출물 정리
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

pyinstaller OpsNavHelper.spec
if errorlevel 1 goto error

echo.
echo ==========================================
echo 빌드 완료.
echo.
echo 산출물:
dir /b dist\*.exe
echo.
echo 크기:
for %%F in (dist\*.exe) do echo   %%~zF bytes
echo ==========================================
echo.
exit /b 0

:error
echo.
echo ==========================================
echo 빌드 실패. 위 메시지 확인.
echo ==========================================
exit /b 1
