// Command trader is the CLI + daemon entrypoint for the Exness MT5 platform.
//
//	trader connect       verify connectivity & print account identity
//	trader account       account dashboard
//	trader positions     list open positions
//	trader buy  SYM      open a BUY  (flags: -lot -sl -tp)
//	trader sell SYM      open a SELL (flags: -lot -sl -tp)
//	trader close TICKET  close one position (flag: -vol for partial)
//	trader close-all     close every open position
//	trader strategy SYM  print the current signal
//	trader backtest      run the Python backtester (flags: -sym -tf -bars)
//	trader run           start the scheduler + health/metrics server
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/exec"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/exness-mt5/trading-platform/internal/config"
	"github.com/exness-mt5/trading-platform/internal/interfaces"
	"github.com/exness-mt5/trading-platform/internal/logging"
	"github.com/exness-mt5/trading-platform/internal/models"
	"github.com/exness-mt5/trading-platform/internal/repositories"
	"github.com/exness-mt5/trading-platform/services/account"
	"github.com/exness-mt5/trading-platform/services/execution"
	"github.com/exness-mt5/trading-platform/services/marketdata"
	"github.com/exness-mt5/trading-platform/services/positions"
	"github.com/exness-mt5/trading-platform/services/risk"
	"github.com/exness-mt5/trading-platform/services/scheduler"
)

func main() {
	if len(os.Args) < 2 {
		usage()
		os.Exit(1)
	}
	cfg := config.Load()
	log := logging.New(cfg.LogLevel, cfg.LogDir, "trader.log")

	md := marketdata.New(cfg.PythonServiceAddr)
	acct := account.New(cfg.PythonServiceAddr)
	pos := positions.New(cfg.PythonServiceAddr)
	riskEng := risk.New(cfg)

	ctx := context.Background()
	cmd := os.Args[1]
	args := os.Args[2:]

	switch cmd {
	case "connect":
		cmdConnect(ctx, cfg, acct)
	case "account":
		cmdAccount(ctx, acct)
	case "positions":
		cmdPositions(ctx, pos)
	case "buy":
		cmdManualOrder(ctx, cfg, md, chooseExecutor(ctx, cfg), models.Buy, args)
	case "sell":
		cmdManualOrder(ctx, cfg, md, chooseExecutor(ctx, cfg), models.Sell, args)
	case "close":
		cmdClose(ctx, pos, args)
	case "close-all":
		n, err := pos.CloseAll(ctx)
		exitErr(err)
		fmt.Printf("closed %d positions\n", n)
	case "strategy":
		cmdStrategy(ctx, cfg, md, args)
	case "backtest":
		cmdBacktest(cfg, args)
	case "run":
		cmdRun(cfg, log, md, acct, pos, riskEng)
	default:
		usage()
		os.Exit(1)
	}
}

func chooseExecutor(ctx context.Context, cfg *config.Config) interfaces.Executor {
	gw := execution.New(cfg.CppGatewayAddr)
	hctx, cancel := context.WithTimeout(ctx, 2*time.Second)
	defer cancel()
	if err := gw.HealthCheck(hctx); err == nil {
		fmt.Println("[exec] using C++ execution gateway:", cfg.CppGatewayAddr)
		return gw
	}
	fmt.Println("[exec] C++ gateway unavailable, using direct Python executor")
	return execution.NewDirect(cfg.PythonServiceAddr)
}

func cmdConnect(ctx context.Context, cfg *config.Config, acct interfaces.AccountService) {
	a, err := acct.Account(ctx)
	exitErr(err)
	fmt.Println("==================== CONNECTION ====================")
	fmt.Printf("Broker:    %s\n", a.Broker)
	fmt.Printf("Server:    %s\n", a.Server)
	fmt.Printf("Account:   %d (%s)\n", a.Login, a.Mode)
	fmt.Printf("Leverage:  1:%d\n", a.Leverage)
	fmt.Printf("Balance:   %.2f %s\n", a.Balance, a.Currency)
	fmt.Printf("Equity:    %.2f %s\n", a.Equity, a.Currency)
	if cfg.Leverage != a.Leverage {
		fmt.Printf("WARNING: configured leverage 1:%d != broker 1:%d\n", cfg.Leverage, a.Leverage)
	}
	if cfg.AccountMode != a.Mode {
		fmt.Printf("WARNING: configured mode %s != broker %s\n", cfg.AccountMode, a.Mode)
	}
	fmt.Println("===================================================")
}

func cmdAccount(ctx context.Context, acct interfaces.AccountService) {
	a, err := acct.Account(ctx)
	exitErr(err)
	printJSON(a)
}

func cmdPositions(ctx context.Context, pos interfaces.PositionService) {
	ps, err := pos.Positions(ctx)
	exitErr(err)
	if len(ps) == 0 {
		fmt.Println("no open positions")
		return
	}
	fmt.Printf("%-10s %-8s %-6s %-8s %-12s %-12s %-10s\n",
		"TICKET", "SYMBOL", "SIDE", "VOLUME", "ENTRY", "SL/TP", "PROFIT")
	for _, p := range ps {
		fmt.Printf("%-10d %-8s %-6s %-8.2f %-12.5f %.5f/%.5f %-10.2f\n",
			p.Ticket, p.Symbol, p.Side, p.Volume, p.EntryPrice, p.SL, p.TP, p.Profit)
	}
}

func cmdManualOrder(ctx context.Context, cfg *config.Config, md interfaces.MarketData,
	executor interfaces.Executor, side models.Side, args []string) {
	fs := newFlagSet()
	lot := fs.Float64("lot", cfg.DefaultLot, "volume in lots")
	sl := fs.Float64("sl", 0, "stop loss price (0 = derive from ATR)")
	tp := fs.Float64("tp", 0, "take profit price (0 = derive from ATR)")
	symbol := parseSymbol(fs, args, cfg.DefaultSymbol)

	slv, tpv := *sl, *tp
	if slv == 0 || tpv == 0 {
		// derive mandatory stops from a fresh signal's ATR-based levels
		sig, err := md.Signal(ctx, symbol, cfg.Timeframe, cfg.WarmupBars)
		exitErr(err)
		dist := deriveStopDistance(sig.ATR, sig.Price)
		if side == models.Buy {
			if slv == 0 {
				slv = sig.Price - dist
			}
			if tpv == 0 {
				tpv = sig.Price + cfg.MinRR*dist
			}
		} else {
			if slv == 0 {
				slv = sig.Price + dist
			}
			if tpv == 0 {
				tpv = sig.Price - cfg.MinRR*dist
			}
		}
	}

	req := models.OrderRequest{
		ClientID: fmt.Sprintf("manual-%s-%d", side, time.Now().UnixNano()),
		Symbol:   symbol, Side: side, Volume: *lot, SL: slv, TP: tpv,
		Magic: cfg.MagicNumber, Comment: "manual",
	}
	res, err := executor.Execute(ctx, req)
	exitErr(err)
	printJSON(res)
}

func cmdClose(ctx context.Context, pos interfaces.PositionService, args []string) {
	fs := newFlagSet()
	vol := fs.Float64("vol", 0, "partial close volume (0 = full)")
	rest := splitFlags(fs, args)
	if len(rest) < 1 {
		exitErr(fmt.Errorf("usage: trader close TICKET [-vol N]"))
	}
	ticket, err := strconv.ParseInt(rest[0], 10, 64)
	exitErr(err)
	exitErr(pos.Close(ctx, ticket, *vol))
	fmt.Printf("close request sent for ticket %d\n", ticket)
}

func cmdStrategy(ctx context.Context, cfg *config.Config, md interfaces.MarketData, args []string) {
	fs := newFlagSet()
	symbol := parseSymbol(fs, args, cfg.DefaultSymbol)
	sig, err := md.Signal(ctx, symbol, cfg.Timeframe, cfg.WarmupBars)
	exitErr(err)
	printJSON(sig)
}

func cmdBacktest(cfg *config.Config, args []string) {
	fs := newFlagSet()
	sym := fs.String("sym", cfg.DefaultSymbol, "symbol")
	tf := fs.String("tf", cfg.Timeframe, "timeframe")
	bars := fs.Int("bars", 3000, "number of bars")
	_ = splitFlags(fs, args)
	c := exec.Command("python3", "backtest_cli.py",
		"--symbol", *sym, "--timeframe", *tf, "--bars", strconv.Itoa(*bars))
	c.Dir = "python"
	c.Stdout = os.Stdout
	c.Stderr = os.Stderr
	if err := c.Run(); err != nil {
		exitErr(fmt.Errorf("backtest failed (is the venv active?): %w", err))
	}
}

func cmdRun(cfg *config.Config, log *slog.Logger, md interfaces.MarketData,
	acct interfaces.AccountService, pos interfaces.PositionService, riskEng *risk.Engine) {
	store, err := repositories.NewStore(cfg.DatabaseURL, cfg.LogDir)
	exitErr(err)
	defer store.Close()

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	executor := chooseExecutor(ctx, cfg)
	sched := scheduler.New(cfg, log, md, acct, pos, riskEng, executor, store)

	// health & metrics server
	go serveHTTP(ctx, cfg, acct, pos)

	log.Info("trader run starting", "mode", cfg.AccountMode, "symbols", cfg.Symbols)
	if err := sched.Run(ctx); err != nil && err != context.Canceled {
		log.Error("scheduler exited", "err", err)
	}
}

func serveHTTP(ctx context.Context, cfg *config.Config, acct interfaces.AccountService, pos interfaces.PositionService) {
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"status": "ok", "ts": time.Now().Unix()})
	})
	mux.HandleFunc("/account", func(w http.ResponseWriter, r *http.Request) {
		a, err := acct.Account(r.Context())
		writeJSON(w, a, err)
	})
	mux.HandleFunc("/positions", func(w http.ResponseWriter, r *http.Request) {
		p, err := pos.Positions(r.Context())
		writeJSON(w, map[string]any{"positions": p}, err)
	})
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		a, err := acct.Account(r.Context())
		if err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
		ps, _ := pos.Positions(r.Context())
		fmt.Fprintf(w, "trader_balance %.2f\n", a.Balance)
		fmt.Fprintf(w, "trader_equity %.2f\n", a.Equity)
		fmt.Fprintf(w, "trader_margin_level %.2f\n", a.MarginLevel)
		fmt.Fprintf(w, "trader_open_positions %d\n", len(ps))
	})
	srv := &http.Server{Addr: cfg.GoHTTPAddr, Handler: mux}
	go func() { <-ctx.Done(); _ = srv.Close() }()
	fmt.Println("[http] health/metrics on", cfg.GoHTTPAddr)
	_ = srv.ListenAndServe()
}

func writeJSON(w http.ResponseWriter, v any, err error) {
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(v)
}

func printJSON(v any) {
	b, _ := json.MarshalIndent(v, "", "  ")
	fmt.Println(string(b))
}

func exitErr(err error) {
	if err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}

// ---- small flag / arg helpers ----

func newFlagSet() *flag.FlagSet {
	fs := flag.NewFlagSet("trader", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	return fs
}

// splitFlags collects leading positional args, parses the rest as flags, and
// appends any trailing positionals. Works for `cmd POS -flag V` layouts.
func splitFlags(fs *flag.FlagSet, args []string) []string {
	var pos []string
	i := 0
	for i < len(args) && !strings.HasPrefix(args[i], "-") {
		pos = append(pos, args[i])
		i++
	}
	_ = fs.Parse(args[i:])
	return append(pos, fs.Args()...)
}

func parseSymbol(fs *flag.FlagSet, args []string, def string) string {
	rest := splitFlags(fs, args)
	if len(rest) > 0 {
		return strings.ToUpper(rest[0])
	}
	return def
}

// deriveStopDistance returns an ATR-based stop distance, falling back to a small
// fraction of price when ATR is unavailable.
func deriveStopDistance(atr, price float64) float64 {
	if atr > 0 {
		return 1.5 * atr
	}
	return price * 0.001
}

func usage() {
	fmt.Println(`trader — Exness MT5 automated trading platform
commands:
  connect | account | positions
  buy SYM [-lot N -sl P -tp P] | sell SYM [...]
  close TICKET [-vol N] | close-all
  strategy SYM | backtest [-sym -tf -bars] | run`)
}
