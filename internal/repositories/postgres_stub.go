//go:build !postgres

package repositories

import (
	"errors"

	"github.com/exness-mt5/trading-platform/internal/interfaces"
)

// newPostgresStore is a no-op unless the binary is built with `-tags postgres`.
func newPostgresStore(_ string) (interfaces.Store, error) {
	return nil, errors.New("postgres support not compiled in (build with -tags postgres)")
}
