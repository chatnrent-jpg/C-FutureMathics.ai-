# Opens FutureMathics local dashboard — waits until Streamlit is listening.
$root = "C:\FutureMathics.ai"
Set-Location $root
$env:PYTHONPATH = $root

function Test-Port8502 {
    return [bool](Get-NetTCPConnection -LocalPort 8502 -State Listen -ErrorAction SilentlyContinue)
}

if (-not (Test-Port8502)) {
    Start-Process -WindowStyle Minimized -FilePath "cmd.exe" -ArgumentList @(
        "/k", "cd /d $root && set PYTHONPATH=$root && python -m streamlit run scripts\sandbox_streamlit.py --server.port 8502 --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false"
    )
    for ($i = 0; $i -lt 25; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Port8502) { break }
    }
}

if (Test-Port8502) {
    Start-Process "http://127.0.0.1:8502"
} else {
    [System.Windows.Forms.MessageBox]::Show(
        "FutureMathics dashboard did not start on port 8502.`nCheck the minimized Streamlit window for errors.",
        "FutureMathics",
        "OK",
        "Warning"
    ) | Out-Null
}
