import type { BacktestJob, BotRun, Candle, DashboardSettings, Snapshot, Trade } from "./types";

let csrfToken = "";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (options.method && options.method !== "GET" && csrfToken) headers.set("X-CSRF-Token", csrfToken);
  const response = await fetch(path, { ...options, headers, credentials: "include" });
  if (!response.ok) {
    let detail = `Lỗi ${response.status}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      detail = await response.text();
    }
    throw new Error(detail || `Lỗi ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function getMe() {
  const result = await request<{ authenticated: boolean; csrf: string }>("/api/auth/me");
  csrfToken = result.csrf;
  return result;
}

export async function login(password: string) {
  const result = await request<{ authenticated: boolean; csrf: string }>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ password })
  });
  csrfToken = result.csrf;
  return result;
}

export async function logout() {
  await request("/api/auth/logout", { method: "POST" });
  csrfToken = "";
}

export const getSnapshot = () => request<Snapshot>("/api/dashboard/snapshot");
export const getCandles = () => request<{ candles: Candle[] }>("/api/market/candles?limit=60");
export const getRuns = () => request<{ items: BotRun[] }>("/api/runs?limit=100");
export const getTrades = () => request<{ items: Trade[] }>("/api/trades?limit=300");
export const getSettings = () => request<DashboardSettings>("/api/settings");
export const getHealth = () => request<Record<string, unknown>>("/api/system/health");

export function createRun(payload: Record<string, unknown>) {
  return request<BotRun>("/api/runs", { method: "POST", body: JSON.stringify(payload) });
}

export function stopRun(id: string, emergency = false) {
  return request<BotRun>(`/api/runs/${id}/${emergency ? "emergency-stop" : "stop"}`, { method: "POST" });
}

export const getBacktests = () => request<{ items: BacktestJob[] }>("/api/backtests");
export const getBacktest = (id: string) => request<BacktestJob>(`/api/backtests/${id}`);
export function createBacktest(payload: Record<string, number>) {
  return request<BacktestJob>("/api/backtests", { method: "POST", body: JSON.stringify(payload) });
}

export function acknowledgeClaim(id: string) {
  return request<Trade>(`/api/trades/${id}/claim-acknowledge`, { method: "POST" });
}

export function queueClaim(id: string) {
  return request<Trade>(`/api/trades/${id}/claim`, { method: "POST" });
}
