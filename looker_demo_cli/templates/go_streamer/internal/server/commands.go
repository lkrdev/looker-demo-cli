package server

import (
	"bufio"
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"time"

	"demo-streamer/internal/model"
)

type CommandLogEntry struct {
	Timestamp string   `json:"timestamp"`
	Command   string   `json:"command"`
	Args      []string `json:"args"`
	PID       int      `json:"pid"`
}

func CommandLogPath(statePath string) string {
	return filepath.Join(filepath.Dir(statePath), "commands.jsonl")
}

func RecordCommandRun(statePath, cmd string, args []string) {
	f, err := os.OpenFile(CommandLogPath(statePath), os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0644)
	if err != nil {
		return
	}
	defer f.Close()
	b, err := json.Marshal(CommandLogEntry{
		Timestamp: model.FmtTS(time.Now()),
		Command:   cmd,
		Args:      args,
		PID:       os.Getpid(),
	})
	if err == nil {
		_, _ = f.Write(append(b, '\n'))
	}
}

func ReadCommandLog(statePath string) []CommandLogEntry {
	b, err := os.ReadFile(CommandLogPath(statePath))
	if err != nil {
		return nil
	}
	var entries []CommandLogEntry
	sc := bufio.NewScanner(bytes.NewReader(b))
	for sc.Scan() {
		var e CommandLogEntry
		if json.Unmarshal(sc.Bytes(), &e) == nil && e.Timestamp != "" {
			entries = append(entries, e)
		}
	}
	slices.Reverse(entries)
	return entries[:min(len(entries), 200)]
}
