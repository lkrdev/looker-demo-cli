package sim

import (
	"math"
	"math/rand/v2"
	"slices"
	"time"
)

func DowMultiplier(wd time.Weekday) float64 {
	switch wd {
	case time.Sunday:
		return 1.15
	case time.Monday:
		return 1.12
	case time.Tuesday, time.Wednesday:
		return 1.00
	case time.Thursday:
		return 1.02
	case time.Friday:
		return 0.88
	case time.Saturday:
		return 0.85
	default:
		return 1.00
	}
}

func IsMemorialDayWeekend(t time.Time) bool {
	if t.Month() != time.May || t.Day() < 23 {
		return false
	}
	switch t.Weekday() {
	case time.Saturday:
		return t.Day() >= 23 && t.Day() <= 29
	case time.Sunday:
		return t.Day() >= 24 && t.Day() <= 30
	case time.Monday:
		return t.Day() >= 25 && t.Day() <= 31
	}
	return false
}

func IsLaborDayWeekend(t time.Time) bool {
	m, d, wd := t.Month(), t.Day(), t.Weekday()
	if m == time.August && d >= 30 {
		return (wd == time.Saturday && d >= 30) || (wd == time.Sunday && d >= 31)
	}
	if m == time.September && d <= 7 {
		switch wd {
		case time.Saturday:
			return d >= 1 && d <= 5
		case time.Sunday:
			return d >= 2 && d <= 6
		case time.Monday:
			return d >= 1 && d <= 7
		}
	}
	return false
}

func IsFourthOfJuly(t time.Time) bool {
	return t.Month() == time.July && t.Day() >= 3 && t.Day() <= 5
}

func IsPeakLogistics(t time.Time) bool {
	m, d := t.Month(), t.Day()
	return (m == time.November && d >= 20) || (m == time.December && d <= 24)
}

func CalendarSeason(t time.Time) (trafficMult float64, discChance float64, isPromo bool) {
	dow := DowMultiplier(t.Weekday())
	m, d := t.Month(), t.Day()

	if IsMemorialDayWeekend(t) {
		return dow * 1.35, 0.35, true
	}
	if IsLaborDayWeekend(t) {
		return dow * 1.35, 0.35, true
	}
	if IsFourthOfJuly(t) {
		return dow * 0.70, 0.05, false
	}
	if m == time.November && d >= 20 {
		return dow * 2.50, 0.30, true
	}
	if m == time.December {
		if d <= 23 {
			return dow * 2.00, 0.15, false
		}
		if d <= 25 {
			return dow * 0.70, 0.05, false
		}
		return dow * 0.65, 0.25, true
	}
	if m == time.January && d <= 15 {
		return dow * 0.65, 0.25, true
	}
	if m == time.August && d >= 10 && d <= 28 {
		return dow * 1.20, 0.12, false
	}
	return dow, 0.05, false
}

func CategorySeasonWeight(cat string, m time.Month) float64 {
	switch cat {
	case "Swim", "Shorts":
		if m >= time.May && m <= time.August {
			return 2.5
		}
		if m >= time.November || m <= time.February {
			return 0.25
		}
	case "Outerwear & Coats", "Sweaters", "Fashion Hoodies & Sweatshirts":
		if m >= time.October || m <= time.February {
			return 2.5
		}
		if m >= time.May && m <= time.August {
			return 0.25
		}
	case "Dresses", "Skirts":
		if m >= time.April && m <= time.August {
			return 1.8
		}
		if m >= time.December || m <= time.February {
			return 0.5
		}
	}
	return 1.0
}

// MonthlyGrowthMultiplier returns a deterministic growth multiplier for the given timestamp.
// Within each year:
// - One random month experiences hyper-growth (+20% MoM).
// - Other months fluctuate with an average MoM of ~10%, never dropping by more than 5%.
// Across years, an annual compounding baseline provides steady organic expansion.
func MonthlyGrowthMultiplier(t time.Time) float64 {
	t = t.UTC()
	year, month := t.Year(), int(t.Month())
	rng := rand.New(rand.NewPCG(uint64(year), 0x5448454c4f4f4b))
	hyperMonth := 2 + rng.IntN(9) // Pick random month between Feb (2) and Oct (10)

	mult := 1.0
	for m := 2; m <= month; m++ {
		if m == hyperMonth {
			mult *= 1.20
		} else {
			// Uniform in [-0.05, 0.23], mean = +0.09, min = -0.05
			mom := -0.05 + rng.Float64()*0.28
			mult *= (1.0 + mom)
		}
	}

	yIdx := max(0, year-2016)
	base := 0.20 * math.Pow(1.30, math.Min(float64(yIdx), 10.0))
	return math.Min(6.0, base*mult)
}

// FraudAnomalyMinutes returns 1 to 3 distinct minute-of-day offsets [0, 1439]
// when fraudulent purchasing bursts occur on the given calendar date.
func FraudAnomalyMinutes(t time.Time) []int {
	t = t.UTC()
	rng := rand.New(rand.NewPCG(uint64(t.Year())*1000+uint64(t.YearDay()), 0x4652415544))
	k := 1 + rng.IntN(3) // 1, 2, or 3
	mins := make([]int, 0, k)
	for len(mins) < k {
		m := rng.IntN(1440)
		if !slices.Contains(mins, m) {
			mins = append(mins, m)
		}
	}
	return mins
}

// IsFraudAnomalyMinute reports whether a fraudulent customer anomaly is scheduled
// during the given minute timestamp.
func IsFraudAnomalyMinute(t time.Time) bool {
	t = t.UTC()
	return slices.Contains(FraudAnomalyMinutes(t), t.Hour()*60+t.Minute())
}
