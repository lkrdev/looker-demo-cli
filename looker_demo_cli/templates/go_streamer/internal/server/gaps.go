package server

import (
	"time"

	"demo-streamer/internal/model"
)

type Gap struct {
	From time.Time
	To   time.Time
}

func FindGaps(st *model.State, upTo time.Time) []Gap {
	if len(st.Intervals) == 0 {
		return nil
	}
	var gaps []Gap
	for i := 0; i < len(st.Intervals)-1; i++ {
		gapStart := st.Intervals[i].EndMin + 1
		gapEnd := st.Intervals[i+1].StartMin - 1
		if gapStart <= gapEnd {
			gaps = append(gaps, Gap{
				From: time.Unix(gapStart*60, 0).UTC(),
				To:   time.Unix(gapEnd*60, 0).UTC(),
			})
		}
	}
	lastEnd := st.Intervals[len(st.Intervals)-1].EndMin
	nowMin := upTo.UTC().Unix() / 60
	if lastEnd+1 < nowMin {
		gaps = append(gaps, Gap{
			From: time.Unix((lastEnd+1)*60, 0).UTC(),
			To:   time.Unix(nowMin*60, 0).UTC(),
		})
	}
	return gaps
}
