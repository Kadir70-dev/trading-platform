// Package scheduler is the orchestration brain. On every tick it runs the full
// trading workflow for each configured symbol:
//
//	pull account -> manage open positions (breakeven/trailing) -> per symbol:
//	  signal -> persist -> risk evaluate -> execute -> persist execution/audit
package scheduler

import (
	"context"
	"fmt"
	"log/slog"
	"math"
	"sync"
	"time"

	"github.com/exness-mt5/trading-platform/internal/config"
	"github.com/exness-mt5/trading-platform/internal/interfaces"
	"github.com/exness-mt5/trading-platform/internal/models"
)

type Scheduler struct {
	cfg   *config.Config
	log   *slog.Logger
	md    interfaces.MarketData
	acct  interfaces.AccountService
	pos   interfaces.PositionService
	risk  interfaces.RiskEngine
	exec  interfaces.Executor
	store interfaces.Store

	mu      sync.Mutex
	lastEq  float64
	running bool
}

func New(cfg *config.Config, log *slog.Logger, md interfaces.MarketData,
	acct interfaces.AccountService, pos interfaces.PositionService,
	risk interfaces.RiskEngine, exec interfaces.Executor, store interfaces.Store) *Scheduler {
	return &Scheduler{cfg: cfg, log: log, md: md, acct: acct, pos: pos,
		risk: risk, exec: exec, store: store}
}

// Run blocks until ctx is cancelled, ticking every interval.
func (s *Scheduler) Run(ctx context.Context) error {
	interval := time.Duration(s.cfg.SchedulerIntervalSec) * time.Second
	s.log.Info("scheduler starting", "interval", interval.String(), "symbols", s.cfg.Symbols)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	s.Tick(ctx) // run immediately on start
	for {
		select {
		case <-ctx.Done():
			s.log.Info("scheduler stopping")
			return ctx.Err()
		case <-ticker.C:
			s.Tick(ctx)
		}
	}
}

// Tick performs one full workflow pass. Exported so the CLI can run it once.
func (s *Scheduler) Tick(ctx context.Context) {
	s.mu.Lock()
	if s.running {
		s.mu.Unlock()
		s.log.Warn("previous tick still running; skipping")
		return
	}
	s.running = true
	s.mu.Unlock()
	defer func() { s.mu.Lock(); s.running = false; s.mu.Unlock() }()

	acct, err := s.acct.Account(ctx)
	if err != nil {
		s.log.Error("account fetch failed", "err", err)
		_ = s.store.SaveRiskEvent("CRITICAL", "MT5_DISCONNECTED", "", err.Error(), nil)
		return
	}
	_ = s.store.SaveAccountSnapshot(acct)
	s.trackEquityPnL(acct)

	open, err := s.pos.Positions(ctx)
	if err != nil {
		s.log.Error("positions fetch failed", "err", err)
		open = nil
	}
	s.managePositions(ctx, open)

	if s.risk.DailyHalted() {
		s.log.Warn("daily loss limit reached; trading halted for the day")
		_ = s.store.SaveRiskEvent("CRITICAL", "MAX_DAILY_LOSS", "", "daily loss limit reached", nil)
		return
	}

	for _, sym := range s.cfg.Symbols {
		s.processSymbol(ctx, acct, sym, open)
	}
}

func (s *Scheduler) processSymbol(ctx context.Context, acct *models.Account, symbol string, open []models.Position) {
	sig, err := s.md.Signal(ctx, symbol, s.cfg.Timeframe, s.cfg.WarmupBars)
	if err != nil {
		s.log.Error("signal failed", "symbol", symbol, "err", err)
		return
	}
	sigID, _ := s.store.SaveSignal(sig)

	if sig.Side == models.Hold {
		s.log.Info("hold", "symbol", symbol, "reason", sig.Reason)
		return
	}

	info, err := s.md.SymbolInfo(ctx, symbol)
	if err != nil {
		s.log.Error("symbol info failed", "symbol", symbol, "err", err)
		return
	}

	decision := s.risk.Evaluate(ctx, acct, info, sig, open)
	if !decision.Approved {
		s.log.Warn("trade blocked", "symbol", symbol, "code", decision.Code,
			"reasons", decision.Reasons)
		_ = s.store.SaveRiskEvent(decision.Severity, decision.Code, symbol,
			fmt.Sprint(decision.Reasons), map[string]any{"signal": sig})
		return
	}

	req := models.OrderRequest{
		ClientID: fmt.Sprintf("%s-%s-%d", symbol, sig.Side, time.Now().UnixNano()),
		Symbol:   symbol, Side: sig.Side, Volume: decision.Volume,
		SL: decision.SL, TP: decision.TP, Magic: s.cfg.MagicNumber,
		Comment: fmt.Sprintf("ema_rsi conf=%.0f", sig.Confidence),
	}
	orderID, _ := s.store.SaveOrder(req, sigID, "SENT")

	res, err := s.exec.Execute(ctx, req)
	if err != nil {
		s.log.Error("execution failed", "symbol", symbol, "err", err)
		_ = s.store.SaveRiskEvent("CRITICAL", "EXECUTION_ERROR", symbol, err.Error(), nil)
		return
	}
	_ = s.store.SaveExecution(orderID, res, symbol, sig.Side)
	_ = s.store.Audit("scheduler", "execute", "order", req.ClientID, map[string]any{
		"ok": res.OK, "retcode": res.RetcodeDesc, "volume": decision.Volume,
		"sl": decision.SL, "tp": decision.TP, "latency_ms": res.LatencyMs})

	if res.OK {
		s.log.Info("ORDER FILLED", "symbol", symbol, "side", sig.Side,
			"volume", decision.Volume, "ticket", res.Ticket, "latency_ms", res.LatencyMs)
	} else {
		s.log.Warn("order rejected", "symbol", symbol, "retcode", res.RetcodeDesc)
	}
}

// managePositions implements breakeven move and ATR-free trailing using the
// position's own SL distance as the trail unit (1R). Kept conservative.
func (s *Scheduler) managePositions(ctx context.Context, open []models.Position) {
	for _, p := range open {
		info, err := s.md.SymbolInfo(ctx, p.Symbol)
		if err != nil || p.SL == 0 {
			continue
		}
		price := info.Bid
		if p.Side == models.Sell {
			price = info.Ask
		}
		riskDist := math.Abs(p.EntryPrice - p.SL)
		if riskDist <= 0 {
			continue
		}
		var profitDist float64
		if p.Side == models.Buy {
			profitDist = price - p.EntryPrice
		} else {
			profitDist = p.EntryPrice - price
		}
		rMultiple := profitDist / riskDist

		// Breakeven: once +1R, move SL to entry (if not already past it).
		if rMultiple >= 1.0 {
			newSL := p.EntryPrice
			// Trailing: beyond +1R, trail SL by 1R behind price.
			if rMultiple > 1.0 {
				if p.Side == models.Buy {
					newSL = math.Max(newSL, price-riskDist)
				} else {
					newSL = math.Min(newSL, price+riskDist)
				}
			}
			improved := (p.Side == models.Buy && newSL > p.SL) ||
				(p.Side == models.Sell && newSL < p.SL)
			if improved {
				if err := s.pos.Modify(ctx, p.Ticket, newSL, p.TP); err == nil {
					s.log.Info("trailing/breakeven SL moved", "ticket", p.Ticket,
						"new_sl", newSL, "r", fmt.Sprintf("%.2f", rMultiple))
					_ = s.store.Audit("scheduler", "modify_sl", "position",
						fmt.Sprint(p.Ticket), map[string]any{"new_sl": newSL})
				}
			}
		}
	}
}

func (s *Scheduler) trackEquityPnL(acct *models.Account) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.lastEq != 0 {
		// Approximate realised delta for the daily breaker from equity change.
		s.risk.RegisterClosedPnL(acct.Equity - s.lastEq)
	}
	s.lastEq = acct.Equity
}
