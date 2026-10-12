package bq

import (
	"context"
	"fmt"
	"strings"

	"demo-streamer/internal/model"

	"cloud.google.com/go/bigquery/storage/managedwriter"
)

// StorageWriteSink streams rows directly to BigQuery DefaultStream using multiplexed connections
// and native CDC UPSERT/DELETE or INSERT-only semantics.
type StorageWriteSink struct {
	ctx      context.Context
	project  string
	dataset  string
	crudMode string
	client   *managedwriter.Client
	streams  map[string]*managedwriter.ManagedStream
	appendQ  map[string][]any
	cdcQ     map[string]map[int64]any
}

func NewStorageWriteSink(ctx context.Context, project, dataset string) (*StorageWriteSink, error) {
	return NewStorageWriteSinkWithMode(ctx, project, dataset, "upsert_cdc")
}

func NewStorageWriteSinkWithMode(ctx context.Context, project, dataset, crudMode string) (*StorageWriteSink, error) {
	client, err := managedwriter.NewClient(ctx, project, managedwriter.WithMultiplexing())
	if err != nil {
		return nil, fmt.Errorf("managedwriter.NewClient: %w", err)
	}
	if crudMode == "" {
		crudMode = "upsert_cdc"
	}
	return &StorageWriteSink{
		ctx:      ctx,
		project:  project,
		dataset:  dataset,
		crudMode: crudMode,
		client:   client,
		streams:  make(map[string]*managedwriter.ManagedStream),
		appendQ:  make(map[string][]any),
		cdcQ:     make(map[string]map[int64]any),
	}, nil
}

func (s *StorageWriteSink) Emit(row any) {
	tbl, pk, isCDC := model.RowTable(row)
	if tbl == "" {
		return
	}
	if isCDC && strings.ToLower(s.crudMode) != "insert_only" {
		if s.cdcQ[tbl] == nil {
			s.cdcQ[tbl] = make(map[int64]any)
		}
		// Deduplicate by primary key within the tick batch so each PK appears once per AppendRows call.
		s.cdcQ[tbl][pk] = row
	} else {
		s.appendQ[tbl] = append(s.appendQ[tbl], row)
	}
}

func (s *StorageWriteSink) streamFor(table string) (*managedwriter.ManagedStream, error) {
	if ms, ok := s.streams[table]; ok {
		return ms, nil
	}
	descs, err := GetTableDescriptors()
	if err != nil {
		return nil, err
	}
	td := descs[table]
	dest := fmt.Sprintf("projects/%s/datasets/%s/tables/%s", s.project, s.dataset, table)
	ms, err := s.client.NewManagedStream(s.ctx,
		managedwriter.WithType(managedwriter.DefaultStream),
		managedwriter.WithDestinationTable(dest),
		managedwriter.WithSchemaDescriptor(td.DP),
	)
	if err != nil {
		return nil, fmt.Errorf("NewManagedStream(%s): %w", table, err)
	}
	s.streams[table] = ms
	return ms, nil
}

func (s *StorageWriteSink) Flush() error {
	for _, tbl := range AllTables {
		var rows []any
		var isCDC bool
		if m := s.cdcQ[tbl]; len(m) > 0 {
			isCDC = true
			rows = make([]any, 0, len(m))
			for _, r := range m {
				rows = append(rows, r)
			}
			clear(s.cdcQ[tbl])
		} else if list := s.appendQ[tbl]; len(list) > 0 {
			rows = list
			s.appendQ[tbl] = nil
		}
		if len(rows) == 0 {
			continue
		}
		ms, err := s.streamFor(tbl)
		if err != nil {
			return err
		}
		const batchSize = 500
		for i := 0; i < len(rows); i += batchSize {
			end := min(i+batchSize, len(rows))
			encoded := make([][]byte, 0, end-i)
			for _, r := range rows[i:end] {
				ct := model.ResolveChangeType(r, isCDC, s.crudMode)
				b, err := RowToProtoBytesWithChangeType(tbl, r, ct)
				if err != nil {
					return err
				}
				encoded = append(encoded, b)
			}
			res, err := ms.AppendRows(s.ctx, encoded)
			if err != nil {
				return fmt.Errorf("AppendRows(%s): %w", tbl, err)
			}
			if _, err := res.GetResult(s.ctx); err != nil {
				return fmt.Errorf("AppendRows result(%s): %w", tbl, err)
			}
		}
	}
	return nil
}

func (s *StorageWriteSink) Close() error {
	for _, ms := range s.streams {
		_ = ms.Close()
	}
	return s.client.Close()
}
