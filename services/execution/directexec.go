package execution

import (
	"context"
	"time"

	"github.com/exness-mt5/trading-platform/internal/httpx"
	"github.com/exness-mt5/trading-platform/internal/models"
)

// DirectExecutor bypasses the C++ gateway and sends orders straight to the
// Python MT5 service. Used as a fallback when the gateway is unavailable, or in
// lightweight deployments. Implements interfaces.Executor.
type DirectExecutor struct {
	c *httpx.Client
}

func NewDirect(pythonAddr string) *DirectExecutor {
	return &DirectExecutor{c: httpx.New("http://"+pythonAddr, 15*time.Second)}
}

type marketOrderReq struct {
	Symbol  string  `json:"symbol"`
	Side    string  `json:"side"`
	Volume  float64 `json:"volume"`
	SL      float64 `json:"sl"`
	TP      float64 `json:"tp"`
	Comment string  `json:"comment"`
}

func (d *DirectExecutor) HealthCheck(ctx context.Context) error {
	return d.c.GetJSON(ctx, "/health", nil)
}

func (d *DirectExecutor) Execute(ctx context.Context, req models.OrderRequest) (*models.OrderResult, error) {
	var res models.OrderResult
	body := marketOrderReq{req.Symbol, string(req.Side), req.Volume, req.SL, req.TP, req.Comment}
	if err := d.c.PostJSON(ctx, "/order/market", body, &res); err != nil {
		return nil, err
	}
	return &res, nil
}
