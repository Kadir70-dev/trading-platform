// Package models holds the shared domain types passed between services.
package models

import "time"

type Side string

const (
	Buy  Side = "BUY"
	Sell Side = "SELL"
	Hold Side = "HOLD"
)

// Account mirrors python service /account.
type Account struct {
	Login       int64   `json:"login"`
	Broker      string  `json:"broker"`
	Server      string  `json:"server"`
	Mode        string  `json:"mode"`
	Currency    string  `json:"currency"`
	Leverage    int     `json:"leverage"`
	Balance     float64 `json:"balance"`
	Equity      float64 `json:"equity"`
	Margin      float64 `json:"margin"`
	FreeMargin  float64 `json:"free_margin"`
	MarginLevel float64 `json:"margin_level"`
}

// Signal mirrors python strategy output.
type Signal struct {
	Symbol     string   `json:"symbol"`
	Timeframe  string   `json:"timeframe"`
	Side       Side     `json:"side"`
	Confidence float64  `json:"confidence"`
	Price      float64  `json:"price"`
	EMAFast    float64  `json:"ema_fast"`
	EMASlow    float64  `json:"ema_slow"`
	RSI        float64  `json:"rsi"`
	ATR        float64  `json:"atr"`
	SL         *float64 `json:"sl"`
	TP         *float64 `json:"tp"`
	Reason     string   `json:"reason"`
}

// SymbolInfo mirrors python /symbol/{symbol}.
type SymbolInfo struct {
	Symbol       string  `json:"symbol"`
	Digits       int     `json:"digits"`
	Point        float64 `json:"point"`
	Bid          float64 `json:"bid"`
	Ask          float64 `json:"ask"`
	SpreadPoints float64 `json:"spread_points"`
	VolumeMin    float64 `json:"volume_min"`
	VolumeMax    float64 `json:"volume_max"`
	VolumeStep   float64 `json:"volume_step"`
	TradeAllowed bool    `json:"trade_allowed"`
	TickValue    float64 `json:"tick_value"`
	TickSize     float64 `json:"tick_size"`
	ContractSize float64 `json:"contract_size"`
}

type Position struct {
	Ticket     int64   `json:"ticket"`
	Symbol     string  `json:"symbol"`
	Side       Side    `json:"side"`
	Volume     float64 `json:"volume"`
	EntryPrice float64 `json:"entry_price"`
	SL         float64 `json:"sl"`
	TP         float64 `json:"tp"`
	Swap       float64 `json:"swap"`
	Profit     float64 `json:"profit"`
}

// OrderRequest is what the Go layer sends to the C++ gateway / python.
type OrderRequest struct {
	ClientID string  `json:"client_id"`
	Symbol   string  `json:"symbol"`
	Side     Side    `json:"side"`
	Volume   float64 `json:"volume"`
	SL       float64 `json:"sl"`
	TP       float64 `json:"tp"`
	Comment  string  `json:"comment"`
	Magic    int64   `json:"magic"`
}

// OrderResult is the execution confirmation.
type OrderResult struct {
	OK           bool    `json:"ok"`
	Retcode      int     `json:"retcode"`
	RetcodeDesc  string  `json:"retcode_desc"`
	Ticket       *int64  `json:"ticket"`
	Price        *float64 `json:"price"`
	Volume       *float64 `json:"volume"`
	SpreadPoints *float64 `json:"spread_points"`
	LatencyMs    float64 `json:"latency_ms"`
}

// RiskDecision is produced by the risk engine.
type RiskDecision struct {
	Approved   bool
	Volume     float64
	SL         float64
	TP         float64
	Reasons    []string // why blocked (if any)
	Code       string   // machine code for risk_events
	Severity   string   // INFO|WARN|BLOCK|CRITICAL
	EvaluatedAt time.Time
}
