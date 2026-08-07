# Fix: Simple Stack Trading Again (Institutional OFF)

## What Broke
`institutional_mode_enabled()` defaulted **ON whenever simple stack was ON**.
That blocked valid LONG/SHORT setups with regime/grade/circuit-breaker gates.

## What We Fixed
1. Institutional mode now defaults **OFF**
2. Deploy script forces `FM_INSTITUTIONAL_MODE=0` on every deploy
3. Simple stack (bands + stop/TP/lock) trades again

## Deploy From Your PC (PowerShell)

```powershell
cd C:\FutureMathics.ai
git fetch origin
git checkout cursor/disable-institutional-default-8175
git pull origin cursor/disable-institutional-default-8175
.\scripts\deploy_virtue_remote.ps1
```

This uses the correct host from the script (`ubuntu@54.91.152.140`) and your PEM key.
Do **not** use `54.177.179.227` unless that is your current instance.

## Verify After Deploy

In journal / logs look for:
```
BOOT SIMPLE_STACK_ENABLED bands+stop+tp+peak_lock+time_decay
```

You should **not** see:
```
BOOT INSTITUTIONAL_GRADE_ENABLED
```

Dashboard should start taking simple-stack LONG/SHORT setups again.
