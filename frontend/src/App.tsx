import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import {
  Activity, BarChart3, BookOpenCheck, Bot, ChevronRight, Download, History,
  LogOut, Moon, Play, RefreshCw, Settings, Sun, TerminalSquare, WalletCards
} from "lucide-react";
import {
  createBacktest, getBacktest, getBacktests, getCandles, getHealth, getMe,
  getRuns, getSettings, getSnapshot, getTrades, login, logout, queueClaim
} from "./api";
import { MarketChart, RunControls, SignalMatrix, StatusDot, WindowRail } from "./components";
import type { BacktestJob, BotRun, Candle, DashboardSettings, Snapshot, Trade } from "./types";

const emptySnapshot: Snapshot = {
  guide_profile: "polymarket-btc-5m-v1",
  active_run: null,
  latest_run: null,
  market: { server_time: Date.now() / 1000, window_ts: 0, close_ts: 0, slug: "", btc_price: null, window_open: null, delta_pct: null, up_price: null, down_price: null, market_available: false },
  trades: [], events: [], workers: [],
  trader_readiness: {
    worker_online: false, worker_stale: true, worker_status: "missing", last_seen: null,
    credentials: {}, credentials_complete: false, api_valid: false, balance_check_ok: false,
    usdc_balance: null, live_trading_enabled: false, web_live_trading_enabled: false,
    signature_type: null, preflight_at: null, can_start_live: false
  },
  claim_readiness: {
    worker_online: false, worker_stale: true, worker_status: "missing", last_seen: null,
    auto_claim_enabled: false, credentials: {}, credentials_complete: false,
    sdk_ready: false, auth_mode: null, clob_auth_mode: null, wallet: null, pending_claims: 0,
    failed_claims: 0, last_error: null, can_auto_claim: false
  },
  stats: { trades: 0, pnl: 0, wins: 0 }
};

export default function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [snapshotReady, setSnapshotReady] = useState(false);
  const [snapshot, setSnapshot] = useState<Snapshot>(emptySnapshot);
  const [candles, setCandles] = useState<Candle[]>([]);
  const [theme, setTheme] = useState(() => localStorage.getItem("polybot-theme") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));

  const refresh = useCallback(async () => {
    try { setSnapshot(await getSnapshot()); setSnapshotReady(true); }
    catch (error) {
      if (error instanceof Error && error.message.includes("đăng nhập")) {
        setSnapshotReady(false);
        setAuthenticated(false);
      }
    }
  }, []);

  useEffect(() => { getMe().then(() => setAuthenticated(true)).catch(() => setAuthenticated(false)); }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("polybot-theme", theme);
  }, [theme]);
  useEffect(() => {
    if (!authenticated) return;
    refresh();
    getCandles().then((result) => setCandles(result.candles)).catch(() => undefined);
    const snapshotTimer = window.setInterval(refresh, 2500);
    const candleTimer = window.setInterval(() => getCandles().then((result) => setCandles(result.candles)).catch(() => undefined), 15_000);
    const stream = new EventSource("/api/events");
    stream.onmessage = () => refresh();
    return () => { clearInterval(snapshotTimer); clearInterval(candleTimer); stream.close(); };
  }, [authenticated, refresh]);

  if (authenticated === null) return <div className="boot-screen"><Bot size={28} /><span>Đang khởi động trạm điều khiển…</span></div>;
  if (!authenticated) return <LoginScreen onLogin={() => setAuthenticated(true)} theme={theme} toggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")} />;
  if (!snapshotReady) return <div className="boot-screen"><Bot size={28} /><span>Đang đồng bộ trạng thái worker…</span></div>;

  return <DashboardShell snapshot={snapshot} candles={candles} theme={theme} toggleTheme={() => setTheme(theme === "dark" ? "light" : "dark")} refresh={refresh} onLogout={() => { logout().finally(() => { setSnapshotReady(false); setAuthenticated(false); }); }} />;
}

function LoginScreen({ onLogin, theme, toggleTheme }: { onLogin: () => void; theme: string; toggleTheme: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { await login(password); onLogin(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Không thể đăng nhập."); }
    finally { setBusy(false); }
  }
  return (
    <main className="login-screen">
      <button className="icon-button theme-login" onClick={toggleTheme} title="Đổi giao diện">{theme === "dark" ? <Sun /> : <Moon />}</button>
      <div className="login-instrument" aria-hidden="true">
        <span>300s</span><i /><span>T-40</span><i /><span>T-10</span><i className="hot" /><span>T-5</span>
      </div>
      <form className="login-panel" onSubmit={submit}>
        <div className="brand-mark"><Bot size={25} /><span>BTC 5M</span></div>
        <span className="eyebrow">POLYMARKET CONTROL STATION</span>
        <h1>Đọc đúng cửa sổ.<br />Giữ đúng kỷ luật.</h1>
        <p>Dashboard riêng cho bot BTC Up/Down 5 phút. Mọi tham số chiến lược live đều bị khóa theo build guide.</p>
        <label>Mật khẩu quản trị<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Nhập mật khẩu" autoFocus /></label>
        {error && <p className="login-error">{error}</p>}
        <button className="button primary login-button" disabled={busy || !password}>Đăng nhập <ChevronRight size={17} /></button>
        <small>Không có private key nào được gửi xuống trình duyệt.</small>
      </form>
    </main>
  );
}

function DashboardShell({ snapshot, candles, theme, toggleTheme, refresh, onLogout }: { snapshot: Snapshot; candles: Candle[]; theme: string; toggleTheme: () => void; refresh: () => void; onLogout: () => void }) {
  const location = useLocation();
  const titles: Record<string, string> = { "/": "Tổng quan", "/history": "Lịch sử giao dịch", "/backtests": "Backtest", "/settings": "Cấu hình vận hành", "/system": "Hệ thống" };
  const nav = [
    ["/", Activity, "Tổng quan"], ["/history", History, "Lịch sử"], ["/backtests", BarChart3, "Backtest"],
    ["/settings", Settings, "Cấu hình"], ["/system", TerminalSquare, "Hệ thống"]
  ] as const;
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-brand"><Bot size={24} /><div><b>BTC 5M</b><small>CONTROL</small></div></div>
        <nav>{nav.map(([path, Icon, label]) => <NavLink key={path} to={path} end={path === "/"} title={label}><Icon size={19} /><span>{label}</span></NavLink>)}</nav>
        <div className="sidebar-foot"><div className="guide-lock"><BookOpenCheck size={17} /><div><small>GUIDE LOCKED</small><b>v1</b></div></div><button className="nav-utility" onClick={onLogout} title="Đăng xuất"><LogOut size={18} /><span>Đăng xuất</span></button></div>
      </aside>
      <div className="main-column">
        <header className="topbar"><div><span className="eyebrow">{snapshot.guide_profile}</span><h1>{titles[location.pathname] || "Dashboard"}</h1></div><div className="top-actions"><button className="icon-button" onClick={refresh} title="Làm mới"><RefreshCw size={18} /></button><button className="icon-button" onClick={toggleTheme} title="Đổi giao diện">{theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}</button><div className="worker-pill" title={snapshot.trader_readiness.can_start_live ? "Trader sẵn sàng chạy live" : "Trader đang khóa live"}><StatusDot active={snapshot.trader_readiness.worker_online} /><span>{snapshot.trader_readiness.can_start_live ? "Trader ready" : "Trader locked"}</span></div></div></header>
        <main className="content">
          <Routes>
            <Route path="/" element={<Overview snapshot={snapshot} candles={candles} theme={theme} refresh={refresh} />} />
            <Route path="/history" element={<HistoryPage initial={snapshot.trades} />} />
            <Route path="/backtests" element={<BacktestPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/system" element={<SystemPage snapshot={snapshot} />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
      <nav className="mobile-nav">{nav.map(([path, Icon, label]) => <NavLink key={path} to={path} end={path === "/"}><Icon size={19} /><span>{label}</span></NavLink>)}</nav>
    </div>
  );
}

function Overview({ snapshot, candles, theme, refresh }: { snapshot: Snapshot; candles: Candle[]; theme: string; refresh: () => void }) {
  const winRate = snapshot.stats.trades ? snapshot.stats.wins / snapshot.stats.trades : 0;
  const lastTrade = snapshot.trades[0];
  return (
    <div className="page-stack">
      <WindowRail market={snapshot.market} status={snapshot.active_run?.status || "idle"} />
      <section className="metrics-band">
        <Metric label="Trạng thái bot" value={snapshot.active_run ? snapshot.active_run.status : "Đang nghỉ"} meta={snapshot.active_run ? `${snapshot.active_run.run_kind} · ${snapshot.active_run.mode}` : "Chưa có phiên hoạt động"} active={Boolean(snapshot.active_run)} />
        <Metric label="PnL đã ghi nhận" value={`${snapshot.stats.pnl >= 0 ? "+" : ""}$${snapshot.stats.pnl.toFixed(2)}`} meta={`${snapshot.stats.trades} giao dịch`} tone={snapshot.stats.pnl >= 0 ? "up" : "down"} />
        <Metric label="Win rate" value={`${(winRate * 100).toFixed(0)}%`} meta={`${snapshot.stats.wins}/${snapshot.stats.trades || 0} thắng`} />
        <Metric label="Vị thế gần nhất" value={lastTrade ? `${lastTrade.direction.toUpperCase()} · $${lastTrade.bet.toFixed(2)}` : "—"} meta={lastTrade ? `${lastTrade.pnl >= 0 ? "+" : ""}${lastTrade.pnl.toFixed(3)} PnL` : "Chưa có dữ liệu"} tone={lastTrade ? (lastTrade.pnl >= 0 ? "up" : "down") : ""} />
      </section>
      <div className="overview-grid">
        <section className="data-panel chart-panel"><PanelTitle eyebrow="BINANCE · 1M" title="BTC trong vùng quyết định" meta={snapshot.market.window_open ? `Open $${snapshot.market.window_open.toLocaleString()}` : "Đang đồng bộ"} /><MarketChart candles={candles} windowOpen={snapshot.market.window_open} theme={theme} /></section>
        <section className="data-panel signal-panel"><PanelTitle eyebrow="COMPOSITE SIGNAL" title="Bảy lớp bằng chứng" meta="Window Delta ưu tiên" /><SignalMatrix events={snapshot.events} fallback={lastTrade?.breakdown} /></section>
        <RunControls snapshot={snapshot} onChanged={refresh} />
      </div>
      <TradeLedger trades={snapshot.trades.slice(0, 8)} compact onChanged={refresh} />
    </div>
  );
}

function Metric({ label, value, meta, tone = "", active = false }: { label: string; value: string; meta: string; tone?: string; active?: boolean }) {
  return <div className={`metric ${tone}`}><small>{label}</small><strong>{active && <StatusDot active />}{value}</strong><span>{meta}</span></div>;
}

function PanelTitle({ eyebrow, title, meta }: { eyebrow: string; title: string; meta: string }) {
  return <div className="panel-title"><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div><small>{meta}</small></div>;
}

function TradeLedger({ trades, compact = false, onChanged }: { trades: Trade[]; compact?: boolean; onChanged?: () => void }) {
  return <section className="ledger"><div className="ledger-title"><div><span className="eyebrow">TRADE LEDGER</span><h2>{compact ? "Giao dịch gần nhất" : "Toàn bộ lịch sử"}</h2></div><span>{trades.length} dòng</span></div><div className="table-scroll"><table><thead><tr><th>Thời gian</th><th>Hướng</th><th>Signal</th><th>Entry</th><th>Stake</th><th>Kết quả</th><th>PnL</th><th>Claim</th></tr></thead><tbody>{trades.length ? trades.map((trade) => <tr key={trade.id}><td className="mono">{formatTime(trade.created_at)}</td><td><span className={`direction ${trade.direction}`}>{trade.direction.toUpperCase()}</span></td><td className="mono">{trade.score > 0 ? "+" : ""}{trade.score.toFixed(1)} · {(trade.confidence * 100).toFixed(0)}%</td><td className="mono">${trade.entry_price.toFixed(2)}</td><td className="mono">${trade.bet.toFixed(2)}</td><td>{trade.won == null ? "Chờ" : trade.won ? "Thắng" : "Thua"}</td><td className={`mono ${trade.pnl >= 0 ? "text-up" : "text-down"}`}>{trade.pnl >= 0 ? "+" : ""}${trade.pnl.toFixed(3)}</td><td><ClaimCell trade={trade} onChanged={onChanged} /></td></tr>) : <tr><td colSpan={8}><EmptyState text="Chưa có giao dịch. Bắt đầu một dry-run để thu dữ liệu thật." /></td></tr>}</tbody></table></div></section>;
}

function ClaimCell({ trade, onChanged }: { trade: Trade; onChanged?: () => void }) {
  const [busy, setBusy] = useState(false);
  if (!trade.claim_required) return <>—</>;
  const labels: Record<string, string> = {
    pending: "Đang chờ", checking: "Đang kiểm tra", awaiting_resolution: "Chờ resolve",
    submitting: "Đang gửi", submitted: "Đang xác nhận", claimed: "Đã claim",
    acknowledged: "Đã xử lý", failed: "Lỗi claim", manual_required: "Cần thủ công"
  };
  const retryable = trade.claim_status === "failed" || trade.claim_status === "manual_required";
  async function retry() {
    setBusy(true);
    try { await queueClaim(trade.id); onChanged?.(); }
    finally { setBusy(false); }
  }
  const completed = trade.claim_status === "claimed" || trade.claim_status === "acknowledged";
  return <div className={`claim-state ${trade.claim_status}`} title={trade.claim_error || undefined}><span>{labels[trade.claim_status] || trade.claim_status}</span>{retryable && <button className="icon-button claim-retry" onClick={retry} disabled={busy} title="Đưa lại vào hàng đợi claim"><RefreshCw size={14} /></button>}{!completed && <a href={trade.market_url} target="_blank" rel="noreferrer" className="claim-link" title="Mở market để claim thủ công" aria-label="Mở market để claim thủ công">↗</a>}</div>;
}

function HistoryPage({ initial }: { initial: Trade[] }) {
  const [trades, setTrades] = useState(initial);
  const [filter, setFilter] = useState("all");
  const refresh = useCallback(() => { getTrades().then((result) => setTrades(result.items)); }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const filtered = trades.filter((trade) => filter === "all" || trade.direction === filter || (filter === "wins" && trade.won) || (filter === "losses" && trade.won === false));
  return <div className="page-stack"><section className="page-intro"><div><span className="eyebrow">LỊCH SỬ BỀN VỮNG</span><h2>Mỗi quyết định, đủ ngữ cảnh</h2><p>Signal, giá khớp, kết quả và trạng thái claim được lưu độc lập với log tạm thời.</p></div><div className="segmented filter"><button className={filter === "all" ? "selected" : ""} onClick={() => setFilter("all")}>Tất cả</button><button className={filter === "up" ? "selected" : ""} onClick={() => setFilter("up")}>Up</button><button className={filter === "down" ? "selected" : ""} onClick={() => setFilter("down")}>Down</button><button className={filter === "wins" ? "selected" : ""} onClick={() => setFilter("wins")}>Thắng</button><button className={filter === "losses" ? "selected" : ""} onClick={() => setFilter("losses")}>Thua</button></div></section><TradeLedger trades={filtered} onChanged={refresh} /></div>;
}

function BacktestPage() {
  const [jobs, setJobs] = useState<BacktestJob[]>([]);
  const [selected, setSelected] = useState<BacktestJob | null>(null);
  const [hours, setHours] = useState(72); const [starting, setStarting] = useState(100); const [minBet, setMinBet] = useState(1);
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const refresh = useCallback(() => getBacktests().then((result) => setJobs(result.items)), []);
  useEffect(() => { refresh(); const timer = setInterval(refresh, 3000); return () => clearInterval(timer); }, [refresh]);
  async function submit(event: FormEvent) { event.preventDefault(); setBusy(true); setError(""); try { const job = await createBacktest({ hours, starting_bankroll: starting, min_bet: minBet }); setSelected(job); refresh(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Không thể tạo backtest."); } finally { setBusy(false); } }
  async function open(job: BacktestJob) { setSelected(job.status === "completed" ? await getBacktest(job.id) : job); }
  const configs = selected?.results?.configs || [];
  return <div className="page-stack"><section className="backtest-launch"><div><span className="eyebrow">T-60 HISTORICAL MODEL</span><h2>So sánh 27 cấu hình</h2><p>Backtest chạy ở worker riêng. Kết quả mang tính định hướng; dry-run với live ask vẫn là phép thử thực tế hơn.</p></div><form onSubmit={submit}><label>Khoảng dữ liệu<select value={hours} onChange={(event) => setHours(Number(event.target.value))}><option value={24}>24 giờ</option><option value={72}>72 giờ</option><option value={168}>7 ngày</option><option value={336}>14 ngày</option><option value={720}>30 ngày</option></select></label><label>Bankroll<input type="number" min="1" value={starting} onChange={(event) => setStarting(Number(event.target.value))} /></label><label>Min bet<input type="number" min="0.01" step="0.01" value={minBet} onChange={(event) => setMinBet(Number(event.target.value))} /></label><button className="button primary" disabled={busy}><Play size={16} /> Chạy backtest</button>{error && <small className="text-down">{error}</small>}</form></section><div className="backtest-grid"><section className="job-list"><PanelTitle eyebrow="JOB QUEUE" title="Các lần chạy" meta={`${jobs.length} job`} />{jobs.map((job) => <button key={job.id} className={`job-row ${selected?.id === job.id ? "selected" : ""}`} onClick={() => open(job)}><span className={`job-state ${job.status}`} /> <div><b>{job.hours} giờ · ${job.starting_bankroll}</b><small>{job.status} · {formatTime(job.created_at)}</small></div><ChevronRight size={16} /></button>)}{!jobs.length && <EmptyState text="Chưa có backtest nào." />}</section><section className="backtest-result"><PanelTitle eyebrow="RESULT MATRIX" title={selected?.best ? `${selected.best.mode} @ ${(selected.best.threshold * 100).toFixed(0)}%` : "Chọn một kết quả"} meta={selected?.best ? `${(selected.best.roi * 100).toFixed(1)}% ROI` : ""} />{configs.length ? <><div className="heatmap">{configs.map((item) => <div key={`${item.mode}-${item.threshold}`} className="heat-cell" style={{ "--heat": `${Math.max(-1, Math.min(1, item.roi))}` } as React.CSSProperties} title={`${item.mode} @ ${item.threshold}: ${(item.roi * 100).toFixed(1)}%`}><small>{item.mode.slice(0, 3)} · {(item.threshold * 100).toFixed(0)}</small><b>{(item.roi * 100).toFixed(0)}%</b></div>)}</div><a className="button secondary download" href={`/api/backtests/${selected?.id}/download`}><Download size={16} /> Tải XLSX</a></> : <EmptyState text={selected ? `Trạng thái: ${selected.status}` : "Kết quả ma trận sẽ xuất hiện ở đây."} />}</section></div></div>;
}

function SettingsPage() {
  const [settings, setSettings] = useState<DashboardSettings | null>(null);
  const [runs, setRuns] = useState<BotRun[]>([]);
  const refresh = useCallback(() => {
    getSettings().then(setSettings).catch(() => undefined);
    getRuns().then((result) => setRuns(result.items)).catch(() => undefined);
  }, []);
  useEffect(() => { refresh(); const timer = window.setInterval(refresh, 5000); return () => window.clearInterval(timer); }, [refresh]);
  const guide = settings?.guide || {};
  const environment = settings?.environment || {};
  const readiness = settings?.trader_readiness;
  const claims = settings?.claim_readiness;
  return <div className="page-stack"><section className="page-intro"><div><span className="eyebrow">RUNTIME ONLY</span><h2>Frontend không chỉnh chiến lược</h2><p>Mode và hạn mức được chụp tại lúc tạo run. Timing, confidence và trọng số luôn lấy từ Guide Profile.</p></div><div className="guide-badge"><BookOpenCheck /><div><small>PROFILE</small><b>{String(guide.profile_id || "—")}</b></div></div></section><div className="settings-grid"><section className="settings-section"><PanelTitle eyebrow="GUIDE CONSTANTS" title="Thông số bị khóa" meta="Read-only" /><dl className="definition-list"><Definition label="Window" value={`${guide.window_seconds || 300}s`} /><Definition label="Thu tick" value={`T-${guide.tick_start || 40}s`} /><Definition label="Snipe" value={`T-${guide.snipe_start || 10}s`} /><Definition label="Hạn chót" value={`T-${guide.hard_deadline || 5}s`} /><Definition label="Poll" value={`${guide.poll_interval || 2}s`} /><Definition label="Spike" value={String(guide.spike_threshold || 1.5)} /></dl></section><section className="settings-section"><PanelTitle eyebrow="TRADER-WORKER ENV" title="Khóa và credentials" meta={readiness?.can_start_live ? "Live ready" : "Live locked"} /><div className="env-list">{Object.entries(environment).map(([name, ready]) => <div key={name}><StatusDot active={ready} /><code>{name}</code><span>{ready ? "Đã cấu hình" : "Còn thiếu"}</span></div>)}</div><div className="readiness-list"><ReadinessRow label="Worker heartbeat" ready={Boolean(readiness?.worker_online)} value={readiness?.worker_online ? "Online" : "Offline / stale"} /><ReadinessRow label="CLOB preflight" ready={Boolean(readiness?.api_valid)} value={readiness?.api_valid ? "Hợp lệ" : "Chưa đạt"} /><ReadinessRow label="Số dư USDC" ready={Boolean(readiness?.balance_check_ok)} value={readiness?.usdc_balance == null ? "Chưa đọc được" : `$${readiness.usdc_balance.toFixed(2)}`} /><ReadinessRow label="Live lock · web" ready={Boolean(readiness?.web_live_trading_enabled)} value={readiness?.web_live_trading_enabled ? "Đã bật" : "Đang khóa"} /><ReadinessRow label="Live lock · worker" ready={Boolean(readiness?.live_trading_enabled)} value={readiness?.live_trading_enabled ? "Đã bật" : "Đang khóa"} /><ReadinessRow label="Signature type" ready={readiness?.signature_type != null} value={readiness?.signature_type == null ? "Không hợp lệ" : String(readiness.signature_type)} /></div>{readiness?.preflight_at && <small className="preflight-time">Preflight {formatTime(readiness.preflight_at)}</small>}</section><ClaimSettingsSection readiness={claims} /></div><section className="ledger"><div className="ledger-title"><div><span className="eyebrow">CONFIG SNAPSHOTS</span><h2>Các phiên gần đây</h2></div></div><div className="run-strip">{runs.slice(0, 8).map((run) => <div key={run.id}><small>{formatTime(run.created_at)}</small><b>{run.run_kind} · {run.mode}</b><span>${run.session_budget} / min ${run.min_bet}</span></div>)}</div></section></div>;
}

function ClaimSettingsSection({ readiness }: { readiness: DashboardSettings["claim_readiness"] | undefined }) {
  const clobReady = readiness?.clob_auth_mode === "provided" || readiness?.clob_auth_mode === "derived";
  return <section className="settings-section"><PanelTitle eyebrow="CLAIM-WORKER" title="Redeem qua relayer" meta={readiness?.can_auto_claim ? "Auto claim ready" : "Auto claim locked"} /><div className="readiness-list"><ReadinessRow label="Worker heartbeat" ready={Boolean(readiness?.worker_online)} value={readiness?.worker_online ? "Online" : "Offline / stale"} /><ReadinessRow label="Auto claim" ready={Boolean(readiness?.auto_claim_enabled)} value={readiness?.auto_claim_enabled ? "Đã bật" : "Đang khóa"} /><ReadinessRow label="Relayer credentials" ready={Boolean(readiness?.credentials_complete)} value={readiness?.credentials_complete ? readiness?.auth_mode || "Đầy đủ" : "Còn thiếu"} /><ReadinessRow label="CLOB auth" ready={clobReady} value={readiness?.clob_auth_mode === "provided" ? "Đã cấu hình" : readiness?.clob_auth_mode === "derived" ? "SDK tự derive" : "Chưa đạt"} /><ReadinessRow label="SDK + wallet" ready={Boolean(readiness?.sdk_ready)} value={readiness?.sdk_ready ? "Đã xác thực" : "Chưa đạt"} /><ReadinessRow label="Đang chờ" ready={(readiness?.pending_claims || 0) === 0} value={String(readiness?.pending_claims || 0)} /><ReadinessRow label="Claim lỗi" ready={(readiness?.failed_claims || 0) === 0} value={String(readiness?.failed_claims || 0)} /></div>{readiness?.last_error && <small className="text-down preflight-time">{readiness.last_error}</small>}</section>;
}

function Definition({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd>{value}</dd></div>; }
function ReadinessRow({ label, ready, value }: { label: string; ready: boolean; value: string }) { return <div><span>{label}</span><b className={ready ? "text-up" : "text-down"}>{value}</b></div>; }

function SystemPage({ snapshot }: { snapshot: Snapshot }) {
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  useEffect(() => { getHealth().then(setHealth); }, []);
  return <div className="page-stack"><section className="system-band"><div><span className="eyebrow">SERVICE HEALTH</span><h2>Bốn process, một nguồn trạng thái</h2><p>Trader, backtest và claim tách riêng để không chen vào cửa sổ đặt lệnh.</p></div><Activity size={34} /></section><section className="worker-grid">{["web", "trader-worker", "backtest-worker", "claim-worker"].map((role) => { const worker = snapshot.workers.find((item) => item.role === role); const ok = role === "web" || (role === "trader-worker" ? snapshot.trader_readiness.worker_online : role === "claim-worker" ? snapshot.claim_readiness.worker_online : Boolean(worker)); return <div className="worker-block" key={role}><div><StatusDot active={ok} /><b>{role}</b></div><strong>{role === "web" ? "healthy" : worker?.status || "chưa kết nối"}</strong><small>{worker?.last_seen ? `Heartbeat ${formatTime(worker.last_seen)}` : role === "web" ? "API đang phản hồi" : "Khởi chạy service cùng PostgreSQL"}</small></div>; })}</section><section className="readiness-summary"><div><span className="eyebrow">LIVE READINESS</span><h2>{snapshot.trader_readiness.can_start_live ? "Sẵn sàng giao dịch thật" : "Live đang được khóa an toàn"}</h2></div><div><ReadinessRow label="Credentials" ready={snapshot.trader_readiness.credentials_complete} value={snapshot.trader_readiness.credentials_complete ? "Đầy đủ" : "Còn thiếu"} /><ReadinessRow label="API + balance" ready={snapshot.trader_readiness.api_valid} value={snapshot.trader_readiness.usdc_balance == null ? "Chưa đạt" : `$${snapshot.trader_readiness.usdc_balance.toFixed(2)}`} /><ReadinessRow label="Auto claim" ready={snapshot.claim_readiness.can_auto_claim} value={snapshot.claim_readiness.can_auto_claim ? "Sẵn sàng" : "Đang khóa"} /></div></section><section className="event-log"><PanelTitle eyebrow="ENGINE EVENTS" title="Luồng sự kiện gần nhất" meta={`${snapshot.events.length} mục`} />{snapshot.events.map((event) => <div className="event-row" key={event.id}><time>{formatTime(event.created_at)}</time><span className={`event-state ${event.state}`}>{event.state}</span><p>{event.message || event.event_type}</p></div>)}{!snapshot.events.length && <EmptyState text="Worker chưa phát sự kiện nào." />}</section><details className="raw-health"><summary>Health payload</summary><pre>{JSON.stringify(health, null, 2)}</pre></details></div>;
}

function EmptyState({ text }: { text: string }) { return <div className="empty-state"><WalletCards size={24} /><p>{text}</p></div>; }
function formatTime(value: string) { return new Intl.DateTimeFormat("vi-VN", { timeZone: "Asia/Ho_Chi_Minh", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value)); }
