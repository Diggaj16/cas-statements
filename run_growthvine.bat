@echo off
echo ===================================================
echo Starting Growthvine Financial Dashboard...
echo ===================================================
echo.

echo [1/2] Starting FastAPI Backend...
start "Growthvine Backend (FastAPI)" cmd /k "uvicorn main:app --reload"

echo [2/2] Starting React Frontend...
start "Growthvine Frontend (React)" cmd /k "cd frontend && npm run dev"

echo.
echo Application is launching!
echo.
echo - The Backend API will be running at: http://localhost:8000
echo - The Frontend UI will be running at: http://localhost:5173
echo.
echo You can close this window. To stop the application, simply close the two new windows that opened.
pause
