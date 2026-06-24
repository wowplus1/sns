@echo off
echo ==================================================
echo [1/3] Killing all running Python background tasks...
echo ==================================================
taskkill /F /IM python.exe 2>nul
taskkill /F /IM wscript.exe 2>nul

echo ==================================================
echo [2/3] Registering background monitoring server...
echo ==================================================
schtasks /create /tn "SNS_Monitoring_Server" /tr "wscript.exe \"%USERPROFILE%\.gemini\sns\run_server.vbs\"" /sc onlogon /rl highest /f

echo ==================================================
echo [3/3] Starting server instance...
echo ==================================================
schtasks /run /tn "SNS_Monitoring_Server"

echo ==================================================
echo Registration and clean restart completed successfully!
echo ==================================================
pause
