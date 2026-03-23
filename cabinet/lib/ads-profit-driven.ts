import { readFile } from 'node:fs/promises';
import path from 'node:path';

export type AdsProfitDrivenPeriod = {
  spend: number;
  orders: number;
  revenue: number;
  cost_per_order: number | null;
  revenue_per_order: number | null;
  profit_per_order_before_ads: number | null;
  profit_per_order_after_ads: number | null;
};

export type AdsProfitDrivenStatus = 'OK' | 'HYPOTHESIS' | 'ACTION' | 'NOISE';

export type AdsProfitDrivenProduct = {
  sku: string;
  product_name: string;
  status: AdsProfitDrivenStatus;
  reasons: string[];
  period_7: AdsProfitDrivenPeriod | null;
  period_30: AdsProfitDrivenPeriod | null;
  period_90: AdsProfitDrivenPeriod | null;
};

export type AdsProfitDrivenSnapshot = {
  date: string;
  schema: string;
  periods: {
    '7d': { available: boolean };
    '30d': { available: boolean };
    '90d': { available: boolean };
  };
  summary: {
    ok_count: number;
    hypothesis_count: number;
    action_count: number;
    noise_count: number;
    top1_spend_share: number | null;
    top3_spend_share: number | null;
    top3_signal_count: number;
  };
  products: AdsProfitDrivenProduct[];
  insights: string[];
};

const SNAPSHOT_PATH = path.join(process.cwd(), 'data', 'wb_ads_profit_driven_analysis_v1.json');

export async function getAdsProfitDrivenSnapshot(): Promise<AdsProfitDrivenSnapshot | null> {
  try {
    const raw = await readFile(SNAPSHOT_PATH, 'utf-8');
    return JSON.parse(raw) as AdsProfitDrivenSnapshot;
  } catch {
    return null;
  }
}

export async function getAdsProfitDrivenProduct(sku: string): Promise<AdsProfitDrivenProduct | null> {
  const snapshot = await getAdsProfitDrivenSnapshot();
  if (!snapshot) return null;
  return snapshot.products.find((item) => item.sku === sku) ?? null;
}

export function getVisibleAdsSignals(snapshot: AdsProfitDrivenSnapshot | null): AdsProfitDrivenProduct[] {
  if (!snapshot) return [];
  return snapshot.products.filter((item) => item.status === 'HYPOTHESIS' || item.status === 'ACTION');
}

export function getAdsSignalSummary(snapshot: AdsProfitDrivenSnapshot | null) {
  const signals = getVisibleAdsSignals(snapshot);
  const lead = signals[0] ?? null;
  return {
    actionCount: snapshot?.summary.action_count ?? 0,
    hypothesisCount: snapshot?.summary.hypothesis_count ?? 0,
    top1SpendShare: snapshot?.summary.top1_spend_share ?? null,
    top3SpendShare: snapshot?.summary.top3_spend_share ?? null,
    top3SignalCount: snapshot?.summary.top3_signal_count ?? 0,
    lead,
    hasSignals: signals.length > 0,
  };
}
