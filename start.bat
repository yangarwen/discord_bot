@echo off
REM ====================================================
REM  Discord Roleplay Bot 一鍵啟動
REM  在檔案總管裡對這個檔案點兩下即可執行
REM  關閉：在跳出的黑色視窗按 Ctrl+C，或直接關掉視窗
REM ====================================================
chcp 65001 >nul
cd /d "%~dp0"

REM 先關掉任何還在跑的舊 bot 實例，避免多開搶同一個 Token 導致互動「未回應」
echo 檢查並關閉殘留的舊 bot...
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter ""Name='python.exe'"" | Where-Object { $_.CommandLine -like '*bot.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" 2>nul

echo 啟動 Discord Roleplay Bot...
echo（要關閉 bot：按 Ctrl+C 或關掉這個視窗）
echo.
".venv\Scripts\python.exe" bot.py
echo.
echo Bot 已停止。按任意鍵關閉視窗。
pause >nul
