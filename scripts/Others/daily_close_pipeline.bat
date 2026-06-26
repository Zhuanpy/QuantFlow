@echo off
REM ============================================================================
REM  QuantFlow 收盘后一键流水线（Windows 任务计划程序入口）
REM  下载行情(日K+1m+15m) -> 重算个股分布快照 -> /stock_stats/ 自然就最新了
REM
REM  手动跑一次（先验证）:
REM      scripts\Others\daily_close_pipeline.bat
REM
REM  注册到任务计划程序（管理员 PowerShell，建议交易日 15:30 之后，留够下载时间）:
REM      schtasks /Create /TN "QuantFlow-DailyClose" /SC WEEKLY /D MON,TUE,WED,THU,FRI ^
REM        /ST 16:00 /TR "D:\MyProject\MyStock\QuantFlow\scripts\Others\daily_close_pipeline.bat" /RL HIGHEST
REM  删除:  schtasks /Delete /TN "QuantFlow-DailyClose" /F
REM  立即测试一次:  schtasks /Run /TN "QuantFlow-DailyClose"
REM ============================================================================

setlocal
set PROJECT_DIR=D:\MyProject\MyStock\QuantFlow
set PYTHON=D:\Miniconda3\envs\QuantFlow\python.exe
set LOG_DIR=%PROJECT_DIR%\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM 用当天日期命名日志（YYYYMMDD）。Win11 已移除 WMIC，改用 PowerShell 取日期避免区域格式问题
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i
set LOG_FILE=%LOG_DIR%\daily_close_%TODAY%.log

cd /d "%PROJECT_DIR%"

echo ======================================== >> "%LOG_FILE%"
echo [%DATE% %TIME%] 开始收盘流水线 >> "%LOG_FILE%"

REM --skip-non-trading：非交易日(周末/节假日)自动跳过，避免无谓下载
REM --merge-below 5：顺带把「合并<5%小周期」去噪口径也算上（页面口径下拉用得到；不需要可删）
"%PYTHON%" scripts\Others\daily_close_pipeline.py --days 5 --skip-non-trading --merge-below 5 >> "%LOG_FILE%" 2>&1
set RC=%ERRORLEVEL%

echo [%DATE% %TIME%] 流水线结束，退出码=%RC% >> "%LOG_FILE%"
echo. >> "%LOG_FILE%"

REM 退出码: 0=全成功 2=下载异常但快照已重算 3=快照失败 4=非交易日跳过
endlocal & exit /b %RC%

REM ----------------------------------------------------------------------------
REM  备选：若直接用 env 的 python.exe 出现缺 DLL / 找不到包，改用 conda 激活后再跑：
REM      call D:\Miniconda3\Scripts\activate.bat QuantFlow
REM      python scripts\Others\daily_close_pipeline.py --days 5 --skip-non-trading >> "%LOG_FILE%" 2>&1
REM ----------------------------------------------------------------------------
