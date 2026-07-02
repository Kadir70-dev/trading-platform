// Package config loads platform configuration from environment variables,
// optionally seeded from a .env file. It is the Go-side mirror of python/config.py.
package config

import (
	"bufio"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	Broker      string
	AccountMode string
	Leverage    int

	Symbols       []string
	DefaultSymbol string
	Timeframe     string
	DefaultLot    float64
	MagicNumber   int64

	// Risk
	MaxRiskPct       float64
	MinRR            float64
	MaxDailyLossPct  float64
	MaxDrawdownPct   float64
	MaxOpenTrades    int
	MaxSymbolExposure int
	MaxSpreadPoints  int
	EmergencyStop    bool

	// Endpoints
	PythonServiceAddr string
	CppGatewayAddr    string
	GoHTTPAddr        string
	DatabaseURL       string

	LogLevel string
	LogDir   string

	SchedulerIntervalSec int
	WarmupBars           int
}

// LoadDotEnv reads KEY=VALUE lines from path into the process environment
// without overriding variables that are already set.
func LoadDotEnv(path string) {
	f, err := os.Open(path)
	if err != nil {
		return
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		k = strings.TrimSpace(k)
		v = strings.TrimSpace(v)
		// Strip inline comments (" #" / "\t#") on unquoted values so that
		// `KEY=value   # note` yields "value", matching dotenv conventions.
		if !strings.HasPrefix(v, `"`) && !strings.HasPrefix(v, `'`) {
			for _, sep := range []string{" #", "\t#"} {
				if idx := strings.Index(v, sep); idx >= 0 {
					v = strings.TrimSpace(v[:idx])
				}
			}
		}
		v = strings.Trim(v, `"'`)
		if _, exists := os.LookupEnv(k); !exists {
			_ = os.Setenv(k, v)
		}
	}
}

func Load() *Config {
	LoadDotEnv(".env")
	c := &Config{
		Broker:               getStr("BROKER", "EXNESS"),
		AccountMode:          strings.ToUpper(getStr("ACCOUNT_MODE", "DEMO")),
		Leverage:             getInt("LEVERAGE", 2000),
		Symbols:              splitCSV(getStr("SYMBOLS", "XAUUSD,EURUSD,GBPUSD,USDJPY")),
		DefaultSymbol:        getStr("DEFAULT_SYMBOL", "XAUUSD"),
		Timeframe:            getStr("TIMEFRAME", "M1"),
		DefaultLot:           getFloat("DEFAULT_LOT", 0.01),
		MagicNumber:          int64(getInt("MAGIC_NUMBER", 20260630)),
		MaxRiskPct:           getFloat("MAX_RISK_PCT", 1.0),
		MinRR:                getFloat("MIN_RR", 2.0),
		MaxDailyLossPct:      getFloat("MAX_DAILY_LOSS_PCT", 5.0),
		MaxDrawdownPct:       getFloat("MAX_DRAWDOWN_PCT", 20.0),
		MaxOpenTrades:        getInt("MAX_OPEN_TRADES", 5),
		MaxSymbolExposure:    getInt("MAX_SYMBOL_EXPOSURE", 2),
		MaxSpreadPoints:      getInt("MAX_SPREAD_POINTS", 50),
		EmergencyStop:        getBool("EMERGENCY_STOP", false),
		PythonServiceAddr:    getStr("PYTHON_SERVICE_ADDR", "127.0.0.1:8800"),
		CppGatewayAddr:       getStr("CPP_GATEWAY_ADDR", "127.0.0.1:8900"),
		GoHTTPAddr:           getStr("GO_HTTP_ADDR", "127.0.0.1:8080"),
		DatabaseURL:          getStr("DATABASE_URL", ""),
		LogLevel:             strings.ToUpper(getStr("LOG_LEVEL", "INFO")),
		LogDir:               getStr("LOG_DIR", "./logs"),
		SchedulerIntervalSec: getInt("SCHEDULER_INTERVAL_SEC", 60),
		WarmupBars:           getInt("WARMUP_BARS", 200),
	}
	return c
}

func getStr(k, def string) string {
	if v, ok := os.LookupEnv(k); ok && v != "" {
		return v
	}
	return def
}
func getInt(k string, def int) int {
	if v, ok := os.LookupEnv(k); ok {
		if n, err := strconv.Atoi(strings.TrimSpace(v)); err == nil {
			return n
		}
	}
	return def
}
func getFloat(k string, def float64) float64 {
	if v, ok := os.LookupEnv(k); ok {
		if n, err := strconv.ParseFloat(strings.TrimSpace(v), 64); err == nil {
			return n
		}
	}
	return def
}
func getBool(k string, def bool) bool {
	if v, ok := os.LookupEnv(k); ok {
		b, err := strconv.ParseBool(strings.TrimSpace(v))
		if err == nil {
			return b
		}
	}
	return def
}
func splitCSV(s string) []string {
	parts := strings.Split(s, ",")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		if p = strings.TrimSpace(strings.ToUpper(p)); p != "" {
			out = append(out, p)
		}
	}
	return out
}
