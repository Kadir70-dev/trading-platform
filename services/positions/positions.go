// Package positions adapts the Python service position endpoints.
package positions

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

type listResp struct {
	Positions []models.Position `json:"positions"`
}

func (s *Service) Positions(ctx context.Context) ([]models.Position, error) {
	var out listResp
	if err := s.c.GetJSON(ctx, "/positions", &out); err != nil {
		return nil, err
	}
	return out.Positions, nil
}

type closeReq struct {
	Ticket int64    `json:"ticket"`
	Volume *float64 `json:"volume"`
}

func (s *Service) Close(ctx context.Context, ticket int64, volume float64) error {
	var v *float64
	if volume > 0 {
		v = &volume
	}
	return s.c.PostJSON(ctx, "/order/close", closeReq{ticket, v}, nil)
}

type closeAllResp struct {
	Closed int `json:"closed"`
}

func (s *Service) CloseAll(ctx context.Context) (int, error) {
	var out closeAllResp
	if err := s.c.PostJSON(ctx, "/order/close-all", nil, &out); err != nil {
		return 0, err
	}
	return out.Closed, nil
}

type modifyReq struct {
	Ticket int64    `json:"ticket"`
	SL     *float64 `json:"sl"`
	TP     *float64 `json:"tp"`
}

func (s *Service) Modify(ctx context.Context, ticket int64, sl, tp float64) error {
	var slp, tpp *float64
	if sl > 0 {
		slp = &sl
	}
	if tp > 0 {
		tpp = &tp
	}
	return s.c.PostJSON(ctx, "/order/modify", modifyReq{ticket, slp, tpp}, nil)
}
