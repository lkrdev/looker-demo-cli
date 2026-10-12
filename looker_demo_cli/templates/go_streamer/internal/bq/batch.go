package bq

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"demo-streamer/internal/model"
)

// BatchLoadSink writes NDJSON temp files per table (deduplicating CDC tables by PK in memory) and runs free bq load jobs.
type BatchLoadSink struct {
	project  string
	dataset  string
	crudMode string
	tmpDir   string
	files    map[string]*os.File
	writers  map[string]*bufio.Writer
	encs     map[string]*json.Encoder
	cdcRows  map[string]map[int64]any
}

func NewBatchLoadSink(project, dataset string) (*BatchLoadSink, error) {
	return NewBatchLoadSinkWithMode(project, dataset, "upsert_cdc")
}

func NewBatchLoadSinkWithMode(project, dataset, crudMode string) (*BatchLoadSink, error) {
	tmpDir, err := os.MkdirTemp("", "demo-streamer-batch-*")
	if err != nil {
		return nil, err
	}
	if crudMode == "" {
		crudMode = "upsert_cdc"
	}
	return &BatchLoadSink{
		project:  project,
		dataset:  dataset,
		crudMode: crudMode,
		tmpDir:   tmpDir,
		files:    make(map[string]*os.File),
		writers:  make(map[string]*bufio.Writer),
		encs:     make(map[string]*json.Encoder),
		cdcRows:  make(map[string]map[int64]any),
	}, nil
}

func (b *BatchLoadSink) writeRow(table string, row any) error {
	enc, ok := b.encs[table]
	if !ok {
		f, err := os.Create(filepath.Join(b.tmpDir, table+".ndjson"))
		if err != nil {
			return err
		}
		bw := bufio.NewWriterSize(f, 256*1024)
		enc = json.NewEncoder(bw)
		enc.SetEscapeHTML(false)
		b.files[table] = f
		b.writers[table] = bw
		b.encs[table] = enc
	}
	return enc.Encode(row)
}

func (b *BatchLoadSink) Emit(row any) {
	tbl, pk, isCDC := model.RowTable(row)
	if tbl == "" {
		return
	}
	if isCDC && strings.ToLower(b.crudMode) != "insert_only" {
		if b.cdcRows[tbl] == nil {
			b.cdcRows[tbl] = make(map[int64]any)
		}
		if model.ResolveChangeType(row, isCDC, b.crudMode) == "DELETE" {
			delete(b.cdcRows[tbl], pk)
			return
		}
		// Keep only final state per PK across the backfill so bq load writes zero duplicate primary keys.
		b.cdcRows[tbl][pk] = row
		return
	}
	_ = b.writeRow(tbl, row)
}

func (b *BatchLoadSink) CDCBufferLen(table string) int {
	return len(b.cdcRows[table])
}

func (b *BatchLoadSink) FlushAndLoad() error {
	defer os.RemoveAll(b.tmpDir)
	for _, tbl := range AllTables {
		if m := b.cdcRows[tbl]; len(m) > 0 {
			for _, r := range m {
				if err := b.writeRow(tbl, r); err != nil {
					return err
				}
			}
			clear(b.cdcRows[tbl])
		}
	}
	for tbl, bw := range b.writers {
		if err := bw.Flush(); err != nil {
			return fmt.Errorf("flush %s: %w", tbl, err)
		}
		if err := b.files[tbl].Close(); err != nil {
			return fmt.Errorf("close %s: %w", tbl, err)
		}
	}
	for _, tbl := range AllTables {
		path := filepath.Join(b.tmpDir, tbl+".ndjson")
		fi, err := os.Stat(path)
		if err != nil || fi.Size() == 0 {
			continue
		}
		target := fmt.Sprintf("%s:%s.%s", b.project, b.dataset, tbl)
		fmt.Fprintf(os.Stderr, "Loading %s (%d bytes)...\n", target, fi.Size())
		cmd := exec.Command("bq", "load", "--project_id="+b.project, "--source_format=NEWLINE_DELIMITED_JSON", target, path)
		cmd.Stdout = os.Stderr
		cmd.Stderr = os.Stderr
		if err := cmd.Run(); err != nil {
			return fmt.Errorf("bq load %s failed: %w", target, err)
		}
	}
	return nil
}
