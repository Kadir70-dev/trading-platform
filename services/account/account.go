// Package account adapts the Python service /account into AccountService.
package account

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
	return &Service{c: httpx.New("http://"+baseURL, 10*time.Second)}
}

func (s *Service) Account(ctx context.Context) (*models.Account, error) {
	var out models.Account
	if err := s.c.GetJSON(ctx, "/account", &out); err != nil {
		return nil, err
	}
	return &out, nil
}
