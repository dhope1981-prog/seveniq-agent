@echo off
REM Weekly live-feedback report on closed paper trades.
REM Registered as Windows Scheduled Task "SEVENIQ Weekly Live Feedback".
echo ===== Live Feedback run %DATE% %TIME% ===== >> "C:\Users\Dustin\seveniq_agent\live_feedback_report.log"
"C:\Users\Dustin\AppData\Local\Programs\Python\Python314\python.exe" "C:\Users\Dustin\seveniq_agent\live_feedback.py" >> "C:\Users\Dustin\seveniq_agent\live_feedback_report.log" 2>&1
