# wait-pg.ps1 - wait until Docker Postgres on 5433 accepts connections
param(
  [string]$Container = "vetted-postgres",
  [int]$Port = 5433,
  [int]$TimeoutSec = 90
)

$ErrorActionPreference = "Stop"
Write-Host "Waiting for $Container (host port $Port)..."

$deadline = (Get-Date).AddSeconds($TimeoutSec)
while ((Get-Date) -lt $deadline) {
  $running = docker ps --filter "name=$Container" --format "{{.Names}}"
  if (-not $running) {
    Write-Host "Container not running yet..."
    Start-Sleep -Seconds 2
    continue
  }

  $health = docker inspect --format "{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}" $Container 2>$null
  docker exec $Container pg_isready -U vetted_user -d vetted_db 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: Postgres ready (health=$health)"
    exit 0
  }

  Write-Host "Not ready yet (health=$health)..."
  Start-Sleep -Seconds 2
}

Write-Host "FAIL: timed out waiting for Postgres"
exit 1
