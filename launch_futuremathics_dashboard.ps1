# FutureMathics — Streamlit dashboard (PowerShell)
Set-Location $PSScriptRoot
$env:PYTHONPATH = $PSScriptRoot
streamlit run scripts\sandbox_streamlit.py --server.port 8502
