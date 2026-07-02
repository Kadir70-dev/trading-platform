// Package repositories provides Store implementations.
//
// FileStore is the default, dependency-free persistence layer: it appends
// JSON lines to files under the log directory and hands out monotonic IDs.
// It lets the platform run and be audited without a database. For production,
// build with `-tags postgres` to use the PostgreSQL store instead.
package repositories

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"

	"github.com/exness-mt5/trading-platform/internal/models"
)

type FileStore struct {
	dir    string
	mu     sync.Mutex
	seq    int64
	files  map[string]*os.File
}

func NewFileStore(dir string) (*FileStore, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	return &FileStore{dir: dir, files: map[string]*os.File{}}, nil
}

func (s *FileStore) nextID() int64 { return atomic.AddInt64(&s.seq, 1) }

func (s *FileStore) write(table string, rec any) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	f, ok := s.files[table]
	if !ok {
		var err error
		f, err = os.OpenFile(filepath.Join(s.dir, table+".jsonl"),
			os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
		if err != nil {
			return err
		}
		s.files[table] = f
	}
	b, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	_, err = f.Write(append(b, '\n'))
	return err
}

func (s *FileStore) SaveSignal(sig *models.Signal) (int64, error) {
	id := s.nextID()
	return id, s.write("signals", map[string]any{"id": id, "ts": now(), "signal": sig})
}

func (s *FileStore) SaveOrder(req models.OrderRequest, signalID int64, status string) (int64, error) {
	id := s.nextID()
	return id, s.write("orders", map[string]any{
		"id": id, "ts": now(), "signal_id": signalID, "status": status, "order": req})
}

func (s *FileStore) SaveExecution(orderID int64, res *models.OrderResult, symbol string, side models.Side) error {
	return s.write("executions", map[string]any{
		"ts": now(), "order_id": orderID, "symbol": symbol, "side": side, "result": res})
}

func (s *FileStore) SaveRiskEvent(severity, code, symbol, message string, ctx map[string]any) error {
	return s.write("risk_events", map[string]any{
		"ts": now(), "severity": severity, "code": code, "symbol": symbol,
		"message": message, "context": ctx})
}

func (s *FileStore) SaveAccountSnapshot(a *models.Account) error {
	return s.write("accounts", map[string]any{"ts": now(), "account": a})
}

func (s *FileStore) Audit(actor, action, entity, entityID string, detail map[string]any) error {
	return s.write("audit_logs", map[string]any{
		"ts": now(), "actor": actor, "action": action, "entity": entity,
		"entity_id": entityID, "detail": detail})
}

func (s *FileStore) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, f := range s.files {
		_ = f.Close()
	}
	return nil
}

func now() string { return time.Now().UTC().Format(time.RFC3339Nano) }
