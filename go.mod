module github.com/exness-mt5/trading-platform

go 1.22

// The core platform builds with the standard library only (offline-friendly).
// The PostgreSQL store is behind the `postgres` build tag and pulls pgx:
//   go build -tags postgres ./...
// require github.com/jackc/pgx/v5 v5.6.0
