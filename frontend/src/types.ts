export type RunKind = "dry_run" | "live";
export type Mode = "safe" | "aggressive" | "degen";

export interface BotRun {
  id: string;
  run_kind: RunKind;
  mode: Mode;
  guide_profile: string;
  status: string;
  session_budget: number;
  min_bet: number;
  once: boolean;
  max_trades: number | null;
  trades_count: number;
  wins_count: number;
  final_bankroll: number | null;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  heartbeat_at: string | null;
  stop_requested_at: string | null;
  emergency_stop: boolean;
}

export interface Trade {
  id: string;
  run_id: string;
  window_ts: number;
  slug: string;
  direction: "up" | "down";
  actual_outcome: "up" | "down" | null;
  won: boolean | null;
  score: number;
  confidence: number;
  breakdown: Record<string, number>;
  delta_pct: number;
  bet: number;
  entry_price: number;
  shares: number;
  spent: number;
  pnl: number;
  bankroll_after: number;
  order_kind: string;
  order_id: string | null;
  claim_required: boolean;
  claim_status: string;
  market_url: string;
  created_at: string;
}

export interface EngineEvent {
  id: number;
  run_id: string | null;
  event_type: string;
  state: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface MarketSnapshot {
  server_time: number;
  window_ts: number;
  close_ts: number;
  slug: string;
  btc_price: number | null;
  window_open: number | null;
  delta_pct: number | null;
  up_price: number | null;
  down_price: number | null;
  market_available: boolean;
}

export interface Worker {
  role: string;
  status: string;
  detail: Record<string, unknown>;
  last_seen: string;
}

export interface TraderReadiness {
  worker_online: boolean;
  worker_stale: boolean;
  worker_status: string;
  last_seen: string | null;
  credentials: Record<string, boolean>;
  credentials_complete: boolean;
  api_valid: boolean;
  balance_check_ok: boolean;
  usdc_balance: number | null;
  live_trading_enabled: boolean;
  web_live_trading_enabled: boolean;
  signature_type: number | null;
  preflight_at: string | null;
  can_start_live: boolean;
}

export interface Snapshot {
  guide_profile: string;
  active_run: BotRun | null;
  latest_run: BotRun | null;
  market: MarketSnapshot;
  trades: Trade[];
  events: EngineEvent[];
  workers: Worker[];
  trader_readiness: TraderReadiness;
  stats: { trades: number; pnl: number; wins: number };
}

export interface DashboardSettings {
  guide: Record<string, unknown>;
  live_trading_enabled: boolean;
  environment: Record<string, boolean>;
  trader_readiness: TraderReadiness;
  database: string;
  timezone: string;
  password_hash_configured: boolean;
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface BacktestJob {
  id: string;
  status: string;
  hours: number;
  starting_bankroll: number;
  min_bet: number;
  windows_count: number;
  error: string | null;
  created_at: string;
  completed_at: string | null;
  best: BacktestConfig | null;
  results?: BacktestResults;
}

export interface BacktestConfig {
  mode: string;
  threshold: number;
  trades: number;
  wins: number;
  win_rate: number;
  final_bankroll: number;
  roi: number;
  max_drawdown: number;
  curve?: number[];
}

export interface BacktestResults {
  configs: BacktestConfig[];
  best: BacktestConfig & { trade_log?: Record<string, unknown>[] };
  windows_count: number;
}
