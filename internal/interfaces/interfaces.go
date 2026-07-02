// Package interfaces declares the seams between services so implementations can
// be swapped (e.g. C++ gateway today, FIX executor tomorrow) via DI.
package interfaces

import (
	"context"

	"github.com/exness-mt5/trading-platform/internal/models"
)

// MarketData fetches signals and instrument metadata.
type MarketData interface {
	Signal(ctx context.Context, symbol, timeframe string, count int) (*models.Signal, error)
	SymbolInfo(ctx context.Context, symbol string) (*models.SymbolInfo, error)
}

// AccountService reads account state.
type AccountService interface {
	Account(ctx context.Context) (*models.Account, error)
}

// PositionService reads and closes positions.
type PositionService interface {
	Positions(ctx context.Context) ([]models.Position, error)
	Close(ctx context.Context, ticket int64, volume float64) error
	CloseAll(ctx context.Context) (int, error)
	Modify(ctx context.Context, ticket int64, sl, tp float64) error
}

// RiskEngine validates a candidate trade and sizes it.
type RiskEngine interface {
	Evaluate(ctx context.Context, acct *models.Account, sym *models.SymbolInfo,
		sig *models.Signal, openPositions []models.Position) models.RiskDecision
	RegisterClosedPnL(pnl float64)
	DailyHalted() bool
}

// Executor routes an approved order to the broker (via C++ gateway).
type Executor interface {
	Execute(ctx context.Context, req models.OrderRequest) (*models.OrderResult, error)
	HealthCheck(ctx context.Context) error
}

// Store persists signals, orders, executions, risk events, audit logs.
type Store interface {
	SaveSignal(s *models.Signal) (int64, error)
	SaveOrder(req models.OrderRequest, signalID int64, status string) (int64, error)
	SaveExecution(orderID int64, res *models.OrderResult, symbol string, side models.Side) error
	SaveRiskEvent(severity, code, symbol, message string, ctx map[string]any) error
	SaveAccountSnapshot(a *models.Account) error
	Audit(actor, action, entity, entityID string, detail map[string]any) error
	Close() error
}
