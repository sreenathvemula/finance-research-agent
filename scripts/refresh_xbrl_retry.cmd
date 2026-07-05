@echo off
rem One-shot retry of the NSE XBRL refresh (auto-scheduled after NSE throttling).
rem Skips the 1,246 symbols already fetched (no --force). Self-deletes its task.
cd /d C:\path\to\finance-research-agent
echo ==== retry started %date% %time% ==== >> data\index\xbrl_retry.log
"C:\path\to\conda\envs\finance-ai\python.exe" scripts\02_nse_xbrl_quarterly.py --all >> data\index\xbrl_retry.log 2>&1
"C:\path\to\conda\envs\finance-ai\python.exe" scripts\29_xbrl_to_md.py --all >> data\index\xbrl_retry.log 2>&1
echo ==== retry finished %date% %time% ==== >> data\index\xbrl_retry.log
schtasks /Delete /TN "FinanceXBRLRetry" /F
