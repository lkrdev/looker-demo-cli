package server

import (
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"time"

	"demo-streamer/internal/model"
)

const indexHTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>demo-streamer status</title>
<style>
  body { font-family: system-ui, -apple-system, sans-serif; margin: 2rem auto; max-width: 1040px; padding: 0 1rem; color: #111827; background: #f9fafb; }
  h1, h2 { color: #111827; margin-top: 1.75rem; }
  .meta { color: #4b5563; font-size: 0.9rem; margin-bottom: 1.5rem; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 0.75rem; margin-bottom: 1.5rem; }
  .card { background: #fff; border: 1px solid #e5e7eb; border-radius: 6px; padding: 0.85rem 1rem; }
  .card .label { font-size: 0.75rem; color: #6b7280; text-transform: uppercase; letter-spacing: 0.04em; }
  .card .val { font-size: 1.35rem; font-weight: 600; margin-top: 0.25rem; }
  table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #e5e7eb; border-radius: 6px; overflow: hidden; margin-bottom: 1.5rem; }
  th, td { text-align: left; padding: 0.6rem 0.85rem; border-bottom: 1px solid #e5e7eb; font-size: 0.875rem; }
  th { background: #f3f4f6; font-weight: 600; color: #374151; }
  code { background: #f3f4f6; padding: 0.15rem 0.35rem; border-radius: 4px; font-size: 0.82rem; }
  .ok { color: #065f46; background: #d1fae5; padding: 0.5rem 0.75rem; border-radius: 6px; display: inline-block; font-size: 0.9rem; }
  .err { color: #991b1b; background: #fee2e2; padding: 0.6rem 0.85rem; border-radius: 6px; margin-bottom: 1rem; font-size: 0.9rem; }
  form { display: flex; gap: 0.5rem; max-width: 380px; margin-top: 2rem; }
  input { flex: 1; padding: 0.5rem 0.75rem; border: 1px solid #d1d5db; border-radius: 6px; font-size: 0.95rem; }
  button { padding: 0.5rem 1rem; background: #111827; color: #fff; border: none; border-radius: 6px; font-size: 0.95rem; cursor: pointer; }
</style>
</head>
<body>
<div id="app"></div>
<script>
const app = document.getElementById('app');
const params = new URLSearchParams(window.location.search);
const key = params.get('key') || '';

function esc(s) {
  const d = document.createElement('div');
  d.textContent = String(s ?? '');
  return d.innerHTML;
}

function renderKeyForm(errMsg) {
  app.innerHTML = (errMsg ? '<div class="err">' + esc(errMsg) + '</div>' : '') +
    '<form id="key-form">' +
      '<input id="key-input" type="password" placeholder="Key" required autofocus>' +
      '<button type="submit">Submit</button>' +
    '</form>';
  document.getElementById('key-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const u = new URL(window.location.href);
    u.searchParams.set('key', document.getElementById('key-input').value);
    window.location.href = u.toString();
  });
}

async function loadStatus() {
  if (!key) {
    renderKeyForm('');
    return;
  }
  const res = await fetch('/api/status?key=' + encodeURIComponent(key));
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    renderKeyForm(data.error || ('HTTP ' + res.status));
    return;
  }
  let gapsHtml = '';
  if (!data.intervals || data.intervals.length === 0) {
    gapsHtml = '<p>No intervals recorded yet. Run initial backfill: <code>streamer backfill --days 3650 --state ' + esc(data.state_path) + '</code></p>';
  } else if (!data.gaps || data.gaps.length === 0) {
    gapsHtml = '<p class="ok">No gaps detected across ' + data.intervals.length + ' covered interval(s).</p>';
  } else {
    gapsHtml = '<table><thead><tr><th>#</th><th>From (UTC)</th><th>To (UTC)</th><th>Duration</th><th>Backfill command</th></tr></thead><tbody>' +
      data.gaps.map((g, i) => '<tr><td>' + (i + 1) + '</td><td><code>' + esc(g.from) + '</code></td><td><code>' + esc(g.to) + '</code></td><td>' + g.minutes + ' min</td><td><code>' + esc(g.command) + '</code></td></tr>').join('') +
      '</tbody></table>';
  }
  const ivHtml = (!data.intervals || data.intervals.length === 0) ? '<p>None</p>' :
    '<table><thead><tr><th>#</th><th>Start (UTC)</th><th>End (UTC)</th><th>Minutes</th></tr></thead><tbody>' +
    data.intervals.map((iv, i) => '<tr><td>' + (i + 1) + '</td><td><code>' + esc(iv.start) + '</code></td><td><code>' + esc(iv.end) + '</code></td><td>' + iv.minutes + '</td></tr>').join('') +
    '</tbody></table>';
  const cmdHtml = (!data.commands || data.commands.length === 0) ? '<p>No commands logged yet.</p>' :
    '<table><thead><tr><th>Timestamp (UTC)</th><th>PID</th><th>Command</th><th>Arguments</th></tr></thead><tbody>' +
    data.commands.map(c => '<tr><td><code>' + esc(c.timestamp) + '</code></td><td>' + c.pid + '</td><td><code>' + esc(c.command) + '</code></td><td><code>' + esc((c.args || []).join(' ')) + '</code></td></tr>').join('') +
    '</tbody></table>';

  app.innerHTML =
    '<h1>demo-streamer status</h1>' +
    '<div class="meta">Updated at <code>' + esc(data.generated_at) + '</code> | State file: <code>' + esc(data.state_path) + '</code></div>' +
    '<div class="cards">' +
      '<div class="card"><div class="label">Brands</div><div class="val">' + data.brands + '</div></div>' +
      '<div class="card"><div class="label">Products</div><div class="val">' + data.products + '</div></div>' +
      '<div class="card"><div class="label">Users</div><div class="val">' + data.users + '</div></div>' +
      '<div class="card"><div class="label">Orders</div><div class="val">' + data.orders + '</div></div>' +
      '<div class="card"><div class="label">Web Events</div><div class="val">' + data.events + '</div></div>' +
      '<div class="card"><div class="label">Lookback Views/Carts</div><div class="val">' + data.viewed_items + ' / ' + data.carted_items + '</div></div>' +
      '<div class="card"><div class="label">Pending Orders</div><div class="val">' + data.pending_orders + '</div></div>' +
    '</div>' +
    '<h2>Timeframe gaps</h2>' + gapsHtml +
    '<h2>Covered intervals</h2>' + ivHtml +
    '<h2>Command execution history</h2>' + cmdHtml;
}

loadStatus();
if (key) setInterval(loadStatus, 10000);
</script>
</body>
</html>`

func NewStatusMux(statePath string) *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = io.WriteString(w, indexHTML)
	})
	mux.HandleFunc("/api/status", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		key := r.URL.Query().Get("key")
		secret := os.Getenv("SECRET")
		if key == "" || secret == "" || subtle.ConstantTimeCompare([]byte(key), []byte(secret)) != 1 {
			w.WriteHeader(http.StatusUnauthorized)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "missing or invalid key query parameter"})
			return
		}
		st, err := model.LoadState(statePath)
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		now := time.Now().UTC().Truncate(time.Minute)
		rawGaps := FindGaps(st, now)
		type gapJSON struct {
			From    string `json:"from"`
			To      string `json:"to"`
			Minutes int    `json:"minutes"`
			Command string `json:"command"`
		}
		gaps := make([]gapJSON, len(rawGaps))
		for i, g := range rawGaps {
			gaps[i] = gapJSON{
				From:    model.FmtTS(g.From),
				To:      model.FmtTS(g.To),
				Minutes: int(g.To.Sub(g.From).Minutes()) + 1,
				Command: fmt.Sprintf("streamer backfill --from %s --to %s --state %s", model.FmtTS(g.From), model.FmtTS(g.To), statePath),
			}
		}
		type ivJSON struct {
			Start   string `json:"start"`
			End     string `json:"end"`
			Minutes int64  `json:"minutes"`
		}
		ivs := make([]ivJSON, len(st.Intervals))
		for i, iv := range st.Intervals {
			ivs[i] = ivJSON{
				Start:   model.FmtTS(time.Unix(iv.StartMin*60, 0).UTC()),
				End:     model.FmtTS(time.Unix(iv.EndMin*60, 0).UTC()),
				Minutes: iv.EndMin - iv.StartMin + 1,
			}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"generated_at":   model.FmtTS(now),
			"state_path":     statePath,
			"brands":         len(st.OnboardedBrands),
			"products":       len(st.Products),
			"users":          len(st.Users),
			"orders":         st.NextOrderID - 1,
			"events":         st.NextEventID - 1,
			"viewed_items":   len(st.ViewedItems),
			"carted_items":   len(st.CartedItems),
			"pending_orders": len(st.PendingOrders),
			"gaps":           gaps,
			"intervals":      ivs,
			"commands":       ReadCommandLog(statePath),
		})
	})
	return mux
}

func StartBackgroundServer(addr, statePath string) {
	if addr != "" {
		go func() { _ = http.ListenAndServe(addr, NewStatusMux(statePath)) }()
	}
}
