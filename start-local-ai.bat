@echo off
REM Starts the app together with the bundled open model (Ollama + gemma3:4b by default).
docker compose -f docker-compose.yml -f docker-compose.local-ai.yml up -d --build
if errorlevel 1 exit /b 1
echo.
if "%GRIEVANCE_PORT%"=="" (set APPPORT=8502) else (set APPPORT=%GRIEVANCE_PORT%)
echo The app is starting at http://127.0.0.1:%APPPORT%
echo The first start downloads the model (about 3.3 GB). To watch the download:
echo   docker compose -f docker-compose.yml -f docker-compose.local-ai.yml logs -f model-pull
echo When it finishes, choose the local model under "Engine" in the app.
