import { useEffect, useMemo, useRef, useState } from "react";
import { createChart, type IChartApi, type UTCTimestamp } from "lightweight-charts";
import { AlertTriangle, Check, CircleStop, LockKeyhole, Play, ShieldCheck, Square } from "lucide-react";
import { createRun, stopRun } from "./api";
import type { Candle, EngineEvent, MarketSnapshot, Mode, Snapshot } from "./types";

export function StatusDot({ active }: { active: boolean }) {
  return <span className={`status-dot ${active ? "is-active" : ""}`} aria-hidden="true" />;
}

export function WindowRail({ market, status }: { market: MarketSnapshot; status: string }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 250);
    return () => window.clearInterval(timer);
  }, []);
  const progress = Math.max(0, Math.min(1, (now - market.window_ts) / 300));
  const remaining = Math.max(0, market.close_ts - now);
  const minute = Math.floor(remaining / 60);
  const second = Math.floor(remaining % 60);
  const phase = remaining <= 5 ? "Hạn chót" : remaining <= 10 ? "Snipe" : remaining <= 40 ? "Thu tick" : "Quan sát";

  return (
    <section className="window-rail" aria-label="Cửa sổ giao dịch 5 phút">
      <div className="rail-heading">
        <div>
          <span className="eyebrow">BTC UP / DOWN · 5 PHÚT</span>
          <strong>{phase}</strong>
        </div>
        <div className="rail-clock">
          <span>T-{minute}:{second.toString().padStart(2, "0")}</span>
          <small>{status || "idle"}</small>
        </div>
      </div>
      <div className="rail-track">
        <div className="rail-progress" style={{ width: `${progress * 100}%` }} />
        <div className="rail-playhead" style={{ left: `${progress * 100}%` }} />
        <RailMark left={0} label="Mở" />
        <RailMark left={86.666} label="T-40" />
        <RailMark left={96.666} label="T-10" emphasis />
        <RailMark left={98.333} label="T-5" danger />
      </div>
      <div className="rail-readouts">
        <Readout label="BTC" value={market.btc_price ? `$${market.btc_price.toLocaleString("en-US", { maximumFractionDigits: 0 })}` : "—"} />
        <Readout label="Delta cửa sổ" value={market.delta_pct == null ? "—" : `${market.delta_pct >= 0 ? "+" : ""}${market.delta_pct.toFixed(4)}%`} tone={market.delta_pct == null ? "" : market.delta_pct >= 0 ? "up" : "down"} />
        <Readout label="UP" value={market.up_price == null ? "—" : `$${market.up_price.toFixed(2)}`} tone="up" />
        <Readout label="DOWN" value={market.down_price == null ? "—" : `$${market.down_price.toFixed(2)}`} tone="down" />
        <Readout label="Market" value={market.market_available ? "Sẵn sàng" : "Chưa có token"} />
      </div>
    </section>
  );
}

function RailMark({ left, label, emphasis, danger }: { left: number; label: string; emphasis?: boolean; danger?: boolean }) {
  return <span className={`rail-mark ${emphasis ? "emphasis" : ""} ${danger ? "danger" : ""}`} style={{ left: `${left}%` }}><i />{label}</span>;
}

function Readout({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return <div className={`readout ${tone}`}><small>{label}</small><span>{value}</span></div>;
}

export function MarketChart({ candles, windowOpen, theme }: { candles: Candle[]; windowOpen: number | null; theme: string }) {
  const container = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const dark = theme === "dark";
    const chart = createChart(container.current, {
      autoSize: true,
      layout: { background: { color: "transparent" }, textColor: dark ? "#b7c3be" : "#53615b", fontFamily: "IBM Plex Mono" },
      grid: { vertLines: { color: dark ? "#26312d" : "#dde4e0" }, horzLines: { color: dark ? "#26312d" : "#dde4e0" } },
      rightPriceScale: { borderColor: dark ? "#34413c" : "#c8d2cd" },
      timeScale: { borderColor: dark ? "#34413c" : "#c8d2cd", timeVisible: true, secondsVisible: false },
      crosshair: { vertLine: { color: "#315cff" }, horzLine: { color: "#315cff" } },
      height: 310
    });
    const series = chart.addCandlestickSeries({
      upColor: "#00866f", downColor: "#d64b5c", wickUpColor: "#00866f", wickDownColor: "#d64b5c", borderVisible: false
    });
    series.setData(candles.map((item) => ({ ...item, time: item.time as UTCTimestamp })));
    if (windowOpen) {
      series.createPriceLine({ price: windowOpen, color: "#315cff", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Window open" });
    }
    chart.timeScale().fitContent();
    chartRef.current = chart;
    return () => { chart.remove(); chartRef.current = null; };
  }, [candles, windowOpen, theme]);

  return <div ref={container} className="market-chart" aria-label="Biểu đồ nến BTC 1 phút" />;
}

const indicatorNames: Record<string, string> = {
  window_delta: "Window Delta",
  momentum: "Micro Momentum",
  acceleration: "Acceleration",
  ema: "EMA 9/21",
  rsi: "RSI 14",
  volume: "Volume Surge",
  tick_trend: "Tick Trend"
};

export function SignalMatrix({ events, fallback }: { events: EngineEvent[]; fallback?: Record<string, number> }) {
  const breakdown = useMemo(() => {
    const signal = events.find((event) => event.event_type === "signal");
    return (signal?.payload.breakdown as Record<string, number> | undefined) || fallback || {};
  }, [events, fallback]);
  return (
    <div className="signal-matrix">
      {Object.entries(indicatorNames).map(([key, name]) => {
        const value = breakdown[key] || 0;
        const width = Math.min(Math.abs(value) / 7 * 50, 50);
        return (
          <div className={`signal-row ${key === "window_delta" ? "dominant" : ""}`} key={key}>
            <span>{name}</span>
            <div className="signal-axis">
              <i className={value < 0 ? "negative" : "positive"} style={{ width: `${width}%`, left: value < 0 ? `${50 - width}%` : "50%" }} />
            </div>
            <b>{value > 0 ? "+" : ""}{value.toFixed(1)}</b>
          </div>
        );
      })}
    </div>
  );
}

export function RunControls({ snapshot, onChanged }: { snapshot: Snapshot; onChanged: () => void }) {
  const [mode, setMode] = useState<Mode>("safe");
  const [kind, setKind] = useState<"dry_run" | "live">("dry_run");
  const [budget, setBudget] = useState(20);
  const [minBet, setMinBet] = useState(1);
  const [once, setOnce] = useState(true);
  const [maxTrades, setMaxTrades] = useState("");
  const [liveOpen, setLiveOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [phrase, setPhrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = snapshot.active_run;
  const readiness = snapshot.trader_readiness;
  const liveFormReady = readiness.can_start_live
    && readiness.usdc_balance != null
    && readiness.usdc_balance >= minBet;
  const liveReason = !readiness.worker_online ? "Trader worker chưa có heartbeat mới"
    : !readiness.credentials_complete ? "Credentials trên trader-worker chưa đầy đủ"
      : !readiness.api_valid ? "CLOB API hoặc kiểm tra số dư chưa đạt"
        : !readiness.live_trading_enabled ? "LIVE_TRADING_ENABLED đang tắt ở trader-worker"
          : !readiness.web_live_trading_enabled ? "LIVE_TRADING_ENABLED đang tắt ở web"
            : readiness.usdc_balance == null || readiness.usdc_balance < minBet ? `Số dư thấp hơn min bet $${minBet.toFixed(2)}`
            : "Live sẵn sàng";

  async function start(confirmed = false) {
    if (kind === "live" && !confirmed) { setLiveOpen(true); return; }
    setBusy(true); setError("");
    try {
      await createRun({
        run_kind: kind, mode, session_budget: budget, min_bet: minBet, once,
        max_trades: maxTrades ? Number(maxTrades) : null,
        ...(kind === "live" ? { password, confirmation_text: phrase } : {})
      });
      setLiveOpen(false); setPassword(""); setPhrase(""); onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không thể bắt đầu phiên.");
    } finally { setBusy(false); }
  }

  async function stop(emergency: boolean) {
    if (!active) return;
    setBusy(true); setError("");
    try { await stopRun(active.id, emergency); onChanged(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Không thể dừng phiên."); }
    finally { setBusy(false); }
  }

  return (
    <section className="run-controls">
      <div className="section-title"><div><span className="eyebrow">ĐIỀU KHIỂN PHIÊN</span><h2>{active ? "Bot đang hoạt động" : "Sẵn sàng chạy"}</h2></div><ShieldCheck size={22} /></div>
      {active ? (
        <div className="active-run">
          <div className="active-run-main"><StatusDot active /><span>{active.run_kind === "live" ? "REAL" : "DRY-RUN"}</span><b>{active.mode}</b><small>{active.status}</small></div>
          <dl className="run-facts">
            <div><dt>Hạn mức</dt><dd>${active.session_budget.toFixed(2)}</dd></div>
            <div><dt>Min bet</dt><dd>${active.min_bet.toFixed(2)}</dd></div>
            <div><dt>Trades</dt><dd>{active.trades_count}{active.max_trades ? `/${active.max_trades}` : ""}</dd></div>
            <div><dt>Guide</dt><dd>LOCKED</dd></div>
          </dl>
          <div className="active-actions">
            <button className="button secondary" onClick={() => stop(false)} disabled={busy}><Square size={16} /> Dừng</button>
            <button className="button danger" onClick={() => stop(true)} disabled={busy}><CircleStop size={16} /> Dừng khẩn cấp</button>
          </div>
        </div>
      ) : (
        <div className="control-form">
          <div className="control-group"><span>Loại phiên</span><div className="segmented"><button className={kind === "dry_run" ? "selected" : ""} onClick={() => setKind("dry_run")}>Dry-run</button><button className={kind === "live" ? "selected live" : ""} onClick={() => setKind("live")} disabled={!liveFormReady} title={liveReason}><LockKeyhole size={14} /> Real</button></div></div>
          <div className="control-group"><span>Mode</span><div className="segmented three">{(["safe", "aggressive", "degen"] as Mode[]).map((item) => <button key={item} className={mode === item ? "selected" : ""} onClick={() => setMode(item)}>{item}</button>)}</div></div>
          <div className="field-pair"><label>Hạn mức phiên<input type="number" min="1" step="1" value={budget} onChange={(event) => setBudget(Number(event.target.value))} /></label><label>Min bet<input type="number" min="0.01" step="0.01" value={minBet} onChange={(event) => setMinBet(Number(event.target.value))} /></label></div>
          <div className="field-pair"><label className="check-label"><input type="checkbox" checked={once} onChange={(event) => setOnce(event.target.checked)} /> Chỉ một cửa sổ</label><label>Số trade tối đa<input type="number" min="1" placeholder="Không giới hạn" value={maxTrades} onChange={(event) => setMaxTrades(event.target.value)} disabled={once} /></label></div>
          <div className={`live-readiness ${liveFormReady ? "ready" : "locked"}`}><StatusDot active={liveFormReady} /><span>{liveReason}</span>{readiness.usdc_balance != null && <b>${readiness.usdc_balance.toFixed(2)} USDC</b>}</div>
          <button className={`button primary ${kind === "live" ? "live-button" : ""}`} onClick={() => start()} disabled={busy || budget < minBet || (kind === "live" && !liveFormReady)}><Play size={17} /> {kind === "live" ? "Mở khóa giao dịch thật" : "Bắt đầu dry-run"}</button>
        </div>
      )}
      {error && <p className="inline-error"><AlertTriangle size={15} />{error}</p>}
      {liveOpen && (
        <div className="modal-backdrop" role="presentation">
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="live-title">
            <LockKeyhole size={28} /><span className="eyebrow">HAI LỚP KHÓA</span><h2 id="live-title">Xác nhận giao dịch thật</h2>
            <p>Lệnh đã fill không thể hoàn tác. Railway env cũng phải bật <code>LIVE_TRADING_ENABLED</code>.</p>
            <label>Mật khẩu dashboard<input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoFocus /></label>
            <label>Nhập chính xác <code>GIAO DICH THAT</code><input value={phrase} onChange={(event) => setPhrase(event.target.value)} /></label>
            <div className="modal-actions"><button className="button secondary" onClick={() => setLiveOpen(false)}>Hủy</button><button className="button danger" disabled={busy || phrase !== "GIAO DICH THAT" || !password} onClick={() => start(true)}><Check size={16} /> Xác nhận live</button></div>
          </div>
        </div>
      )}
    </section>
  );
}
