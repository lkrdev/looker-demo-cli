package sim

import (
	"encoding/binary"
	"fmt"
	"math"
	"math/rand/v2"
	"strings"
	"time"

	"demo-streamer/internal/model"

	"github.com/go-faker/faker/v4"
)

var (
	emailDomains     = []string{"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com", "aol.com"}
	userTrafficSrcs  = []string{"Search", "Search", "Search", "Search", "Search", "Search", "Search", "Organic", "Organic", "Facebook", "Display", "Email"}
	eventTrafficSrcs = []string{"Adwords", "Adwords", "Adwords", "Adwords", "Organic", "Organic", "Organic", "Organic", "Facebook", "Email", "YouTube"}
	osBrowsers       = [][2]string{
		{"Macintosh", "Safari"}, {"Macintosh", "Safari"}, {"Macintosh", "Safari"},
		{"Windows", "Chrome"}, {"Windows", "Chrome"}, {"Windows", "Chrome"},
		{"Macintosh", "Chrome"}, {"Macintosh", "Chrome"},
		{"Windows", "IE"}, {"Linux", "Chrome"}, {"Windows", "Firefox"},
		{"Macintosh", "Firefox"}, {"Linux", "Firefox"}, {"Windows", "Safari"},
		{"Macintosh", "Other"}, {"Windows", "Other"}, {"Linux", "Other"},
	}
)

func (e *Engine) newUUID() string {
	var b [16]byte
	binary.LittleEndian.PutUint64(b[0:8], e.rng.Uint64())
	binary.LittleEndian.PutUint64(b[8:16], e.rng.Uint64())
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

func round2(v float64) float64 {
	return math.Round(v*100) / 100
}

func cleanAlpha(s string) string {
	var b strings.Builder
	for _, r := range strings.ToLower(s) {
		if r >= 'a' && r <= 'z' {
			b.WriteRune(r)
		}
	}
	if b.Len() == 0 {
		return "user"
	}
	return b.String()
}

func (e *Engine) skewedEmailNum() int {
	if e.rng.Float64() < 0.80 {
		return 1 + e.rng.IntN(999)
	}
	return 1000 + e.rng.IntN(9000)
}

func (e *Engine) generateEmail(first, last string) string {
	f, l := cleanAlpha(first), cleanAlpha(last)
	fi, li := f[:1], l[:1]
	domain := emailDomains[e.rng.IntN(len(emailDomains))]
	var local string
	switch e.rng.IntN(10) {
	case 0:
		local = fmt.Sprintf("%s%s%d", fi, l, e.skewedEmailNum())
	case 1:
		local = fmt.Sprintf("%s%s%d", f, li, e.skewedEmailNum())
	case 2:
		local = fmt.Sprintf("%s.%s%d", fi, l, e.skewedEmailNum())
	case 3:
		local = fmt.Sprintf("%s.%s%d", f, li, e.skewedEmailNum())
	case 4:
		local = fmt.Sprintf("%s%s%d", l, fi, e.skewedEmailNum())
	case 5:
		local = fmt.Sprintf("%s%s%d", li, f, e.skewedEmailNum())
	case 6:
		local = f + l
	case 7:
		local = f + "." + l
	case 8:
		local = f + "_" + l
	default:
		local = fmt.Sprintf("%s%s%d", f, l, e.skewedEmailNum())
	}
	return local + "@" + domain
}

func (e *Engine) signupUser(ts time.Time, loc model.LocationProfile, emit func(any)) model.UserRow {
	id := e.State.NextUserID
	e.State.NextUserID++
	gender := "Female"
	var first string
	if e.rng.Float64() < 0.447 {
		gender = "Male"
		first = faker.FirstNameMale()
	} else {
		first = faker.FirstNameFemale()
	}
	last := faker.LastName()

	u := model.UserRow{
		ID:            id,
		FirstName:     first,
		LastName:      last,
		Email:         e.generateEmail(first, last),
		Age:           int64(12 + e.rng.IntN(59)),
		City:          loc.City,
		State:         loc.State,
		Country:       loc.Country,
		Zip:           loc.Zip,
		Latitude:      loc.Lat,
		Longitude:     loc.Lon,
		Gender:        gender,
		CreatedAt:     model.FmtTS(ts),
		TrafficSource: userTrafficSrcs[e.rng.IntN(len(userTrafficSrcs))],
	}
	e.State.Users = append(e.State.Users, u)
	if e.rng.Float64() < 0.05 {
		e.State.PowerUserIDs = append(e.State.PowerUserIDs, u.ID)
	} else if e.rng.Float64() < 0.0105 {
		e.State.HeavyBrowserIDs = append(e.State.HeavyBrowserIDs, u.ID)
	}
	emit(u)
	return u
}

func (e *Engine) lookupUser(id int64) model.UserRow {
	idx := id - 1
	if idx >= 0 && idx < int64(len(e.State.Users)) && e.State.Users[idx].ID == id {
		return e.State.Users[idx]
	}
	return e.State.Users[e.rng.IntN(len(e.State.Users))]
}

func (e *Engine) runBrowseSession(ts time.Time, emit func(any)) {
	sessionID := e.newUUID()
	ob := osBrowsers[e.rng.IntN(len(osBrowsers))]
	tsrc := eventTrafficSrcs[e.rng.IntN(len(eventTrafficSrcs))]
	var adEventID *int64
	if len(e.State.RecentAdClicks) > 0 && e.rng.Float64() < 0.35 {
		id := e.State.RecentAdClicks[e.rng.IntN(len(e.State.RecentAdClicks))]
		adEventID = &id
	}

	secOffset := e.rng.IntN(30)
	evTime := ts.Add(time.Duration(secOffset) * time.Second)
	loc := e.Seed.Locations[e.rng.IntN(len(e.Seed.Locations))]
	anonUser := model.UserRow{City: loc.City, State: loc.State, Country: loc.Country, Zip: loc.Zip, Latitude: loc.Lat, Longitude: loc.Lon}

	if len(e.State.HeavyBrowserIDs) == 0 && len(e.State.Users) >= 100 {
		for _, existU := range e.State.Users {
			if existU.ID > 0 && existU.ID%100 == 7 {
				e.State.HeavyBrowserIDs = append(e.State.HeavyBrowserIDs, existU.ID)
			}
		}
	}
	if len(e.State.PowerUserIDs) == 0 && len(e.State.Users) >= 20 {
		for _, existU := range e.State.Users {
			if existU.ID > 0 && existU.ID%20 == 0 {
				e.State.PowerUserIDs = append(e.State.PowerUserIDs, existU.ID)
			}
		}
	}

	roll := e.rng.Float64()
	var (
		u              model.UserRow
		uid            *int64
		activeUser     model.UserRow
		isHeavyBrowser bool
		isPowerUser    bool
		medianLen      = 5.0
	)

	seq := int64(1)
	p := e.pickBrowsedProduct(ts)

	if len(e.State.Users) > 0 && roll < 0.35 {
		activeUser = anonUser
		medianLen = 4.0
		e.emitWebEvent(evTime, sessionID, seq, nil, adEventID, anonUser, ob[0], ob[1], tsrc, "/home", "Home", emit)
	} else if roll < 0.55 || len(e.State.Users) == 0 {
		e.emitWebEvent(evTime, sessionID, seq, nil, adEventID, anonUser, ob[0], ob[1], tsrc,
			fmt.Sprintf("/department/%s/category/%s/brand/%s", p.Department, p.Category, p.Brand), "Brand", emit)
		seq++
		evTime = evTime.Add(time.Duration(2+e.rng.IntN(6)) * time.Second)
		u = e.signupUser(evTime, loc, emit)
		userID := u.ID
		uid = &userID
		activeUser = u
		e.emitWebEvent(evTime, sessionID, seq, uid, adEventID, u, ob[0], ob[1], tsrc, "/register", "Register", emit)
	} else {
		isHeavyBrowser = len(e.State.HeavyBrowserIDs) > 0 && e.rng.Float64() < 0.06
		if isHeavyBrowser {
			powerID := e.State.HeavyBrowserIDs[e.rng.IntN(len(e.State.HeavyBrowserIDs))]
			u = e.lookupUser(powerID)
			medianLen = 12.0
		} else if len(e.State.PowerUserIDs) > 0 && e.rng.Float64() < 0.16 {
			powerID := e.State.PowerUserIDs[e.rng.IntN(len(e.State.PowerUserIDs))]
			u = e.lookupUser(powerID)
			isPowerUser = true
			medianLen = 5.0
		} else {
			u = e.State.Users[e.rng.IntN(len(e.State.Users))]
			userFactor := 0.6 + 0.8*float64((u.ID*2654435761)%100)/100.0
			medianLen = 5.0 * userFactor
		}
		userID := u.ID
		uid = &userID
		activeUser = u

		if e.rng.Float64() < 0.5 {
			e.emitWebEvent(evTime, sessionID, seq, uid, adEventID, u, ob[0], ob[1], tsrc,
				fmt.Sprintf("/department/%s/category/%s", p.Department, p.Category), "Category", emit)
		} else {
			e.emitWebEvent(evTime, sessionID, seq, uid, adEventID, u, ob[0], ob[1], tsrc,
				fmt.Sprintf("/department/%s/category/%s/brand/%s", p.Department, p.Category, p.Brand), "Brand", emit)
		}
	}

	// Always view at least one product in every session before possible cart/purchase
	seq++
	evTime = evTime.Add(time.Duration(2+e.rng.IntN(8)) * time.Second)
	e.emitWebEvent(evTime, sessionID, seq, uid, adEventID, activeUser, ob[0], ob[1], tsrc,
		fmt.Sprintf("/product/%d", p.ID), "Product", emit)

	sessionEvents := max(int(seq), int(math.Round(-medianLen*math.Log(max(1e-9, e.rng.Float64()))/0.69314718056)))

	for seq < int64(sessionEvents) {
		seq++
		evTime = evTime.Add(time.Duration(2+e.rng.IntN(8)) * time.Second)
		p = e.pickBrowsedProduct(ts)
		if seq%2 == 0 {
			e.emitWebEvent(evTime, sessionID, seq, uid, adEventID, activeUser, ob[0], ob[1], tsrc,
				fmt.Sprintf("/product/%d", p.ID), "Product", emit)
		} else {
			e.emitWebEvent(evTime, sessionID, seq, uid, adEventID, activeUser, ob[0], ob[1], tsrc,
				fmt.Sprintf("/department/%s/category/%s", p.Department, p.Category), "Category", emit)
		}
	}

	if uid != nil {
		trafficMult, _, _ := CalendarSeason(ts)
		cartChance := 0.55
		if trafficMult > 1.5 {
			cartChance = 0.70
		}
		if isHeavyBrowser {
			cartChance = 0.03
		} else if isPowerUser {
			cartChance = 0.75
		}

		if e.rng.Float64() < cartChance {
			var due int64
			if e.rng.Float64() < 0.65 {
				due = min(ts.Unix()+59, evTime.Unix()+int64(3+e.rng.IntN(12)))
			} else {
				due = evTime.Unix() + int64(60+e.rng.IntN(43200))
			}
			e.State.ViewedItems = append(e.State.ViewedItems, model.FunnelItem{
				UserID:        *uid,
				ProductID:     p.ID,
				SessionID:     sessionID,
				SeqNum:        seq,
				AdEventID:     adEventID,
				OS:            ob[0],
				Browser:       ob[1],
				TrafficSource: tsrc,
				DueUnix:       due,
			})
		}
	}
}

func (e *Engine) completePurchase(buyTime time.Time, ci model.FunnelItem, emit func(any)) {
	p := e.State.Products[ci.ProductID-1]
	u := e.State.Users[ci.UserID-1]

	avail := e.State.AvailableInventory[p.ID]
	if len(avail) == 0 {
		for i := 0; i < 2; i++ {
			avail = append(avail, e.stockInventoryItem(p, buyTime, emit))
		}
	}
	invID := avail[0]
	e.State.AvailableInventory[p.ID] = avail[1:]
	if e.rng.Float64() < 0.85 {
		e.stockInventoryItem(p, buyTime, emit)
	}

	soldAtStr := model.FmtTS(buyTime)
	createdDate := e.State.InventoryCreated[invID]
	if createdDate == "" {
		createdDate = model.FmtDate(buyTime)
	}
	emit(model.InventoryItemRow{
		ID:                          invID,
		ProductID:                   p.ID,
		CreatedAt:                   createdDate,
		SoldAt:                      &soldAtStr,
		Cost:                        p.Cost,
		ProductCategory:             p.Category,
		ProductName:                 p.Name,
		ProductBrand:                p.Brand,
		ProductRetailPrice:          p.RetailPrice,
		ProductDepartment:           p.Department,
		ProductSKU:                  p.SKU,
		ProductDistributionCenterID: p.DCID,
	})

	salePrice := p.RetailPrice
	_, discChance, isPromo := CalendarSeason(buyTime)
	if e.rng.Float64() < discChance {
		var pcts []float64
		if isPromo {
			pcts = []float64{0.15, 0.20, 0.25, 0.30, 0.40, 0.50}
		} else {
			pcts = []float64{0.05, 0.10, 0.15, 0.20, 0.25, 0.50}
		}
		discAmt := pcts[e.rng.IntN(len(pcts))]
		salePrice = round2(p.RetailPrice * (1.0 - discAmt))
		emit(model.DiscountRow{
			ProductID:       p.ID,
			InventoryItemID: invID,
			RetailPrice:     p.RetailPrice,
			DiscountPrice:   salePrice,
			DiscountAmount:  discAmt,
			Date:            soldAtStr,
		})
	}

	uid := ci.UserID
	seq := ci.SeqNum
	if seq == 0 {
		seq = 1
		e.emitWebEvent(buyTime.Add(-2*time.Second), ci.SessionID, seq, &uid, ci.AdEventID, u, ci.OS, ci.Browser, ci.TrafficSource, "/cart", "Cart", emit)
	}
	seq++
	e.emitWebEvent(buyTime, ci.SessionID, seq, &uid, ci.AdEventID, u, ci.OS, ci.Browser, ci.TrafficSource, "/purchase", "Purchase", emit)

	orderID := e.State.NextOrderID
	e.State.NextOrderID++
	oiID := e.State.NextOrderItemID
	e.State.NextOrderItemID++

	po := model.PendingOrder{
		OrderItemID:     oiID,
		OrderID:         orderID,
		UserID:          ci.UserID,
		InventoryItemID: invID,
		SalePrice:       salePrice,
		Status:          "Processing",
		CreatedAt:       soldAtStr,
		IsPrimaryItem:   true,
	}
	e.emitOrderAndTx(po, emit)
	emit(model.CombinedOrdersUsersRow{
		UserID: ci.UserID,
	})

	if e.rng.Float64() < 0.035 {
		po.NextStatus = "Cancelled"
		po.NextDueUnix = buyTime.Unix() + int64(300+e.rng.IntN(14400))
	} else {
		po.NextStatus = "Shipped"
		shipDays := 1 + e.rng.IntN(3)
		if IsPeakLogistics(buyTime) {
			shipDays = 3 + e.rng.IntN(5)
		}
		po.NextDueUnix = buyTime.Unix() + int64(86400*shipDays)
	}
	e.State.PendingOrders = append(e.State.PendingOrders, po)
}

func (e *Engine) emitOrderAndTx(po model.PendingOrder, emit func(any)) {
	ct := po.ChangeType
	if ct == "" && strings.EqualFold(e.State.CrudMode, "full_crud") && po.Status == "Cancelled" {
		ct = "DELETE"
	}
	emit(model.OrderItemRow{
		ID:              po.OrderItemID,
		OrderID:         po.OrderID,
		UserID:          po.UserID,
		InventoryItemID: po.InventoryItemID,
		SalePrice:       po.SalePrice,
		Status:          po.Status,
		CreatedAt:       po.CreatedAt,
		ReturnedAt:      po.ReturnedAt,
		ShippedAt:       po.ShippedAt,
		DeliveredAt:     po.DeliveredAt,
		ChangeType:      ct,
	})

	items := po.OrderItems
	if len(items) == 0 {
		items = []model.TxItem{{
			InventoryItemID: po.InventoryItemID,
			ReturnedAt:      po.ReturnedAt,
			SalePrice:       po.SalePrice,
		}}
	} else if !po.IsPrimaryItem {
		return
	}

	u := e.lookupUser(po.UserID)
	emit(model.TransactionDetailRow{
		OrderID:     po.OrderID,
		Status:      po.Status,
		CreatedAt:   po.CreatedAt,
		ShippedAt:   po.ShippedAt,
		DeliveredAt: po.DeliveredAt,
		Items:       items,
		User: model.TxUser{
			UserID:        u.ID,
			Name:          strings.ToUpper(u.FirstName + " " + u.LastName),
			Email:         u.Email,
			Age:           u.Age,
			City:          u.City,
			State:         u.State,
			Country:       u.Country,
			Zip:           u.Zip,
			Location:      fmt.Sprintf("POINT(%g %g)", u.Longitude, u.Latitude),
			Gender:        u.Gender,
			UserCreatedAt: u.CreatedAt,
			TrafficSource: u.TrafficSource,
		},
		ChangeType: ct,
	})
}

func (e *Engine) emitWebEvent(ts time.Time, sessionID string, seq int64, userID *int64, adEventID *int64, u model.UserRow, osName, browser, tsrc, uri, evType string, emit func(any)) {
	evID := e.State.NextEventID
	e.State.NextEventID++
	r := rand.New(rand.NewPCG(uint64(u.ID), uint64(seq)))
	ip := fmt.Sprintf("%d.%d.%d.%d", 11+r.IntN(200), r.IntN(255), r.IntN(255), 1+r.IntN(254))
	ref := ""
	if adEventID != nil {
		ref = fmt.Sprintf("ref_%d", *adEventID%1000)
	}
	emit(model.EventRow{
		ID:             evID,
		SequenceNumber: seq,
		SessionID:      sessionID,
		CreatedAt:      model.FmtTS(ts),
		IPAddress:      ip,
		City:           u.City,
		State:          u.State,
		Country:        u.Country,
		Zip:            u.Zip,
		Latitude:       u.Latitude,
		Longitude:      u.Longitude,
		OS:             osName,
		Browser:        browser,
		TrafficSource:  tsrc,
		UserID:         userID,
		URI:            uri,
		EventType:      evType,
		AdEventID:      adEventID,
		ReferrerCode:   ref,
	})
}

// pickFraudProducts selects count high-value products sampled without replacement
// from the catalog, shuffling candidates so successive fraud orders do not repeat identical items.
func (e *Engine) pickFraudProducts(count int) []model.ProductMeta {
	for _, minPrice := range []float64{120.0, 60.0, 0.0} {
		var pool []model.ProductMeta
		for _, p := range e.State.Products {
			if p.RetailPrice >= minPrice {
				pool = append(pool, p)
			}
		}
		if len(pool) >= count || minPrice == 0.0 {
			e.rng.Shuffle(len(pool), func(i, j int) { pool[i], pool[j] = pool[j], pool[i] })
			return pool[:min(count, len(pool))]
		}
	}
	return nil
}

// EmitFraudAnomaly executes a high-velocity multi-item fraudulent purchasing anomaly.
func (e *Engine) EmitFraudAnomaly(ts time.Time, emit func(any)) {
	if len(e.State.Products) == 0 {
		return
	}

	count := 6 + e.rng.IntN(7) // 6 to 12 items
	prods := e.pickFraudProducts(count)
	if len(prods) == 0 {
		return
	}

	secOffset := e.rng.IntN(20)
	evTime := ts.Add(time.Duration(secOffset) * time.Second)
	loc := e.Seed.Locations[e.rng.IntN(len(e.Seed.Locations))]
	u := e.signupUser(evTime, loc, emit)
	sessionID := e.newUUID()
	ob := osBrowsers[e.rng.IntN(len(osBrowsers))]
	tsrc := "Direct"
	if e.rng.Float64() < 0.30 {
		tsrc = "Search"
	}
	userID := u.ID
	uid := &userID

	var seq int64 = 1
	e.emitWebEvent(evTime, sessionID, seq, uid, nil, u, ob[0], ob[1], tsrc, "/register", "Register", emit)

	for _, p := range prods {
		evTime = evTime.Add(time.Duration(1+e.rng.IntN(3)) * time.Second)
		seq++
		e.emitWebEvent(evTime, sessionID, seq, uid, nil, u, ob[0], ob[1], tsrc, fmt.Sprintf("/product/%d", p.ID), "Product", emit)

		evTime = evTime.Add(time.Duration(1+e.rng.IntN(2)) * time.Second)
		seq++
		e.emitWebEvent(evTime, sessionID, seq, uid, nil, u, ob[0], ob[1], tsrc, "/cart", "Cart", emit)
	}

	buyTime := evTime.Add(time.Duration(2+e.rng.IntN(4)) * time.Second)
	seq++
	e.emitWebEvent(buyTime, sessionID, seq, uid, nil, u, ob[0], ob[1], tsrc, "/purchase", "Purchase", emit)

	orderID := e.State.NextOrderID
	e.State.NextOrderID++
	soldAtStr := model.FmtTS(buyTime)

	var txItems []model.TxItem
	var orderPendingItems []model.PendingOrder

	for _, p := range prods {
		avail := e.State.AvailableInventory[p.ID]
		if len(avail) == 0 {
			for i := 0; i < 2; i++ {
				avail = append(avail, e.stockInventoryItem(p, buyTime, emit))
			}
		}
		invID := avail[0]
		e.State.AvailableInventory[p.ID] = avail[1:]

		createdDate := e.State.InventoryCreated[invID]
		if createdDate == "" {
			createdDate = model.FmtDate(buyTime)
		}

		emit(model.InventoryItemRow{
			ID:                          invID,
			ProductID:                   p.ID,
			CreatedAt:                   createdDate,
			SoldAt:                      &soldAtStr,
			Cost:                        p.Cost,
			ProductCategory:             p.Category,
			ProductName:                 p.Name,
			ProductBrand:                p.Brand,
			ProductRetailPrice:          p.RetailPrice,
			ProductDepartment:           p.Department,
			ProductSKU:                  p.SKU,
			ProductDistributionCenterID: p.DCID,
		})

		oiID := e.State.NextOrderItemID
		e.State.NextOrderItemID++

		txItems = append(txItems, model.TxItem{
			InventoryItemID: invID,
			SalePrice:       p.RetailPrice,
		})

		orderPendingItems = append(orderPendingItems, model.PendingOrder{
			OrderItemID:     oiID,
			OrderID:         orderID,
			UserID:          u.ID,
			InventoryItemID: invID,
			SalePrice:       p.RetailPrice,
			Status:          "Processing",
			CreatedAt:       soldAtStr,
		})
	}

	isCancelled := e.rng.Float64() < 0.35
	var nextStatus string
	var nextDue int64
	if isCancelled {
		nextStatus = "Cancelled"
		nextDue = buyTime.Unix() + int64(600+e.rng.IntN(3000))
	} else {
		nextStatus = "Shipped"
		shipDays := 1 + e.rng.IntN(3)
		if IsPeakLogistics(buyTime) {
			shipDays = 3 + e.rng.IntN(5)
		}
		nextDue = buyTime.Unix() + int64(86400*shipDays)
	}

	for i := range orderPendingItems {
		orderPendingItems[i].NextStatus = nextStatus
		orderPendingItems[i].NextDueUnix = nextDue
		orderPendingItems[i].OrderItems = txItems
		orderPendingItems[i].IsPrimaryItem = i == 0
		e.emitOrderAndTx(orderPendingItems[i], emit)
	}
	emit(model.CombinedOrdersUsersRow{UserID: u.ID})
	e.State.PendingOrders = append(e.State.PendingOrders, orderPendingItems...)
}
