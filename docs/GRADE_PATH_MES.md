# VolumeWatch Grade-Path → MES Futures

**Status**: Production-ready (paper)  
**Brain**: VolumeWatch F→A grade score  
**Body**: MES long/short (FutureMathics execution)

---

## Rules (path-dependent)

### Rising (coming from down / weakness)
| Score | Action |
|------:|--------|
| &lt;50 | **CASH** — no trading |
| ≥50 and &lt;85 | **LONG MES** |
| ≥85 | **Exit long** |
| 85–90 | **CASH** |
| ≥90 | **SHORT MES** |

### Falling (coming from up / strength)
| Score | Action |
|------:|--------|
| ≥90 | **SHORT** (fade extreme) if flat |
| Hold short until ≤50 | **Exit short** |
| &lt;50 | **CASH** — no trading |

Protective **hard stop**: 80 ticks (~$100/contract) — grade owns take-profit timing.

---

## Run (local)

```powershell
cd C:\FutureMathics.ai
$env:PYTHONPATH = "C:\FutureMathics.ai"
python scripts\test_grade_path_engine.py
python scripts\run_grade_futures.py --cycles 5 --ignore-hours
```

Production session (respects CME hours):

```powershell
python scripts\run_grade_futures.py
```

**Requires**: VolumeWatch Macro Hub writing  
`C:\Volumewatch\shared_volumewatch_marketmathics_state.json`  
(stale after 5 minutes unless `GRADE_ALLOW_STALE=True`).

---

## AWS (100% automated — no PC sync)

On EC2, two systemd services run together:

| Service | Role |
|---------|------|
| `vw_grade_publisher` | Computes VolumeWatch grade every 60s on the server |
| `futuremathics_grade` | Trades MES from that grade |

Grade file: `/home/ubuntu/FutureMathics.ai/data/shared_volumewatch_marketmathics_state.json`

**You do not need** `scripts/sync_vw_grade_to_aws.ps1` for production.

```bash
sudo systemctl status vw_grade_publisher futuremathics_grade
sudo journalctl -u vw_grade_publisher -f
sudo journalctl -u futuremathics_grade -f
```

---

## Config (`engine/config.py`)

| Key | Default |
|-----|---------|
| `GRADE_MODE` | True |
| `GRADE_LONG_ENTRY` | 50 |
| `GRADE_LONG_EXIT` | 85 |
| `GRADE_SHORT_ENTRY` | 90 |
| `GRADE_SHORT_EXIT` | 50 |
| `GRADE_CONTRACTS` | 2 |
| `GRADE_HARD_STOP_TICKS` | 80 |
| `GRADE_CYCLE_INTERVAL_S` | 30 |
| `GRADE_DAILY_PROFIT_LOCK` | 1000 |
| `GRADE_DAILY_LOSS_HALT` | 400 |
| `GRADE_SCORE_SOURCE` | overall |

---

## Files

| File | Role |
|------|------|
| `celine/grade_path_engine.py` | Path state machine |
| `engine/volumewatch_grade_feed.py` | Shared-state reader |
| `engine/grade_orchestrator.py` | MES loop |
| `scripts/run_grade_futures.py` | Entrypoint |
| `scripts/test_grade_path_engine.py` | Unit tests |
| `data/grade_path_state.json` | Persisted path/exposure |

---

## AWS note

On EC2, either:
1. Sync/copy the VolumeWatch shared state JSON to the server, or  
2. Set `FM_VOLUMEWATCH_STATE_PATH` to a reachable path, or  
3. Run VolumeWatch hub on the same host so the grade stays fresh.
