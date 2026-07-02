//go:build postgres

// PostgreSQL-backed Store. Enabled with `go build -tags postgres`.
// Requires: go get github.com/jackc/pgx/v5
package repositories

import (
	"context"
	"encoding/json"
	"time"

	"github.com/exness-mt5/trading-platform/internal/interfaces"
	"github.com/exness-mt5/trading-platform/internal/models"
	"github.com/jackc/pgx/v5/pgxpool"
)

type PostgresStore struct {
	pool *pgxpool.Pool
}

func newPostgresStore(url string) (interfaces.Store, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	pool, err := pgxpool.New(ctx, url)
	if err != nil {
		return nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		return nil, err
	}
	return &PostgresStore{pool: pool}, nil
}

func (s *PostgresStore) bg() (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), 5*time.Second)
}

func (s *PostgresStore) SaveSignal(sig *models.Signal) (int64, error) {
	ctx, cancel := s.bg()
	defer cancel()
	var id int64
	err := s.pool.QueryRow(ctx, `
		INSERT INTO signals(symbol,timeframe,side,confidence,price,ema_fast,ema_slow,rsi,atr,reason)
		VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) RETURNING id`,
		sig.Symbol, sig.Timeframe, sig.Side, sig.Confidence, sig.Price,
		sig.EMAFast, sig.EMASlow, sig.RSI, sig.ATR, sig.Reason).Scan(&id)
	return id, err
}

func (s *PostgresStore) SaveOrder(req models.OrderRequest, signalID int64, status string) (int64, error) {
	ctx, cancel := s.bg()
	defer cancel()
	var id int64
	var sigID *int64
	if signalID > 0 {
		sigID = &signalID
	}
	err := s.pool.QueryRow(ctx, `
		INSERT INTO orders(client_id,symbol,side,volume,sl,tp,status,signal_id,magic,comment)
		VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
		ON CONFLICT (client_id) DO UPDATE SET status=EXCLUDED.status, updated_at=now()
		RETURNING id`,
		req.ClientID, req.Symbol, req.Side, req.Volume, req.SL, req.TP,
		status, sigID, req.Magic, req.Comment).Scan(&id)
	return id, err
}

func (s *PostgresStore) SaveExecution(orderID int64, res *models.OrderResult, symbol string, side models.Side) error {
	ctx, cancel := s.bg()
	defer cancel()
	var ticket *int64
	var price, spread *float64
	if res != nil {
		ticket, price, spread = res.Ticket, res.Price, res.SpreadPoints
	}
	_, err := s.pool.Exec(ctx, `
		INSERT INTO executions(order_id,broker_ticket,symbol,side,volume,price,spread_points,latency_ms,retcode,retcode_desc)
		VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
		orderID, ticket, symbol, side, deref(res.Volume), deref(price),
		deref(spread), res.LatencyMs, res.Retcode, res.RetcodeDesc)
	return err
}

func (s *PostgresStore) SaveRiskEvent(severity, code, symbol, message string, c map[string]any) error {
	ctx, cancel := s.bg()
	defer cancel()
	b, _ := json.Marshal(c)
	_, err := s.pool.Exec(ctx, `
		INSERT INTO risk_events(severity,code,symbol,message,context)
		VALUES($1,$2,$3,$4,$5)`, severity, code, symbol, message, b)
	return err
}

func (s *PostgresStore) SaveAccountSnapshot(a *models.Account) error {
	ctx, cancel := s.bg()
	defer cancel()
	_, err := s.pool.Exec(ctx, `
		INSERT INTO accounts(login,broker,server,mode,currency,leverage,balance,equity,margin,free_margin,margin_level)
		VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)`,
		a.Login, a.Broker, a.Server, a.Mode, a.Currency, a.Leverage,
		a.Balance, a.Equity, a.Margin, a.FreeMargin, a.MarginLevel)
	return err
}

func (s *PostgresStore) Audit(actor, action, entity, entityID string, detail map[string]any) error {
	ctx, cancel := s.bg()
	defer cancel()
	b, _ := json.Marshal(detail)
	_, err := s.pool.Exec(ctx, `
		INSERT INTO audit_logs(actor,action,entity,entity_id,detail)
		VALUES($1,$2,$3,$4,$5)`, actor, action, entity, entityID, b)
	return err
}

func (s *PostgresStore) Close() error {
	s.pool.Close()
	return nil
}

func deref(p *float64) float64 {
	if p == nil {
		return 0
	}
	return *p
}
