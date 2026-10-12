package logging

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"sync"
	"time"
)

// Entry represents a structured Cloud Logging v2 JSON payload.
type Entry struct {
	Timestamp string         `json:"timestamp"`
	Severity  string         `json:"severity"`
	Message   string         `json:"message"`
	Component string         `json:"component"`
	LogName   string         `json:"logName"`
	Table     string         `json:"table,omitempty"`
	CrudMode  string         `json:"crudMode,omitempty"`
	Fields    map[string]any `json:"fields,omitempty"`
}

// Logger emits structured JSON log entries compatible with Google Cloud Logging v2.
type Logger struct {
	mu          sync.Mutex
	project     string
	logName     string
	component   string
	minSeverity int
	out         io.Writer
}

// ParseSeverity maps Cloud Logging severity strings to numeric levels.
func ParseSeverity(s string) int {
	switch strings.ToUpper(strings.TrimSpace(s)) {
	case "DEBUG":
		return 100
	case "INFO", "":
		return 200
	case "NOTICE":
		return 300
	case "WARNING", "WARN":
		return 400
	case "ERROR":
		return 500
	case "CRITICAL", "FATAL":
		return 600
	default:
		return 200
	}
}

// NormalizeSeverity canonicalizes a severity string for Cloud Logging v2.
func NormalizeSeverity(s string) string {
	switch strings.ToUpper(strings.TrimSpace(s)) {
	case "DEBUG":
		return "DEBUG"
	case "INFO", "":
		return "INFO"
	case "NOTICE":
		return "NOTICE"
	case "WARNING", "WARN":
		return "WARNING"
	case "ERROR":
		return "ERROR"
	case "CRITICAL", "FATAL":
		return "CRITICAL"
	default:
		return "INFO"
	}
}

// FullLogName formats a Cloud Logging v2 resource path ("projects/<project>/logs/<logName>").
func FullLogName(project, logName string) string {
	if logName == "" {
		logName = "demo-streamer"
	}
	if strings.HasPrefix(logName, "projects/") || project == "" {
		return logName
	}
	return fmt.Sprintf("projects/%s/logs/%s", project, logName)
}

// NewLogger creates a structured Cloud Logging v2 JSON writer.
func NewLogger(project, logName, component, minSeverity string, out io.Writer) *Logger {
	if out == nil {
		out = os.Stderr
	}
	if component == "" {
		component = "demo-streamer"
	}
	return &Logger{
		project:     project,
		logName:     FullLogName(project, logName),
		component:   component,
		minSeverity: ParseSeverity(minSeverity),
		out:         out,
	}
}

// Log writes a structured JSON entry if severity >= minSeverity.
func (l *Logger) Log(severity, message string, fields map[string]any) {
	if l == nil {
		return
	}
	normSev := NormalizeSeverity(severity)
	if ParseSeverity(normSev) < l.minSeverity {
		return
	}
	entry := Entry{
		Timestamp: time.Now().UTC().Format(time.RFC3339Nano),
		Severity:  normSev,
		Message:   message,
		Component: l.component,
		LogName:   l.logName,
		Fields:    fields,
	}
	if fields != nil {
		if tbl, ok := fields["table"].(string); ok {
			entry.Table = tbl
		}
		if cm, ok := fields["crud_mode"].(string); ok {
			entry.CrudMode = cm
		}
	}
	b, err := json.Marshal(entry)
	if err != nil {
		return
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	_, _ = l.out.Write(append(b, '\n'))
}

func (l *Logger) Debug(msg string, fields map[string]any) { l.Log("DEBUG", msg, fields) }
func (l *Logger) Info(msg string, fields map[string]any)  { l.Log("INFO", msg, fields) }
func (l *Logger) Warn(msg string, fields map[string]any)  { l.Log("WARNING", msg, fields) }
func (l *Logger) Error(msg string, fields map[string]any) { l.Log("ERROR", msg, fields) }
