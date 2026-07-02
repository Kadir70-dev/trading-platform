// Package execution routes approved orders to the C++ execution gateway over a
// line-delimited JSON TCP protocol. The gateway performs final validation,
// spread/latency monitoring, and forwards to the broker (Python MT5 service).
//
// Protocol (one JSON object per line):
//   -> {"cmd":"ping"}
//   <- {"ok":true,"pong":true}
//   -> {"cmd":"execute","order":{...OrderRequest...}}
//   <- {...OrderResult...}
package execution

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"net"
	"time"

	"github.com/exness-mt5/trading-platform/internal/models"
)

type Gateway struct {
	addr    string
	timeout time.Duration
}

func New(addr string) *Gateway {
	return &Gateway{addr: addr, timeout: 10 * time.Second}
}

type envelope struct {
	Cmd   string              `json:"cmd"`
	Order *models.OrderRequest `json:"order,omitempty"`
}

func (g *Gateway) roundtrip(ctx context.Context, payload any, out any) error {
	d := net.Dialer{Timeout: g.timeout}
	conn, err := d.DialContext(ctx, "tcp", g.addr)
	if err != nil {
		return fmt.Errorf("dial gateway %s: %w", g.addr, err)
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(g.timeout))

	enc := json.NewEncoder(conn)
	if err := enc.Encode(payload); err != nil {
		return err
	}
	line, err := bufio.NewReader(conn).ReadBytes('\n')
	if err != nil && len(line) == 0 {
		return fmt.Errorf("read gateway reply: %w", err)
	}
	if out == nil {
		return nil
	}
	return json.Unmarshal(line, out)
}

func (g *Gateway) HealthCheck(ctx context.Context) error {
	var resp map[string]any
	if err := g.roundtrip(ctx, envelope{Cmd: "ping"}, &resp); err != nil {
		return err
	}
	if ok, _ := resp["ok"].(bool); !ok {
		return fmt.Errorf("gateway unhealthy: %v", resp)
	}
	return nil
}

func (g *Gateway) Execute(ctx context.Context, req models.OrderRequest) (*models.OrderResult, error) {
	var res models.OrderResult
	if err := g.roundtrip(ctx, envelope{Cmd: "execute", Order: &req}, &res); err != nil {
		return nil, err
	}
	return &res, nil
}
