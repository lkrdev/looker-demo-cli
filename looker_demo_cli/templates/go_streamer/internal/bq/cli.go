package bq

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strconv"

	"demo-streamer/internal/model"
)

func RunQuery(project, sql string, jsonOutput bool, maxRows int) ([]byte, error) {
	args := []string{"query", "--use_legacy_sql=false"}
	if project != "" {
		args = append(args, "--project_id="+project)
	}
	if jsonOutput {
		args = append(args, "--format=json")
	}
	if maxRows > 0 {
		args = append(args, fmt.Sprintf("--max_rows=%d", maxRows))
	}
	args = append(args, sql)
	cmd := exec.Command("bq", args...)
	if !jsonOutput {
		cmd.Stdout = os.Stderr
		cmd.Stderr = os.Stderr
		return nil, cmd.Run()
	}
	return cmd.Output()
}

func AnalyzeDataset(dataset string, defaultSeed []byte, outPath string) error {
	var base model.SeedData
	if err := json.Unmarshal(defaultSeed, &base); err != nil {
		return err
	}

	sql := fmt.Sprintf(`
SELECT
  (SELECT array_agg(cnt ORDER BY hr) FROM (
    SELECT extract(hour from created_at) as hr, count(*) as cnt
    FROM `+"`%s.order_items`"+` GROUP BY 1
  )) as hourly_weights,
  (SELECT array_agg(struct(department, category, weight, avg_price, std_price, min_price, max_price, cost_ratio) ORDER BY department, weight DESC) FROM (
    SELECT
      department, category, count(*) as weight,
      round(avg(retail_price), 2) as avg_price,
      round(stddev(retail_price), 2) as std_price,
      round(min(retail_price), 2) as min_price,
      round(max(retail_price), 2) as max_price,
      round(avg(cost / nullif(retail_price, 0)), 4) as cost_ratio
    FROM `+"`%s.products`"+` GROUP BY department, category
  )) as categories
`, dataset, dataset)

	out, err := RunQuery("", sql, true, 500)
	if err != nil {
		return fmt.Errorf("bq query failed: %w", err)
	}

	var res []struct {
		HourlyWeights []string `json:"hourly_weights"`
		Categories    []struct {
			Department string  `json:"department"`
			Category   string  `json:"category"`
			Weight     int     `json:"weight,string"`
			AvgPrice   float64 `json:"avg_price,string"`
			StdPrice   float64 `json:"std_price,string"`
			MinPrice   float64 `json:"min_price,string"`
			MaxPrice   float64 `json:"max_price,string"`
			CostRatio  float64 `json:"cost_ratio,string"`
		} `json:"categories"`
	}
	if err := json.Unmarshal(out, &res); err != nil || len(res) == 0 {
		return fmt.Errorf("failed to parse bq output: %w", err)
	}

	if len(res[0].HourlyWeights) == 24 {
		base.HourlyWeights = make([]int, 24)
		for i, s := range res[0].HourlyWeights {
			base.HourlyWeights[i], _ = strconv.Atoi(s)
		}
	}
	if len(res[0].Categories) > 0 {
		base.Categories = make([]model.CategoryProfile, len(res[0].Categories))
		for i, c := range res[0].Categories {
			base.Categories[i] = model.CategoryProfile(c)
		}
	}

	b, err := json.MarshalIndent(base, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(outPath, b, 0644)
}
