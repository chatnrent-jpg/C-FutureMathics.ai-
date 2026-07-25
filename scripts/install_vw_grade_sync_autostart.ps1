# Install Macro Hub -> AWS grade sync to start at Windows logon (no admin needed).
# Places a shortcut in the current user's Startup folder.

$ErrorActionPreference = "Stop"
$script = "C:\FutureMathics.ai\scripts\sync_vw_grade_to_aws.ps1"
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$startup = [Environment]::GetFolderPath("Startup")
$lnkPath = Join-Path $startup "FutureMathics_VW_Grade_Sync.lnk"

if (-not (Test-Path $script)) {
    throw "Missing sync script: $script"
}

$w = New-Object -ComObject WScript.Shell
$lnk = $w.CreateShortcut($lnkPath)
$lnk.TargetPath = $ps
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$script`""
$lnk.WorkingDirectory = "C:\FutureMathics.ai"
$lnk.WindowStyle = 7
$lnk.Description = "Sync VolumeWatch Macro Hub grade to AWS"
$lnk.Save()

Write-Host "Startup shortcut created: $lnkPath"
Write-Host "Starting sync now..."
Start-Process -FilePath $ps -ArgumentList "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$script`"" -WindowStyle Minimized
Write-Host "Done. Keep Volume Watch Macro Hub open so AWS mirrors the score on your screen."
