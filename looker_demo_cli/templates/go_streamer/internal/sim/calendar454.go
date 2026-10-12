package sim

import (
	"fmt"
	"time"

	"demo-streamer/internal/model"
)

func FyEndSaturday(fy int) time.Time {
	t := time.Date(fy+1, time.February, 3, 0, 0, 0, 0, time.UTC)
	return t.AddDate(0, 0, -int((t.Weekday()+1)%7))
}

func EmitRetailCalendar454(fromFY, toFY int, emit func(any)) {
	periodMaxWeek := [12]int{4, 9, 13, 17, 22, 26, 30, 35, 39, 43, 48, 53}

	for fy := fromFY; fy <= toFY; fy++ {
		start := FyEndSaturday(fy - 1).AddDate(0, 0, 1)
		end := FyEndSaturday(fy)
		dayIdx := 0
		for cur := start; !cur.After(end); cur = cur.AddDate(0, 0, 1) {
			wk := dayIdx/7 + 1
			dayIdx++
			pNum := 12
			for i, maxW := range periodMaxWeek {
				if wk <= maxW {
					pNum = i + 1
					break
				}
			}
			qNum := (pNum-1)/3 + 1
			season := "Spring"
			seasonNum := int64(1)
			if qNum >= 3 {
				season = "Fall"
				seasonNum = 2
			}
			prev := cur.AddDate(0, 0, -364)
			prevDateInt := int64(prev.Year()*10000 + int(prev.Month())*100 + prev.Day())

			emit(model.RetailCalendar454Row{
				ReferenceDate:          model.FmtDate(cur),
				FiscalYear:             fmt.Sprintf("FY%d", fy),
				FiscalYearNum:          int64(fy),
				FiscalQuarterOfYear:    fmt.Sprintf("Q%d", qNum),
				FiscalQuarterOfYearNum: int64(qNum),
				FiscalWeekOfYear:       fmt.Sprintf("W%02d", wk),
				FiscalWeekOfYearNum:    int64(wk),
				FiscalPeriodOfYear:     fmt.Sprintf("P%02d", pNum),
				FiscalPeriodOfYearNum:  int64(pNum),
				Season:                 season,
				SeasonNum:              seasonNum,
				PrevCustomDate:         prevDateInt,
				PrevCustomWeek:         int64(wk),
			})
		}
	}
}
