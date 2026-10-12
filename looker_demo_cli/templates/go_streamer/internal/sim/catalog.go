package sim

import (
	"encoding/binary"
	"encoding/hex"
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"

	"demo-streamer/internal/model"
)

var (
	brandPrefixes = []string{
		"Northbound", "Vanguard", "Mercer", "Solstice", "Kensington", "Harbor", "Summit", "Crestline",
		"Alder", "Barrow", "Caspian", "Draper", "Evergreen", "Fairmont", "Garrison", "Halcyon",
		"Ironwood", "Juniper", "Kingsley", "Linden", "Mariner", "Noble", "Oakridge", "Pacific",
		"Quarry", "Redwood", "Sterling", "Timberline", "Upland", "Westbrook",
	}
	brandSuffixes = []string{
		"Apparel", "Co.", "Supply", "Denim", "Originals", "Knitwear", "Threads", "Label",
		"Goods", "Outfitter", "Studio", "Collective", "Clothiers", "Works", "Tailors", "Athletics",
	}
	productColors = []string{
		"Black", "Navy", "Charcoal", "Heather Grey", "Olive", "Burgundy", "Cobalt", "Ivory",
		"Indigo", "Khaki", "Rust", "Teal", "Slate", "Forest Green", "Sand", "Crimson",
	}
	productAdjectives = []string{
		"Classic", "Modern", "Ultra-Soft", "Lightweight", "Stretch", "Vintage", "Essential",
		"Performance", "Slim Fit", "Relaxed", "Thermal", "Organic Cotton", "Signature",
		"Athletic", "Everyday", "Deluxe", "Microfiber", "Fleece", "Tailored", "Ribbed",
	}
	productNouns = map[string][]string{
		"Jeans":                         {"Straight Leg Jeans", "Bootcut Denim", "Skinny Jeans", "Relaxed Fit Jeans", "Selvedge Denim"},
		"Underwear":                     {"Boxer Brief", "Cotton Trunk", "Stretch Brief", "Performance Boxer"},
		"Tops & Tees":                   {"Crewneck Tee", "V-Neck T-Shirt", "Henley Shirt", "Graphic Tee", "Pocket Tee"},
		"Pants":                         {"Chino Pant", "Cargo Trouser", "Twill Khaki", "Flat Front Pant"},
		"Sweaters":                      {"Merino Pullover", "Cable Knit Sweater", "Cardigan", "Quarter-Zip Sweater"},
		"Fashion Hoodies & Sweatshirts": {"Fleece Hoodie", "Pullover Sweatshirt", "Zip-Up Hoody", "French Terry Crew"},
		"Sleep & Lounge":                {"Cotton Pajama Set", "Lounge Pant", "Plush Robe", "Sleep Shirt"},
		"Shorts":                        {"Cargo Short", "Chino Short", "Boardshort", "Drawstring Short"},
		"Swim":                          {"Swim Trunk", "Bikini Top", "One-Piece Swimsuit", "Rashguard"},
		"Socks":                         {"Crew Socks 3-Pack", "No-Show Socks", "Merino Hiking Sock", "Ankle Sock"},
		"Outerwear & Coats":             {"Down Puffer Jacket", "Trench Coat", "Wool Peacoat", "Rain Shell", "Parka"},
		"Accessories":                   {"Leather Belt", "Polarized Sunglasses", "Knit Beanie", "Silk Tie", "Cashmere Scarf"},
		"Active":                        {"Training Tank", "Compression Top", "Running Track Jacket", "Cropped Cami"},
		"Suits & Sport Coats":           {"Two-Button Blazer", "Tailored Sport Coat", "Wool Suit Jacket"},
		"Intimates":                     {"Lace Bralette", "Seamless Shaping Cami", "Satin Chemise", "Underwire Bra"},
		"Dresses":                       {"Wrap Midi Dress", "Maxi Sundress", "Cocktail Shift Dress", "A-Line Dress"},
		"Maternity":                     {"Ruched Maternity Tank", "Over-Bump Maternity Jeans", "Nursing Wrap Top"},
		"Plus":                          {"Plus Tunic Top", "Plus Bootcut Jean", "Plus Cardigan"},
		"Socks & Hosiery":               {"Opaque Tights", "Sheer Stockings", "Cozy Boot Socks"},
		"Pants & Capris":                {"Cropped Capri", "Wide-Leg Trouser", "Ankle Chino"},
		"Leggings":                      {"High-Waist Legging", "Seamless Yoga Tight", "Fleece-Lined Legging"},
		"Blazers & Jackets":             {"Structured Blazer", "Moto Jacket", "Cropped Tweed Jacket"},
		"Skirts":                        {"Pleated Midi Skirt", "Denim Mini Skirt", "Pencil Skirt"},
		"Suits":                         {"Tailored Pant Suit", "Two-Piece Skirt Suit"},
		"Jumpsuits & Rompers":           {"Wide-Leg Jumpsuit", "Utility Romper", "Halter Jumpsuit"},
		"Clothing Sets":                 {"Matching Knit Set", "Two-Piece Lounge Set"},
	}
)

func (e *Engine) pickCategory() model.CategoryProfile {
	total := 0
	for _, c := range e.Seed.Categories {
		total += c.Weight
	}
	v := e.rng.IntN(total)
	for _, c := range e.Seed.Categories {
		v -= c.Weight
		if v < 0 {
			return c
		}
	}
	return e.Seed.Categories[0]
}

func (e *Engine) pickBrowsedProduct(ts time.Time) model.ProductMeta {
	if len(e.State.Products) == 0 {
		return model.ProductMeta{}
	}
	for attempts := 0; attempts < 4; attempts++ {
		p := e.State.Products[e.rng.IntN(len(e.State.Products))]
		w := CategorySeasonWeight(p.Category, ts.Month())
		if w >= 1.0 || e.rng.Float64() < w {
			return p
		}
	}
	return e.State.Products[e.rng.IntN(len(e.State.Products))]
}

func (e *Engine) createProduct(brand string, ts time.Time, emit func(any)) model.ProductMeta {
	cat := e.pickCategory()
	id := e.State.NextProductID
	e.State.NextProductID++

	price := cat.AvgPrice + e.rng.NormFloat64()*cat.StdPrice*0.5
	if price < cat.MinPrice {
		price = cat.MinPrice
	}
	if price > cat.MaxPrice {
		price = cat.MaxPrice
	}
	price = round2(price)
	costRatio := cat.CostRatio + e.rng.NormFloat64()*0.028
	if costRatio < 0.25 {
		costRatio = 0.25
	}
	if costRatio > 0.75 {
		costRatio = 0.75
	}
	cost := math.Round(price*costRatio*10000) / 10000

	adj := productAdjectives[e.rng.IntN(len(productAdjectives))]
	color := productColors[e.rng.IntN(len(productColors))]
	nouns := productNouns[cat.Category]
	noun := cat.Category
	if len(nouns) > 0 {
		noun = nouns[e.rng.IntN(len(nouns))]
	}
	name := fmt.Sprintf("%s %s %s %s %s", brand, cat.Department, color, adj, noun)

	var skuBytes [16]byte
	binary.LittleEndian.PutUint64(skuBytes[0:8], e.rng.Uint64())
	binary.LittleEndian.PutUint64(skuBytes[8:16], e.rng.Uint64())
	sku := strings.ToUpper(hex.EncodeToString(skuBytes[:]))
	dcID := int64(1 + e.rng.IntN(len(e.Seed.DistributionCenters)))

	p := model.ProductMeta{
		ID:          id,
		Cost:        cost,
		Category:    cat.Category,
		Name:        name,
		Brand:       brand,
		RetailPrice: price,
		Department:  cat.Department,
		SKU:         sku,
		DCID:        dcID,
	}
	e.State.Products = append(e.State.Products, p)

	emit(model.ProductRow{
		ID:                   p.ID,
		Cost:                 p.Cost,
		Category:             p.Category,
		Name:                 p.Name,
		Brand:                p.Brand,
		RetailPrice:          p.RetailPrice,
		Department:           p.Department,
		SKU:                  p.SKU,
		DistributionCenterID: strconv.FormatInt(p.DCID, 10),
	})

	stockCnt := 1 + e.rng.IntN(2)
	for i := 0; i < stockCnt; i++ {
		e.stockInventoryItem(p, ts, emit)
	}
	return p
}

func (e *Engine) stockInventoryItem(p model.ProductMeta, ts time.Time, emit func(any)) int64 {
	invID := e.State.NextInventoryID
	e.State.NextInventoryID++
	createdDate := model.FmtDate(ts)
	e.State.AvailableInventory[p.ID] = append(e.State.AvailableInventory[p.ID], invID)
	e.State.InventoryCreated[invID] = createdDate

	emit(model.InventoryItemRow{
		ID:                          invID,
		ProductID:                   p.ID,
		CreatedAt:                   createdDate,
		SoldAt:                      nil,
		Cost:                        p.Cost,
		ProductCategory:             p.Category,
		ProductName:                 p.Name,
		ProductBrand:                p.Brand,
		ProductRetailPrice:          p.RetailPrice,
		ProductDepartment:           p.Department,
		ProductSKU:                  p.SKU,
		ProductDistributionCenterID: p.DCID,
	})
	return invID
}

// OnboardNewBrand pulls from the seed brands first, and once exhausted, synthesizes new apparel brand names.
// Always onboards 2-3 new products + initial inventory with every brand.
func (e *Engine) OnboardNewBrand(ts time.Time, emit func(any)) (string, []model.ProductMeta) {
	var brand string
	idx := len(e.State.OnboardedBrands)
	if idx < len(e.Seed.Brands) {
		brand = e.Seed.Brands[idx]
	} else {
		extIdx := idx - len(e.Seed.Brands)
		pref := brandPrefixes[extIdx%len(brandPrefixes)]
		suff := brandSuffixes[(extIdx/len(brandPrefixes))%len(brandSuffixes)]
		cycle := extIdx / (len(brandPrefixes) * len(brandSuffixes))
		if cycle == 0 {
			brand = fmt.Sprintf("%s %s", pref, suff)
		} else {
			brand = fmt.Sprintf("%s %s %d", pref, suff, cycle+1)
		}
	}
	e.State.OnboardedBrands = append(e.State.OnboardedBrands, brand)
	itemCount := 2 + e.rng.IntN(2)
	created := make([]model.ProductMeta, 0, itemCount)
	for i := 0; i < itemCount; i++ {
		created = append(created, e.createProduct(brand, ts, emit))
	}
	return brand, created
}
