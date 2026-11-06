@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo ==========================================
echo   餐厅系统 - 一键发布到服务器
echo ==========================================
echo.
git add -A
git commit -m "更新于 %date:~0,10% %time:~0,5%" 2>nul || echo [提示] 没有新改动，跳过提交。
echo.
git push server main
echo.
echo 推送成功！服务器已自动更新代码并重启后端，
echo 约 3 秒后访问 http://8.155.129.25/ 即为最新版本。
echo.
pause
