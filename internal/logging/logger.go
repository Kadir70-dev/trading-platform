// Package logging provides a structured slog logger writing to stdout and a file.
package logging

import (
	"io"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
)

func New(level, dir, name string) *slog.Logger {
	_ = os.MkdirAll(dir, 0o755)
	var w io.Writer = os.Stdout
	if f, err := os.OpenFile(filepath.Join(dir, name),
		os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644); err == nil {
		w = io.MultiWriter(os.Stdout, f)
	}
	h := slog.NewJSONHandler(w, &slog.HandlerOptions{Level: parseLevel(level)})
	return slog.New(h)
}

func parseLevel(s string) slog.Level {
	switch strings.ToUpper(s) {
	case "DEBUG":
		return slog.LevelDebug
	case "WARN", "WARNING":
		return slog.LevelWarn
	case "ERROR":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}
