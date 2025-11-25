# Multi-Strategy Trading Dashboard - Planning Document

## Overview

A mobile-friendly dashboard for monitoring the multi-strategy trading system (`portfolio.py`). Simple Python backend with authentication, showing real-time status, historical performance, and alerts.

---

## Current State Analysis

### Data Sources Available (Already Built)

| Data Category | Source Object | Real-Time | Historical |
|---------------|---------------|-----------|------------|
| Positions & P&L | `VirtualPositionTracker` | Yes | Trade history |
| Strategy Signals | `StrategyState.last_signal` | Yes | Via snapshots |
| Performance Metrics | `StrategyAttribution` | Yes | Full history |
| Equity Curve | `StrategyAttribution.equity_curve` | Yes | 200K points max |
| Bracket Orders | `BracketOrderManager` | Yes | N/A |
| Errors/Warnings | `lumibot_logger` | Yes | CSV export |
| Cache/Rate Stats | Iteration summary | Yes | N/A |

### Key Files
- `custom_portfolio/multi_strategy_executor_enhanced.py` - Main data source
- `custom_portfolio/tools/strategy_attribution.py` - Performance metrics
- `custom_portfolio/tools/strategy_state.py` - Per-strategy state
- `lumibot/tools/virtual_position_tracker.py` - Position tracking

### Current Limitations
- **No existing web server** - Must build from scratch
- **In-memory only** - No persistent database
- **No API layer** - Direct Python object access only

---

## Architecture Options

**CONSTRAINT: LumiBot core must remain untouched. Dashboard is a completely separate process.**

### Option A: Direct Broker API Polling (Zero Code Changes)

```
┌─────────────────────────┐
│  Portfolio Manager      │
│  (LumiBot - untouched)  │
└─────────────────────────┘
            │
            │ trades via API
            ▼
┌─────────────────────────┐
│  ProjectX Broker API    │
└─────────────────────────┘
            ▲
            │ poll positions/orders/trades
            │
┌─────────────────────────┐
│  Dashboard Server       │
│  (separate process)     │
│  - Own ProjectXClient   │
│  - Polls every 5-30s    │
└─────────────────────────┘
            │
            ▼
┌─────────────────────────┐
│  Mobile Browser         │
└─────────────────────────┘
```

**Pros:**
- Zero changes to any trading code
- Gets real broker state (positions, orders, trades, account balance)
- Dashboard can run anywhere
- Survives trading process crashes

**Cons:**
- No strategy-level attribution (just aggregate positions)
- No virtual position details or bracket targets
- Can't see unrealized P&L per strategy
- Extra API calls to broker

**Best for:** Quick MVP, basic position monitoring

---

### Option B: File-Based State Export (Minimal Changes to custom_portfolio)

```
┌─────────────────────────┐
│  Portfolio Manager      │
│  (custom_portfolio/)    │───────► status.json (every iteration)
│                         │───────► trades.json (on trade)
└─────────────────────────┘
                                         │
                                         │ file read
                                         ▼
                                ┌─────────────────────┐
                                │  Dashboard Server   │
                                │  (polls files)      │
                                └─────────────────────┘
```

**Changes needed:** Add ~20 lines to `portfolio_manager.py` to write JSON status file.

**Pros:**
- Very loose coupling
- Full strategy-level data available
- Simple to implement
- Dashboard survives crashes

**Cons:**
- 1-5 second latency (file polling)
- Disk I/O on every iteration
- File locking considerations

**Best for:** Balance of data richness and simplicity

---

### Option C: Lightweight Status API in custom_portfolio (Recommended)

```
┌────────────────────────────────────────────────────────────┐
│  Portfolio Manager (custom_portfolio/)                     │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  MultiStrategyExecutorEnhanced                       │ │
│  │  (strategies, attribution, broker)                   │ │
│  └──────────────────────────────────────────────────────┘ │
│                          │                                 │
│                          ▼                                 │
│  ┌──────────────────────────────────────────────────────┐ │
│  │  StatusServer (minimal HTTP - background thread)     │ │
│  │  - GET /status → JSON snapshot of executor state     │ │
│  │  - Runs on localhost:9999                            │ │
│  │  - Read-only, no auth (localhost only)               │ │
│  └──────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
                           │ localhost:9999
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Dashboard Server (completely separate process)             │
│  - Polls localhost:9999/status for strategy data            │
│  - Polls broker API for confirmed positions                 │
│  - Serves mobile UI on port 8080                            │
│  - Handles auth, HTTPS, etc.                                │
└─────────────────────────────────────────────────────────────┘
                           │ HTTPS (external)
                           ▼
                    ┌──────────────┐
                    │  Mobile App  │
                    └──────────────┘
```

**Changes needed:**
- Add `StatusServer` class to `custom_portfolio/tools/` (~50-100 lines)
- Start it from `portfolio_manager.py` initialization (3 lines)
- LumiBot core remains **completely untouched**

**Pros:**
- Full strategy-level data (positions, signals, P&L, brackets)
- Near real-time (poll as fast as needed)
- Clean separation: status server is tiny, dashboard is full-featured
- Dashboard process handles all complexity (auth, HTTPS, UI)
- Dashboard survives strategy restarts

**Cons:**
- Small addition to custom_portfolio
- Two processes to manage

**Best for:** Production use with full data visibility

---

### Option D: Hybrid (Broker API + Status File)

Combine Options A and B:
- Dashboard polls broker API for confirmed positions/orders/balance
- Reads status file for strategy attribution and virtual positions
- Best of both worlds with graceful degradation

---

## Recommended: Option C (Lightweight Status API)

Minimal footprint in custom_portfolio (your code), full data access, clean separation.
LumiBot core stays 100% untouched.

---

## API Design

### Endpoints

```
GET  /api/status          - Overall system status
GET  /api/positions       - Current positions by strategy
GET  /api/performance     - Performance metrics by strategy
GET  /api/equity          - Equity curve data (last N points)
GET  /api/orders          - Active bracket orders
GET  /api/alerts          - Recent errors and warnings
WS   /ws/updates          - Real-time push updates
```

### Authentication

Simple token-based auth for MVP:
```python
# Environment variable
DASHBOARD_TOKEN=<random-32-char-token>

# Header-based auth
Authorization: Bearer <token>
```

Future: OAuth2 / Auth0 for production.

### Response Examples

#### GET /api/status
```json
{
  "timestamp": "2025-01-24T10:30:00Z",
  "trading_active": true,
  "session_status": "RTH",
  "next_session_event": "force_flat at 14:55 CT",
  "strategies_running": 12,
  "total_positions": 5,
  "unrealized_pnl": 1250.00,
  "realized_pnl_today": 500.00,
  "iteration_count": 1523,
  "last_iteration": "2025-01-24T10:29:55Z"
}
```

#### GET /api/positions
```json
{
  "positions": [
    {
      "strategy_id": "ES_RSI_ATR_001",
      "symbol": "ES",
      "quantity": 2,
      "entry_price": 5000.25,
      "current_price": 5002.50,
      "unrealized_pnl": 225.00,
      "bars_in_trade": 15,
      "stop_loss": 4995.00,
      "take_profit": 5010.00
    }
  ],
  "summary": {
    "total_exposure": 125000,
    "total_unrealized": 1250.00
  }
}
```

#### GET /api/performance
```json
{
  "strategies": [
    {
      "strategy_id": "ES_RSI_ATR_001",
      "symbol": "ES",
      "total_pnl": 2500.00,
      "trade_count": 45,
      "win_rate": 0.62,
      "profit_factor": 1.8,
      "sharpe_ratio": 1.5,
      "max_drawdown": -800.00
    }
  ],
  "portfolio": {
    "total_pnl": 15000.00,
    "sharpe_ratio": 1.2,
    "max_drawdown": -2500.00
  }
}
```

---

## Frontend Design

### Mobile-First Layout

```
┌────────────────────────────────────┐
│  Multi-Strategy Dashboard    [≡]  │  <- Header with menu
├────────────────────────────────────┤
│  ┌─────────┐ ┌─────────┐          │
│  │ $1,250  │ │ $15,000 │          │  <- Summary cards
│  │ Unreal. │ │ Total   │          │
│  └─────────┘ └─────────┘          │
├────────────────────────────────────┤
│  [Equity Curve Chart]             │  <- Sparkline chart
│  ────────────────────             │
├────────────────────────────────────┤
│  Positions (5)            ▼       │  <- Expandable
│  ├─ ES +2  +$225   [SL/TP]       │
│  ├─ NQ -1  -$150   [SL/TP]       │
│  └─ GC +1  +$75    [SL/TP]       │
├────────────────────────────────────┤
│  Alerts (2)               ▼       │  <- Expandable
│  ├─ WARN: Rate limit hit          │
│  └─ INFO: Session ending soon     │
├────────────────────────────────────┤
│  [Home] [Perf] [Alerts] [Config]  │  <- Bottom nav
└────────────────────────────────────┘
```

### Tech Stack

**Backend:**
- FastAPI (async, fast, auto-docs)
- Uvicorn (ASGI server)
- Python 3.12+

**Frontend:**
- Vanilla HTML/CSS/JS (no framework for simplicity)
- Chart.js for equity curves
- Responsive CSS (Tailwind or custom)
- PWA manifest for "Add to Home Screen"

**Security:**
- HTTPS via reverse proxy (nginx) or Cloudflare tunnel
- Token auth for API
- Rate limiting

---

## Implementation Phases

### Phase 1: Broker API MVP (Zero Changes)
- [ ] Create `dashboard/` directory (separate from lumibot)
- [ ] Dashboard server with ProjectXClient for broker data
- [ ] Endpoints: positions, orders, trades, account balance
- [ ] Basic mobile UI showing real broker state
- [ ] Token authentication

### Phase 2: Status Server in custom_portfolio (Optional Enhancement)
- [ ] Create `custom_portfolio/tools/status_server.py` (~50 lines)
- [ ] Single endpoint: GET /status → JSON snapshot
- [ ] Bind to localhost only (no auth needed)
- [ ] Add 3-line startup in portfolio_manager.py
- [ ] Dashboard polls this for strategy-level data

### Phase 3: Full Dashboard Frontend
- [ ] Mobile-first HTML template
- [ ] Responsive CSS
- [ ] Chart.js for equity curve
- [ ] PWA manifest for "Add to Home Screen"
- [ ] Merge broker data + strategy data in UI

### Phase 4: Production Hardening
- [ ] HTTPS via Cloudflare tunnel or nginx
- [ ] Rate limiting
- [ ] Error handling & reconnection logic
- [ ] Logging

---

## Technical Considerations

### Status Server (Option C) - Minimal Code in custom_portfolio

```python
# custom_portfolio/tools/status_server.py
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class StatusHandler(BaseHTTPRequestHandler):
    executor = None  # Set by StatusServer

    def do_GET(self):
        if self.path == "/status":
            data = self.executor.get_summary() if self.executor else {}
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        else:
            self.send_error(404)

    def log_message(self, *args):
        pass  # Suppress logging

class StatusServer:
    def __init__(self, executor, port=9999):
        StatusHandler.executor = executor
        self.server = HTTPServer(("127.0.0.1", port), StatusHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
```

```python
# In portfolio_manager.py (3 lines)
from custom_portfolio.tools.status_server import StatusServer

# In initialize():
self.status_server = StatusServer(self.executor, port=9999)
self.status_server.start()
```

### Dashboard Server (Separate Process)

```python
# dashboard/server.py (completely separate)
from fastapi import FastAPI, Depends, HTTPException
from lumibot.tools.projectx_helpers import ProjectXClient
import httpx

app = FastAPI()

# Poll broker directly
@app.get("/api/broker/positions")
async def get_broker_positions():
    client = ProjectXClient(config)
    return client.position_search_open(account_id)

# Poll status server (if running)
@app.get("/api/strategy/status")
async def get_strategy_status():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("http://localhost:9999/status", timeout=2.0)
            return r.json()
    except:
        return {"error": "Strategy status unavailable"}
```

### Data Flow

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Mobile Browser  │────►│ Dashboard Server │────►│ Broker API       │
│                  │     │ (port 8080)      │     │ (positions etc)  │
└──────────────────┘     │                  │     └──────────────────┘
                         │                  │
                         │                  │────►┌──────────────────┐
                         │                  │     │ Status Server    │
                         └──────────────────┘     │ (localhost:9999) │
                                                  │ (strategy data)  │
                                                  └──────────────────┘
```

### Memory Considerations

- LumiBot process: Unchanged
- Status server in LumiBot: ~5MB (just HTTP handler)
- Dashboard server: ~50-100MB (FastAPI + templates)

---

## IPC Options (Beyond HTTP)

Research into lightweight inter-process communication:

### Recommended: Unix Domain Sockets
**Top pick for local IPC:**
- Near-zero latency (~microseconds)
- Bidirectional (dashboard can send commands back)
- Clean failure handling - socket auto-cleanup
- Python stdlib only (`socket` module)
- ~50 lines of code

```python
# Bot side (in trading loop)
import socket, json, os
SOCKET_PATH = "/tmp/lumibot_status.sock"
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(SOCKET_PATH)
server.listen(1)
server.settimeout(0.1)  # Non-blocking

# Dashboard reads by connecting to socket
```

### Alternative: SQLite WAL Mode
**Good for persistence + simplicity:**
- Write status to SQLite from bot
- Dashboard reads with concurrent access (WAL mode)
- Free persistence/history
- ~30 lines of code
- Higher latency (~milliseconds) but survives crashes

### Skip These:
- **Redis** - overkill for single-machine IPC
- **Named pipes** - Unix sockets are strictly better
- **ZeroMQ** - adds dependency, no advantage here
- **mmap** - too low-level for this use case

---

## Remote Access Options

### Simplest Secure: Tailscale (Recommended)
- **Zero-config setup** - install, login, done in 5 minutes
- **Excellent mobile apps** with always-on support
- **No port forwarding** needed
- **Free tier**: 3 users, 100 devices (plenty for personal use)
- **Identity-based security** with SSO
- **Best for macOS**: Native app, works perfectly

### Quick Tunnel: Pinggy (Better than ngrok)
- **Free unlimited bandwidth** (ngrok = 5GB limit)
- **$3/month** for persistent custom domains
- **One command**: `ssh -p 443 -R0:localhost:8080 a.pinggy.io`
- Good for quick demos or testing

### ngrok (if you already use it)
- **Free tier limitations**: 5GB/month, random URLs per session
- **Paid**: $8/month for persistent URLs
- Works fine, just more expensive than Pinggy

### Most Robust: nginx + Cloudflare Tunnel
- **Full control** over infrastructure
- **Zero Trust security** - no ports exposed
- **Free** - no subscription costs
- Setup: 30-60 minutes initial
- Combine nginx flexibility with Cloudflare's security

### Avoid:
- **SSH tunnels from phone** - unreliable, battery drain
- **Direct port forwarding** - security risk for financial data

### Comparison Table

| Option | Setup Time | Cost | Bandwidth | Persistent URL | Mobile UX |
|--------|------------|------|-----------|----------------|-----------|
| **Tailscale** | 5 min | Free | Unlimited | N/A (VPN) | Excellent |
| **Pinggy** | 2 min | Free/$3 | Unlimited | Paid only | Good |
| **ngrok** | 2 min | Free/$8 | 5GB free | Paid only | Good |
| **Cloudflare Tunnel** | 15 min | Free | Unlimited | Yes | Good |

---

## Creative Feature Ideas

### Tier 1: Critical Safety (Build First)

**1. Bracket Watchdog** ⭐ HIGHEST PRIORITY
- Real-time scanner for orphaned brackets
- Alerts when position exists without SL/TP
- One-click fix to place missing bracket
- **Why**: Solves ProjectX's broken `linkedOrderId` problem

**2. Session Clock**
- Countdown to force_flat, session boundaries
- Visual "safe zone" vs "danger zone"
- Shows if ALLOW_TRADES_UNTIL_FORCE_FLAT is active
- Circular progress ring (green → yellow → red)

**3. Strategy Heartbeat**
- Visual pulse per strategy showing activity
- Signal generation rate, fill rate, bracket integrity
- Quickly spot "zombie" strategies that stopped trading

### Tier 2: Risk Visibility

**4. Risk Budget Treemap**
- Each strategy as rectangle sized by capital usage
- Color by P&L (green/red)
- Instantly see concentration risk

**5. Yesterday vs Today Timeline**
- Side-by-side comparison
- "This time yesterday you were +$X"
- Pattern recognition for anomalies

### Tier 3: Quality of Life

**6. Daily Digest Email**
- 7am summary: yesterday's P&L, best/worst strategies
- Upcoming session events
- System health check

**7. Anomaly Detection**
- Alert when strategy deviates 2+ std from historical
- Unusual cancellation rates
- Fill slippage exceeding normal

**8. Apple Watch Complications**
- P&L on watch face
- Active position count
- Haptic tap on big wins/losses

**9. Voice Query (Siri Shortcuts)**
- "How's my trading bot?" → P&L summary
- "Pause all trading" → Emergency stop

---

## Mobile UX Best Practices

### Key Metrics at a Glance
- **Total P&L** (large, color-coded)
- **System Health** indicator (🟢🟡🔴)
- **Net Positions** count
- **Session Status** (RTH/ETH/Maintenance)

### Position Cards (Swipeable)
```
┌─────────────────────────────────┐
│ RSI_ES_14   LONG 2 contracts    │
│ Entry: 5000.0 | Now: 5012.5     │
│ P&L: +$625.00 (+1.25%)  🎯🛑   │
│ 🟢 Brackets active | 14 bars    │
└─────────────────────────────────┘
```

### Alert Levels
- **Critical (push immediately)**: Bracket orphan, connection loss, large loss
- **High (push)**: Bracket fills, stop-outs, order rejections
- **Info (in-app only)**: Session changes, signals generated

### Dark Mode
- Essential for night trading
- High contrast: `#00FF88` (green), `#FF4444` (red)
- True black backgrounds for OLED

### What NOT to Include
- Strategy code editing (desktop only)
- Backtesting interface (too complex)
- Custom charting tools (use TradingView)
- News feeds (distracting)

---

## Open Questions

1. **IPC Method**: Unix socket vs SQLite vs HTTP?
   - Socket = lowest latency, bidirectional
   - SQLite = free persistence, survives crashes
   - HTTP = simplest, slightly higher latency

2. **Remote Access**: Tailscale vs Cloudflare Tunnel?
   - Tailscale = 5-minute setup, great mobile apps
   - Cloudflare = more robust, web-accessible URL

3. **Historical Data**: How much to retain?
   - Today only (simplest)
   - Last 7 days (useful)
   - Full backtest results (complex)

4. **Actions Allowed**: Read-only vs control?
   - View only (safer)
   - Emergency flatten all
   - Pause/resume specific strategies
   - Adjust position sizes

5. **Notification Thresholds**:
   - Loss amount for critical alert ($100? $500?)
   - Drawdown % for warning (5%? 10%?)

---

## Web Framework Comparison

| Framework | Memory | WebSocket | Async | Setup | Best For |
|-----------|--------|-----------|-------|-------|----------|
| **FastAPI** ⭐ | 20-30MB | Native | Yes | Easy | Production dashboard |
| **Starlette** | 10-15MB | Native | Yes | Medium | Lighter FastAPI |
| **Flask** | 30+MB | Extension | No | Easy | Simple REST only |
| **Bottle** | 10-15MB | None | No | Easy | Minimal REST |
| **http.server** | Minimal | None | No | None | Don't use |

**Recommendation: FastAPI**
- WebSocket support (critical for real-time)
- Auto-generated API docs
- ~25MB memory alongside trading bot
- 15 lines for full WebSocket + REST API

```python
from fastapi import FastAPI, WebSocket
app = FastAPI()

@app.get("/status")
async def get_status():
    return executor.get_summary()

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    while True:
        await ws.send_json(executor.get_summary())
        await asyncio.sleep(1)
```

---

## Frontend: PWA vs Native

**Recommendation: PWA first, native wrapper if needed**

| Approach | Time to MVP | iOS Push | Offline | Biometric |
|----------|-------------|----------|---------|-----------|
| **PWA** | 3-4 weeks | ❌ Broken | ✅ Yes | ⚠️ WebAuthn |
| **React Native** | 6-8 weeks | ✅ Yes | ✅ Yes | ✅ Native |
| **Flutter** | 5-7 weeks | ✅ Yes | ✅ Yes | ✅ Native |

**PWA Stack (Minimum Viable):**
```
next.js          # React framework
lightweight-charts  # TradingView charts
socket.io-client   # WebSocket
next-pwa          # Service worker
```

**Strategy:**
1. Launch PWA for Android + web (3-4 weeks)
2. Add iOS wrapper via Capacitor if push needed (week 5-6)
3. Full React Native only if platform APIs required

**iOS Push Workaround:** Use Pushover/Telegram for critical alerts instead of native push.

---

## Data Persistence Strategy

**Recommendation: SQLite with WAL mode** (simplest, good enough)

### Why SQLite Wins
- 1-5 writes/sec trivial with WAL mode
- Concurrent read/write (dashboard reads while bot writes)
- ~50MB for 7 days of data
- Zero dependencies (stdlib)
- Survives crashes

### Schema
```sql
-- Status snapshots (write every 1-5 sec)
CREATE TABLE snapshots (
    timestamp INTEGER PRIMARY KEY,
    equity REAL,
    positions_json TEXT,
    pnl_realized REAL,
    pnl_unrealized REAL,
    is_trading INTEGER
);

-- Trade history
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    timestamp INTEGER,
    symbol TEXT,
    side TEXT,
    quantity REAL,
    price REAL,
    pnl REAL,
    strategy TEXT
);

-- Alerts
CREATE TABLE alerts (
    id INTEGER PRIMARY KEY,
    timestamp INTEGER,
    level TEXT,
    message TEXT
);
```

### Alternative: Hybrid SQLite + Parquet
- SQLite for last 48 hours (hot data)
- Parquet archives for days 2-7 (cold data)
- Better compression (~30MB vs 50MB)
- More complex (nightly archival job)

---

## Push Notifications

### Top 3 Options

| Service | Cost | Latency | Reliability | Setup |
|---------|------|---------|-------------|-------|
| **Pushover** ⭐ | $5 one-time | <1s | Highest | Easiest |
| **Telegram Bot** | Free | 1-5s | High | Easy |
| **ntfy.sh** | Free (self-host) | <1s | You manage | Medium |

**Recommendation: Pushover for critical alerts**
- $5 one-time, 7,500 messages/month
- Sub-second delivery
- Priority levels with custom sounds

```python
import requests

def send_alert(message, priority=1):
    requests.post("https://api.pushover.net/1/messages.json", data={
        "token": "APP_TOKEN",
        "user": "USER_KEY",
        "message": message,
        "priority": priority,
        "sound": "bugle" if priority >= 1 else "pushover"
    })

# Critical: bracket orphaned
send_alert("🚨 ES bracket orphaned!", priority=2)
```

**Backup: Telegram Bot** (free, unlimited)
- Good for informational alerts (trade fills, daily P&L)
- Richer formatting (charts, tables)

---

## Existing Code to Leverage

**Methods ready to use in MultiStrategyExecutorEnhanced:**

| Method | Returns | Use For |
|--------|---------|---------|
| `get_summary()` | Dict | Main status endpoint |
| `get_all_strategies()` | List[State] | Per-strategy details |
| `get_performance_report()` | Report | Attribution metrics |
| `on_trading_iteration()` | Dict | Iteration stats |

**Already JSON-serializable** - no conversion needed:
```python
# Status server endpoint (one line)
return executor.get_summary()
```

---

## Dependencies

```
# requirements.txt additions
fastapi>=0.109.0
uvicorn>=0.27.0
websockets>=12.0
requests>=2.31.0  # for Pushover
```

---

## References

- FastAPI docs: https://fastapi.tiangolo.com/
- Chart.js docs: https://www.chartjs.org/
- PWA guide: https://web.dev/progressive-web-apps/

---

## Appendix: Data Structure Summary

### EnhancedStrategyState Fields
- `strategy_id`, `symbol`, `params`, `contracts`
- `tracker` (VirtualPositionTracker)
- `entry_time`, `entry_price`, `entry_side`, `bars_in_trade`
- `take_profit_price`, `stop_loss_price`, `last_atr`
- `realized_pnl`, `unrealized_pnl`, `total_fees_paid`
- `last_signal`, `last_signal_time`
- `trade_history` (list of completed trades)

### StrategyAttribution Methods
- `get_metrics(strategy_id)` → dict of performance stats
- `get_snapshots_df()` → pandas DataFrame
- `get_equity_curve_df()` → pandas DataFrame
- `generate_report()` → comprehensive DataFrame

### VirtualPositionTracker Methods
- `get_position(symbol)` → quantity, avg_price
- `get_all_positions()` → dict
- `get_total_pnl()` → float
