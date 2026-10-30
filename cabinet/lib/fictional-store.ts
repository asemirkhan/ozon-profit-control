import { readFile } from 'node:fs/promises';
import path from 'node:path';

export type FictionalProduct = {
  product_id: string;
  vendor_code: string;
  name: string;
  group: string;
  scenario_code: string;
  quality_status: string;
  recommendation_status: string;
  prev_orders: number;
  curr_orders: number;
  prev_fulfilled_orders: number;
  curr_fulfilled_orders: number;
  prev_cancellations: number;
  curr_cancellations: number;
  prev_returns: number;
  curr_returns: number;
  prev_units: number;
  curr_units: number;
  prev_price: number;
  curr_price: number;
  prev_revenue: number;
  curr_revenue: number;
  prev_ads: number;
  curr_ads: number;
  prev_logistics: number;
  curr_logistics: number;
  prev_logistics_per_sale: number;
  curr_logistics_per_sale: number;
  prev_logistics_per_sold_unit: number;
  curr_logistics_per_sold_unit: number;
  prev_storage: number;
  curr_storage: number;
  prev_commission: number;
  curr_commission: number;
  prev_total_expenses: number;
  curr_total_expenses: number;
  prev_result: number;
  curr_result: number;
  prev_result_per_unit: number;
  curr_result_per_unit: number;
  result_delta: number;
  financial_impact: number;
  prev_views: number;
  curr_views: number;
  prev_clicks: number;
  curr_clicks: number;
  prev_card_visits: number;
  curr_card_visits: number;
  prev_order_conversion: number;
  curr_order_conversion: number;
  prev_attributed_orders: number;
  curr_attributed_orders: number;
  prev_rating: number;
  curr_rating: number;
  prev_search_rank: number;
  curr_search_rank: number;
  stock_days: number | null;
  prev_7d_orders: number;
  curr_7d_orders: number;
  baseline_day_orders: number;
  current_day_orders: number;
};

export type FictionalRecommendation = {
  scenario_code: string;
  product_id: string;
  product_name: string;
  status: string;
  short_problem: string;
  confirmed_facts: string;
  proven_cause: string;
  action: string;
  check_period_days: number;
  success_criterion: string;
  next_step: string;
  full_recommendation: string;
  quality_score: number;
};

export type FictionalEvidence = {
  scenario_code: string;
  product_id: string;
  evidence_source: string;
  dimension: string;
  previous_value: number | '';
  current_value: number | '';
  unit: string;
  contribution_rub: number;
  coverage_pct: number;
  causal_status: 'proven' | 'insufficient';
  evidence_text: string;
};

export type FictionalStorePack = {
  store: {
    store_name: string;
    fictional: true;
    niche: string;
    period_previous: { from: string; to: string };
    period_current: { from: string; to: string };
    last_full_operational_day: string;
    uses_cogs: false;
    product_count: number;
    previous_revenue: number;
    current_revenue: number;
    previous_result: number;
    current_result: number;
  };
  products: FictionalProduct[];
  recommendations: FictionalRecommendation[];
  evidence: FictionalEvidence[];
};

const PACK_PATH = path.resolve(process.cwd(), '..', 'research', 'fictional_store', 'cabinet_pack.json');

export async function getFictionalStorePack(): Promise<FictionalStorePack> {
  return JSON.parse(await readFile(PACK_PATH, 'utf8')) as FictionalStorePack;
}

export async function getFictionalProduct(productId: string) {
  const pack = await getFictionalStorePack();
  const product = pack.products.find((row) => row.product_id === productId);
  if (!product) return null;
  return {
    pack,
    product,
    recommendation: pack.recommendations.find((row) => row.product_id === productId) ?? null,
    evidence: pack.evidence.find((row) => row.product_id === productId) ?? null,
  };
}
