@echo off
chcp 65001 >nul
echo 注册每天 10:00 的定时任务（任务名 AI-Daily-Radar）...
schtasks /Create /SC DAILY /TN "AI-Daily-Radar" /TR "%~dp0run_daily.bat" /ST 10:00 /F
echo.
echo 完成。查看任务：schtasks /Query /TN "AI-Daily-Radar"
echo 删除任务：  schtasks /Delete /TN "AI-Daily-Radar" /F
pause
