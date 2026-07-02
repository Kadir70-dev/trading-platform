// Package marketdata adapts the Python service into the MarketData interface.
package marketdata

import (
	"context"
	"time"

	"github.com/exness-mt5/trading-platform/internal/httpx"
	"github.com/exness-mt5/trading-platform/internal/models"
)

type Service struct {
	c *httpx.Client
}

func New(baseURL string) *Service {
	return &Service{c: httpx.New("http://"+baseURL, 15*time.Second)}
}

type signalReq struct {
	Symbol    string `json:"symbol"`
	Timeframe string `json:"timeframe"`
	Count     int    `json:"count"`
}

func (s *Service) Signal(ctx context.Context, symbol, timeframe string, count int) (*models.Signal, error) {
	var out models.Signal
	err := s.c.PostJSON(ctx, "/signal", signalReq{symbol, timeframe, count}, &out)
	if err != nil {
		return nil, err
	}
	return &out, nil
}

func (s *Service) SymbolInfo(ctx context.Context, symbol string) (*models.SymbolInfo, error) {
	var out models.SymbolInfo
	if err := s.c.GetJSON(ctx, "/symbol/"+symbol, &out); err != nil {
		return nil, err
	}
	return &out, nil
}
