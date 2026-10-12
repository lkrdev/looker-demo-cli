package bq

import (
	"fmt"
	"strings"

	"cloud.google.com/go/bigquery/storage/apiv1/storagepb"
)

var TableClauses = map[string]string{
	"products":            "CLUSTER BY department, category, brand",
	"users":               "PARTITION BY DATE(created_at) CLUSTER BY country, state, traffic_source",
	"inventory_items":     "PARTITION BY created_at CLUSTER BY product_brand, product_category, product_id\nOPTIONS (max_staleness = INTERVAL 0 SECOND)",
	"order_items":         "PARTITION BY DATE(created_at) CLUSTER BY status, user_id, order_id\nOPTIONS (max_staleness = INTERVAL 0 SECOND)",
	"events":              "PARTITION BY DATE(created_at) CLUSTER BY event_type, traffic_source, user_id",
	"ad_events":           "PARTITION BY created_at CLUSTER BY keyword_id, event_type",
	"transaction_detail":  "PARTITION BY DATE(created_at) CLUSTER BY order_id\nOPTIONS (max_staleness = INTERVAL 0 SECOND)",
	"retail_calendar_454": "CLUSTER BY fiscal_year, season",
}

var TablePK = map[string]string{
	"inventory_items":    "id",
	"order_items":        "id",
	"transaction_detail": "order_id",
}

func BqFieldDef(f *storagepb.TableFieldSchema) string {
	if f.Name == "location" || strings.HasSuffix(f.Name, "_geom") {
		return "GEOGRAPHY"
	}
	switch f.Type {
	case storagepb.TableFieldSchema_INT64:
		return "INT64"
	case storagepb.TableFieldSchema_DOUBLE:
		return "FLOAT64"
	case storagepb.TableFieldSchema_TIMESTAMP:
		return "TIMESTAMP"
	case storagepb.TableFieldSchema_STRING:
		if strings.HasSuffix(f.Name, "_date") || f.Name == "created_at" || f.Name == "shipped_at" || f.Name == "delivered_at" {
			return "DATE"
		}
		return "STRING"
	case storagepb.TableFieldSchema_STRUCT:
		var inner []string
		for _, child := range f.Fields {
			inner = append(inner, fmt.Sprintf("%s %s", child.Name, BqFieldDef(child)))
		}
		res := fmt.Sprintf("STRUCT<\n    %s\n  >", strings.Join(inner, ",\n    "))
		if f.Mode == storagepb.TableFieldSchema_REPEATED {
			res = "ARRAY<" + res + ">"
		}
		return res
	default:
		return "STRING"
	}
}

func TableDDL(project, dataset string) string {
	var sb strings.Builder
	fmt.Fprintf(&sb, "CREATE SCHEMA IF NOT EXISTS `%s.%s`;\n\n", project, dataset)
	for _, tbl := range AllTables {
		sch := ProtoSchemaForTable(tbl)
		var colDefs []string
		for _, f := range sch.Fields {
			if f.Name == "_CHANGE_TYPE" {
				continue
			}
			notNull := ""
			if pk := TablePK[tbl]; pk != "" && f.Name == pk {
				notNull = " NOT NULL"
			}
			colDefs = append(colDefs, fmt.Sprintf("  %s %s%s", f.Name, BqFieldDef(f), notNull))
		}
		if pk := TablePK[tbl]; pk != "" {
			colDefs = append(colDefs, fmt.Sprintf("  PRIMARY KEY (%s) NOT ENFORCED", pk))
		}
		clause := ""
		if c, ok := TableClauses[tbl]; ok {
			clause = " " + c
		}
		fmt.Fprintf(&sb, "CREATE TABLE IF NOT EXISTS `%s.%s.%s` (\n%s\n)%s;\n\n",
			project, dataset, tbl, strings.Join(colDefs, ",\n"), clause)
	}
	return sb.String()
}

func DropTablesDDL(project, dataset string) string {
	var sb strings.Builder
	for _, tbl := range AllTables {
		fmt.Fprintf(&sb, "DROP TABLE IF EXISTS `%s.%s.%s`;\n", project, dataset, tbl)
	}
	return sb.String()
}
