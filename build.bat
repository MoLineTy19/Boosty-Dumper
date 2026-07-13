@echo off
REM ============================================================
REM  Сборка BoostyDumper в один .exe с графическим интерфейсом.
REM  Требования:
REM    pip install -r requirements.txt
REM    pip install -r requirements-build.txt
REM  Результат: dist\BoostyDumper.exe
REM ============================================================
setlocal

echo [1/3] Очистка предыдущей сборки...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/3] Запуск PyInstaller...
pyinstaller boosty_dumper.spec --noconfirm
if errorlevel 1 (
    echo [ОШИБКА] Сборка не удалась.
    exit /b 1
)

echo [3/3] Готово.
echo   ^(^^^) Файл: dist\BoostyDumper.exe
endlocal
