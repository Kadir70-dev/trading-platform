package risk

import (
	"context"
	"testing"

	"github.com/exness-mt5/trading-platform/internal/config"
	"github.com/exness-mt5/trading-platform/internal/models"
)

func baseCfg() *config.Config {
	return &config.Config{
		MaxRiskPct: 1.0, MinRR: 2.0, MaxDailyLossPct: 5.0, MaxDrawdownPct: 20.0,
		MaxOpenTrades: 5, MaxSymbolExposure: 2, MaxSpreadPoints: 50,
	}
}

func eurusd() *models.SymbolInfo {
	return &models.SymbolInfo{
		Symbol: "EURUSD", Digits: 5, Point: 0.00001, Bid: 1.0850, Ask: 1.08502,
		SpreadPoints: 2, VolumeMin: 0.01, VolumeMax: 100, VolumeStep: 0.01,
		TradeAllowed: true, TickValue: 1.0, TickSize: 0.00001, ContractSize: 100000,
	}
}

func buySignal() *models.Signal {
	sl, tp := 1.0800, 1.0950
	return &models.Signal{Symbol: "EURUSD", Timeframe: "M1", Side: models.Buy,
		Confidence: 80, Price: 1.0850, SL: &sl, TP: &tp}
}

func acct(equity, free float64) *models.Account {
	return &models.Account{Equity: equity, Balance: equity, FreeMargin: free, Leverage: 2000}
}

func TestApprovesValidTrade(t *testing.T) {
	e := New(baseCfg())
	d := e.Evaluate(context.Background(), acct(1000, 1000), eurusd(), buySignal(), nil)
	if !d.Approved {
		t.Fatalf("expected approval, got blocked: %v", d.Reasons)
	}
	if d.Volume <= 0 {
		t.Fatalf("expected positive volume, got %v", d.Volume)
	}
}

func TestPositionSizingRespectsRisk(t *testing.T) {
	e := New(baseCfg())
	// equity 1000, 1% risk = $10. SL distance = 0.0050 price.
	// value per price unit/lot = tickValue/tickSize = 1/0.00001 = 100000.
	// loss per lot = 0.0050 * 100000 = 500. volume = 10/500 = 0.02.
	d := e.Evaluate(context.Background(), acct(1000, 1000), eurusd(), buySignal(), nil)
	if d.Volume < 0.01 || d.Volume > 0.03 {
		t.Fatalf("expected ~0.02 lots for 1%% risk, got %v", d.Volume)
	}
}

func TestBlocksHighSpread(t *testing.T) {
	e := New(baseCfg())
	sym := eurusd()
	sym.SpreadPoints = 80
	d := e.Evaluate(context.Background(), acct(1000, 1000), sym, buySignal(), nil)
	if d.Approved || d.Code != "SPREAD_TOO_HIGH" {
		t.Fatalf("expected SPREAD_TOO_HIGH, got %s approved=%v", d.Code, d.Approved)
	}
}

func TestBlocksMissingStops(t *testing.T) {
	e := New(baseCfg())
	sig := buySignal()
	sig.SL = nil
	d := e.Evaluate(context.Background(), acct(1000, 1000), eurusd(), sig, nil)
	if d.Approved || d.Code != "MISSING_STOPS" {
		t.Fatalf("expected MISSING_STOPS, got %s", d.Code)
	}
}

func TestBlocksLowRR(t *testing.T) {
	e := New(baseCfg())
	sig := buySignal()
	tp := 1.0860 // tiny TP -> RR < 2
	sig.TP = &tp
	d := e.Evaluate(context.Background(), acct(1000, 1000), eurusd(), sig, nil)
	if d.Approved || d.Code != "RR_TOO_LOW" {
		t.Fatalf("expected RR_TOO_LOW, got %s", d.Code)
	}
}

func TestBlocksDuplicatePosition(t *testing.T) {
	e := New(baseCfg())
	open := []models.Position{{Ticket: 1, Symbol: "EURUSD", Side: models.Buy, Volume: 0.1}}
	d := e.Evaluate(context.Background(), acct(1000, 1000), eurusd(), buySignal(), open)
	if d.Approved || d.Code != "DUPLICATE_POSITION" {
		t.Fatalf("expected DUPLICATE_POSITION, got %s", d.Code)
	}
}

func TestBlocksMaxOpenTrades(t *testing.T) {
	e := New(baseCfg())
	var open []models.Position
	for i := 0; i < 5; i++ {
		open = append(open, models.Position{Ticket: int64(i), Symbol: "GBPUSD", Side: models.Buy})
	}
	d := e.Evaluate(context.Background(), acct(1000, 1000), eurusd(), buySignal(), open)
	if d.Approved || d.Code != "MAX_OPEN_TRADES" {
		t.Fatalf("expected MAX_OPEN_TRADES, got %s", d.Code)
	}
}

func TestBlocksInsufficientMargin(t *testing.T) {
	e := New(baseCfg())
	d := e.Evaluate(context.Background(), acct(1000, 0.01), eurusd(), buySignal(), nil)
	if d.Approved || d.Code != "INSUFFICIENT_MARGIN" {
		t.Fatalf("expected INSUFFICIENT_MARGIN, got %s approved=%v", d.Code, d.Approved)
	}
}

func TestEmergencyStop(t *testing.T) {
	cfg := baseCfg()
	cfg.EmergencyStop = true
	e := New(cfg)
	d := e.Evaluate(context.Background(), acct(1000, 1000), eurusd(), buySignal(), nil)
	if d.Approved || d.Code != "EMERGENCY_STOP" {
		t.Fatalf("expected EMERGENCY_STOP, got %s", d.Code)
	}
}

func TestDailyLossBreaker(t *testing.T) {
	e := New(baseCfg())
	// First evaluation seeds the day start equity at 1000.
	e.Evaluate(context.Background(), acct(1000, 1000), eurusd(), buySignal(), nil)
	e.RegisterClosedPnL(-60) // -6% of 1000 > 5% limit
	if !e.DailyHalted() {
		t.Fatal("expected daily halt after -6% loss")
	}
	d := e.Evaluate(context.Background(), acct(940, 940), eurusd(), buySignal(), nil)
	if d.Approved || d.Code != "MAX_DAILY_LOSS" {
		t.Fatalf("expected MAX_DAILY_LOSS, got %s", d.Code)
	}
}
