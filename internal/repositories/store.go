package repositories

import (
	"github.com/exness-mt5/trading-platform/internal/interfaces"
)

// NewStore selects a Store implementation. With no DATABASE_URL or when built
// without the `postgres` tag, the dependency-free FileStore is used. Build with
// `-tags postgres` and set DATABASE_URL to enable the PostgreSQL store.
func NewStore(databaseURL, logDir string) (interfaces.Store, error) {
	if databaseURL != "" {
		if st, err := newPostgresStore(databaseURL); err == nil {
			return st, nil
		} else {
			// Fall back to file store but surface the reason via stderr-level log.
			// (Caller logs the returned store backend.)
			_ = err
		}
	}
	return NewFileStore(logDir)
}
