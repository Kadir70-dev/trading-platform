// Package risk implements enterprise-grade pre-trade risk management:
// position sizing, spread/margin checks, exposure limits, duplicate detection,
// daily-loss and drawdown circuit breakers, and an emergency stop.
package risk

import (
	"context"
	"fmt"
	"math"
	"sync"
	"time"

	"github.com/exness-mt5/trading-platform/internal/config"
	"github.com/exness-mt5/trading-platform/internal/models"
)

type Engine struct {
	cfg *config.Config

	mu          sync.Mutex
	day         string
	dayStartEq  float64
	dayRealized float64
	peakEquity  float64
	emergency   bool
}

func New(cfg *config.Config) *Engine {
	return &Engine{cfg: cfg, emergency: cfg.EmergencyStop}
}

// SetEmergencyStop toggles the kill switch.
func (e *Engine) SetEmergencyStop(on bool) {
	e.mu.Lock()
	e.emergency = on
	e.mu.Unlock()
}

// RegisterClosedPnL accumulates realised PnL for the daily-loss breaker.
func (e *Engine) RegisterClosedPnL(pnl float64) {
	e.mu.Lock()
	e.dayRealized += pnl
	e.mu.Unlock()
}

func (e *Engine) rollDay(equity float64) {
	today := time.Now().UTC().Format("2006-01-02")
	if e.day != today {
		e.day = today
		e.dayStartEq = equity
		e.dayRealized = 0
		if equity > e.peakEquity {
			e.peakEquity = equity
		}
	}
}

// DailyHalted reports whether the daily-loss breaker has tripped.
func (e *Engine) DailyHalted() bool {
	e.mu.Lock()
	defer e.mu.Unlock()
	if e.dayStartEq <= 0 {
		return false
	}
	lossPct := -e.dayRealized / e.dayStartEq * 100.0
	return lossPct >= e.cfg.MaxDailyLossPct
}

func block(code, severity string, reasons ...string) models.RiskDecision {
	return models.RiskDecision{
		Approved: false, Code: code, Severity: severity,
		Reasons: reasons, EvaluatedAt: time.Now().UTC(),
	}
}

// Evaluate runs the full pre-trade checklist and sizes the position.
func (e *Engine) Evaluate(_ context.Context, acct *models.Account, sym *models.SymbolInfo,
	sig *models.Signal, open []models.Position) models.RiskDecision {

	e.mu.Lock()
	e.rollDay(acct.Equity)
	if acct.Equity > e.peakEquity {
		e.peakEquity = acct.Equity
	}
	emergency := e.emergency
	dayStart := e.dayStartEq
	dayRealized := e.dayRealized
	peak := e.peakEquity
	e.mu.Unlock()

	// 0. Emergency stop
	if emergency {
		return block("EMERGENCY_STOP", "CRITICAL", "emergency stop engaged")
	}

	// 1. Signal must be actionable with mandatory SL/TP
	if sig.Side != models.Buy && sig.Side != models.Sell {
		return block("NO_SIGNAL", "INFO", "signal is HOLD")
	}
	if sig.SL == nil || sig.TP == nil || *sig.SL <= 0 || *sig.TP <= 0 {
		return block("MISSING_STOPS", "BLOCK", "stop loss / take profit mandatory")
	}

	// 2. Tradeability
	if !sym.TradeAllowed {
		return block("SYMBOL_DISABLED", "BLOCK", "symbol trading disabled")
	}

	// 3. Spread guard
	if sym.SpreadPoints > float64(e.cfg.MaxSpreadPoints) {
		return block("SPREAD_TOO_HIGH", "BLOCK",
			fmt.Sprintf("spread %.1f > max %d", sym.SpreadPoints, e.cfg.MaxSpreadPoints))
	}

	// 4. Daily loss breaker
	if dayStart > 0 {
		lossPct := -dayRealized / dayStart * 100.0
		if lossPct >= e.cfg.MaxDailyLossPct {
			return block("MAX_DAILY_LOSS", "CRITICAL",
				fmt.Sprintf("daily loss %.2f%% >= %.2f%%", lossPct, e.cfg.MaxDailyLossPct))
		}
	}

	// 5. Drawdown breaker
	if peak > 0 {
		ddPct := (peak - acct.Equity) / peak * 100.0
		if ddPct >= e.cfg.MaxDrawdownPct {
			return block("MAX_DRAWDOWN", "CRITICAL",
				fmt.Sprintf("drawdown %.2f%% >= %.2f%%", ddPct, e.cfg.MaxDrawdownPct))
		}
	}

	// 6. Open trade & exposure limits
	if len(open) >= e.cfg.MaxOpenTrades {
		return block("MAX_OPEN_TRADES", "BLOCK",
			fmt.Sprintf("open trades %d >= %d", len(open), e.cfg.MaxOpenTrades))
	}
	symExposure := 0
	for _, p := range open {
		if p.Symbol == sig.Symbol {
			symExposure++
			// 7. Duplicate same-direction position guard
			if p.Side == sig.Side {
				return block("DUPLICATE_POSITION", "BLOCK",
					fmt.Sprintf("existing %s position on %s (ticket %d)", p.Side, p.Symbol, p.Ticket))
			}
		}
	}
	if symExposure >= e.cfg.MaxSymbolExposure {
		return block("MAX_SYMBOL_EXPOSURE", "BLOCK",
			fmt.Sprintf("%s exposure %d >= %d", sig.Symbol, symExposure, e.cfg.MaxSymbolExposure))
	}

	// 8. Reward:Risk floor
	entry := sig.Price
	slDist := math.Abs(entry - *sig.SL)
	tpDist := math.Abs(*sig.TP - entry)
	if slDist <= 0 {
		return block("INVALID_STOPS", "BLOCK", "zero stop distance")
	}
	rr := tpDist / slDist
	if rr < e.cfg.MinRR-1e-9 {
		return block("RR_TOO_LOW", "BLOCK",
			fmt.Sprintf("reward:risk %.2f < min %.2f", rr, e.cfg.MinRR))
	}

	// 9. Position sizing from risk %
	vol := e.sizePosition(acct.Equity, slDist, sym)
	if vol < sym.VolumeMin {
		return block("RISK_TOO_SMALL", "WARN",
			fmt.Sprintf("sized volume %.4f < min %.4f for %.2f%% risk", vol, sym.VolumeMin, e.cfg.MaxRiskPct))
	}

	// 10. Margin sufficiency (approx): required margin = vol*contract*price/leverage
	required := vol * sym.ContractSize * entry / float64(maxInt(acct.Leverage, 1))
	if required > acct.FreeMargin {
		return block("INSUFFICIENT_MARGIN", "BLOCK",
			fmt.Sprintf("required margin %.2f > free %.2f", required, acct.FreeMargin))
	}

	return models.RiskDecision{
		Approved: true, Volume: vol, SL: *sig.SL, TP: *sig.TP,
		Code: "APPROVED", Severity: "INFO", EvaluatedAt: time.Now().UTC(),
	}
}

// sizePosition: volume such that hitting SL loses ~max_risk_pct of equity.
func (e *Engine) sizePosition(equity, slDist float64, sym *models.SymbolInfo) float64 {
	riskMoney := equity * e.cfg.MaxRiskPct / 100.0
	// money lost per 1.0 lot if price moves slDist against us
	valuePerPrice := sym.TickValue / nonZero(sym.TickSize) // money per 1.0 price unit per lot
	lossPerLot := slDist * valuePerPrice
	if lossPerLot <= 0 {
		return 0
	}
	raw := riskMoney / lossPerLot
	// round down to volume step
	step := nonZero(sym.VolumeStep)
	vol := math.Floor(raw/step) * step
	if vol > sym.VolumeMax {
		vol = sym.VolumeMax
	}
	return roundTo(vol, step)
}

func nonZero(v float64) float64 {
	if v == 0 {
		return 1e-9
	}
	return v
}
func roundTo(v, step float64) float64 {
	if step <= 0 {
		return v
	}
	return math.Round(v/step) * step
}
func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}
