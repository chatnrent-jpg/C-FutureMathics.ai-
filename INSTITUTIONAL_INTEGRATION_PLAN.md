# Institutional Grade Integration Plan

## Overview
Integrate institutional-grade modules into main.py with minimal disruption to existing logic.

## Integration Strategy
**Mode Toggle:** `institutional_mode_enabled()` from config.py
- When True: Use institutional regime/grading/sizing/exits/monitoring
- When False: Fall back to existing simple_stack or standard MacroMathics logic

## Key Integration Points in main.py

### 1. Imports (Top of File)
```python
from engine.regime_engine import (
    detect_regime,
    regime_allows_entry,
    regime_is_trending,
    regime_is_range,
    MarketRegime,
)
from engine.entry_quality import evaluate_entry_quality, is_institutional_grade
from engine.institutional_sizing import calculate_institutional_size, is_reset_mode
from engine.institutional_exits import evaluate_institutional_exits, update_peak_pnl
from engine.institutional_monitor import evaluate_strategy_health, is_circuit_breaker_tripped
from engine.config import institutional_mode_enabled
```

### 2. Session State (VirtueSession dataclass)
Add new fields:
```python
# Institutional state
institutional_regime: str = "RANGE_MEAN_REVERT"
institutional_regime_confidence: float = 60.0
institutional_entry_grade: str = "F"
institutional_peak_pnl: float = 0.0
recent_trades: list = field(default_factory=list)  # For circuit breaker stats
```

### 3. Regime Detection (After strategy.evaluate())
```python
# Around line 2302, after decision = session.strategy.evaluate(holding=holding)
if institutional_mode_enabled():
    regime_state = detect_regime(
        adx=float(decision.adx),
        atr_pct=float(decision.atr_pct),
        blend=float(decision.blended_score),
        ema_fast=float(decision.ema_fast),
        ema_slow=float(decision.ema_slow),
    )
    session.institutional_regime = regime_state.regime.value
    session.institutional_regime_confidence = regime_state.confidence
    logger.info(
        "CYCLE %s REGIME=%s confidence=%.0f%% reason=%s",
        session.cycle,
        regime_state.regime.value,
        regime_state.confidence,
        regime_state.reason,
    )
else:
    regime_state = None
```

### 4. Circuit Breaker Check (Early in Entry Pipeline)
```python
# Around line 2820, after Layer 1b multi-TP wall-clock lock
if institutional_mode_enabled():
    healthy, health_reason, breaker = evaluate_strategy_health(
        trades_today=session.trades_today,
        consecutive_losses=session.consecutive_losses,
        daily_pnl=session.realized_pnl_today,
        recent_trades=session.recent_trades,
    )
    if not healthy:
        logger.warning(
            "CYCLE %s CIRCUIT_BREAKER %s — %s",
            session.cycle,
            breaker,
            health_reason,
        )
        session.last_action = "FLAT"
        return
```

### 5. Entry Quality Grading (Before Entry Decision)
```python
# Around line 3000, after streak confirmation
if institutional_mode_enabled() and regime_state is not None:
    grade_ok, grade_reason, grade = evaluate_entry_quality(
        regime=regime_state.regime,
        adx=float(decision.adx),
        prev_adx=float(session.prev_adx or 0.0),
        atr=float(decision.atr),
        prev_atr=float(session.prev_atr or 0.0),
        blend=float(decision.blended_score),
        vwap_score=float(decision.vwap_score),
        twap_score=float(decision.twap_score),
        signal_side=side,
    )
    session.institutional_entry_grade = grade
    
    if not grade_ok:
        logger.info(
            "CYCLE %s ENTRY_QUALITY %s — %s",
            session.cycle,
            grade,
            grade_reason,
        )
        session.last_action = "FLAT"
        return
    
    logger.info(
        "CYCLE %s ENTRY_QUALITY %s — %s (PROCEED)",
        session.cycle,
        grade,
        grade_reason,
    )
```

### 6. Institutional Sizing (Replace/Augment Existing Sizing)
```python
# Around line 3200, in sizing logic
if institutional_mode_enabled() and regime_state is not None:
    contracts, size_reason = calculate_institutional_size(
        regime=regime_state.regime,
        regime_confidence=regime_state.confidence,
        entry_grade=session.institutional_entry_grade,
        consecutive_losses=session.consecutive_losses,
        consecutive_wins=session.consecutive_wins,
        daily_pnl=session.realized_pnl_today,
        nav=session.broker.equity,
    )
    
    if contracts == 0:
        logger.info(
            "CYCLE %s INSTITUTIONAL_SIZING zero — %s",
            session.cycle,
            size_reason,
        )
        session.last_action = "FLAT"
        return
    
    logger.info(
        "CYCLE %s INSTITUTIONAL_SIZING %s contracts — %s",
        session.cycle,
        contracts,
        size_reason,
    )
else:
    # Existing sizing logic
    contracts = calculate_normal_size(...)
```

### 7. Institutional Exits (In Hold Path)
```python
# Around line 2590-2680, add institutional exit checks BEFORE existing TP/stop/decay
if institutional_mode_enabled() and regime_state is not None:
    if bool(session.tactical_active) and int(session.tactical_size) > 0:
        # Update peak PnL for trailing stops
        tactical_pnl = _tactical_open_pnl(session, price)
        session.institutional_peak_pnl = update_peak_pnl(
            tactical_pnl,
            session.institutional_peak_pnl,
        )
        
        # Check all 6 exit layers
        should_exit, exit_reason, exit_layer = evaluate_institutional_exits(
            regime=regime_state.regime,
            holding=session.tactical_side,
            entry_price=session.tactical_entry_price,
            current_price=price,
            open_pnl=tactical_pnl,
            cycles_held=session.cycle - (session.entry_cycle_marker or session.cycle),
            adx=float(decision.adx),
            blend=float(decision.blended_score),
            peak_pnl=session.institutional_peak_pnl,
        )
        
        if should_exit:
            ok, pnl = await _close_tactical_sleeve(
                session,
                price=price,
                stop_ticks=stop_ticks,
                reason=f"institutional_{exit_layer}:{exit_reason}",
            )
            if ok:
                logger.info(
                    "CYCLE %s INSTITUTIONAL_EXIT %s pnl≈%.2f — %s",
                    session.cycle,
                    exit_layer,
                    pnl,
                    exit_reason,
                )
            session.last_action = "FLAT"
            return
```

### 8. Recent Trades Tracking (In update_outcome_state)
```python
# Around line 980, in update_outcome_state function
if institutional_mode_enabled():
    # Track recent trades for circuit breaker stats
    trade_record = {
        "cycle": cycle,
        "outcome": session.last_result,
        "pnl": delta,
        "reason": tag,
    }
    session.recent_trades.append(trade_record)
    # Keep last 20 trades only
    if len(session.recent_trades) > 20:
        session.recent_trades = session.recent_trades[-20:]
```

### 9. Boot Log (Show Institutional Mode Status)
```python
# Around line 3510, in boot logs
if institutional_mode_enabled():
    logger.info("BOOT INSTITUTIONAL_GRADE_ENABLED — regime-aware adaptive trading")
    logger.info(
        "  Regimes: TREND (ADX≥22) | RANGE (ADX 12-22) | CHAOS (ATR≥20%% or ADX<12)"
    )
    logger.info("  Entry: A+ or A grade only | Sizing: adaptive | Exits: 6-layer")
    logger.info("  Circuit breakers: CB1-CB4 active")
else:
    logger.info("BOOT INSTITUTIONAL_GRADE_DISABLED — using simple_stack or standard mode")
```

## Testing Strategy

1. **Unit tests:** Test each module in isolation
2. **Integration test:** Add test_institutional_flow to test_virtue_brain.py
3. **Manual test:** Deploy to dev, monitor logs for correct regime/grade/sizing

## Rollback Plan

If integration breaks:
1. Set `FM_INSTITUTIONAL_MODE=0` in .env.local (instant rollback)
2. System falls back to simple_stack mode
3. Or revert commit entirely

## Success Criteria

- [ ] All existing tests pass
- [ ] Institutional mode shows regime/grade/sizing in logs
- [ ] Circuit breakers trigger correctly
- [ ] Exits use layered strategy (L1-L6)
- [ ] Falls back gracefully when institutional_mode_enabled() = False
