package sim

import (
	"fmt"
	"strconv"
	"strings"
	"time"

	"demo-streamer/internal/model"
)

var (
	campaignNames = []string{
		"NA - Search - Brand", "NA - Search - Clothing", "NA - Search - Ecommerce", "NA - Search - Promo", "NA - Search - Competition",
		"NA - Display - Brand", "NA - Display - Promo", "NA - Display - Ecommerce", "NA - Video - Brand", "NA - Video - Promo",
		"EU - Search - Brand", "EU - Search - Clothing", "EU - Search - Ecommerce", "EU - Display - Brand", "EU - Display - Promo",
	}
	adHeadlines = []string{
		"A change you can see.", "A fresh approach to shopping.", "Always in style!", "Discover the difference.",
		"Experience the lifestyle.", "Feel-good shopping.", "Great stores. Great choices.", "Love shopping again.",
		"More of what you want.", "Save money. Live better.", "Shopping for all seasons.", "Shopping with style.",
	}
	adDevices = []string{"Desktop", "Desktop", "Desktop", "Desktop", "Desktop", "Tablet", "Tablet", "Mobile", "Mobile", "Macintosh", "Windows", "Other", "Linux"}
)

func (e *Engine) createCampaignTree(ts time.Time, emit func(any)) {
	cID := e.State.NextCampaignID
	e.State.NextCampaignID++

	cName := campaignNames[e.rng.IntN(len(campaignNames))]
	channel := "Search"
	if strings.Contains(cName, "Display") {
		channel = "Display"
	} else if strings.Contains(cName, "Video") {
		channel = "Video"
	}
	bidTypes := []string{"CPC", "CPC", "CPC", "Target CPA", "Enhanced CPC"}
	bidType := bidTypes[e.rng.IntN(len(bidTypes))]
	periods := []int64{30, 30, 30, 60, 90}
	period := periods[e.rng.IntN(len(periods))]
	createdDate := model.FmtDate(ts)

	emit(model.CampaignRow{
		ID:                 cID,
		AdvertisingChannel: channel,
		Amount:             round2(50 + e.rng.Float64()*9500),
		BidType:            bidType,
		CampaignName:       cName,
		Period:             strconv.FormatInt(period, 10),
		CreatedAt:          createdDate,
	})

	adTypes := []string{"Expanded Text Ad", "Expanded Text Ad", "Text Ad", "Image Ad", "Responsive Display Ad"}
	agCount := 2 + e.rng.IntN(3)
	for i := 0; i < agCount; i++ {
		agID := e.State.NextAdGroupID
		e.State.NextAdGroupID++
		cat := e.pickCategory()
		emit(model.AdGroupRow{
			AdID:       agID,
			CampaignID: cID,
			CreatedAt:  createdDate,
			Name:       cat.Category,
			Period:     period,
			AdType:     adTypes[e.rng.IntN(len(adTypes))],
			Headline:   adHeadlines[e.rng.IntN(len(adHeadlines))],
		})

		kwCount := 3 + e.rng.IntN(4)
		matchTypes := []string{"Phrase", "Phrase", "Exact", "Broad"}
		for j := 0; j < kwCount; j++ {
			kwID := e.State.NextKeywordID
			e.State.NextKeywordID++
			e.State.KeywordIDs = append(e.State.KeywordIDs, kwID)
			brand := e.State.OnboardedBrands[e.rng.IntN(len(e.State.OnboardedBrands))]
			status := "Eligible"
			if e.rng.Float64() < 0.02 {
				status = "Rarely Served"
			}
			emit(model.KeywordRow{
				KeywordID:           kwID,
				AdID:                agID,
				CreatedAt:           createdDate,
				CriterionName:       strings.ToLower(fmt.Sprintf("%s %s clothing", brand, cat.Category)),
				CPCBidAmount:        round2(10 + e.rng.Float64()*900),
				PeriodID:            nil,
				SystemServingStatus: status,
				BiddingStrategyType: bidType,
				QualityScore:        int64(1 + e.rng.IntN(10)),
				KeywordMatchType:    matchTypes[e.rng.IntN(len(matchTypes))],
			})
		}
	}
}
