export type RecommendationPayload = {
  rule_code: string | null;
  rule_label: string;
  action_code: string;
  action_title: string;
  problem_title: string;
  problem_summary: string;
  what_to_do: string;
  where_to_do_it: string;
  action_window_days: number;
  check_after_window: string;
  success_signal: string;
  if_not_improved: string;
  do_not_do: string;
  expected_effect_note: string;
  estimated_gain_if_fixed: number | null;
  effect_type: 'profit_uplift' | 'revenue_at_risk' | 'loss_reduction' | 'working_capital_release' | null;
  effect_value: number | null;
  confidence: 'high' | 'medium' | 'low' | null;
  attribution_quality: 'exact_match' | 'fallback_match' | 'unattributed' | null;
};

export type StoreSummary = {
  run_date: string;
  period_start: string;
  period_end: string;
  marketplace: 'ozon' | 'wb';
  sku_count: number;
  units_sold_total: number;
  revenue_total: number;
  ads_spend_total: number;
  logistics_total: number;
  profit_before_ads_total: number;
  profit_after_ads_total: number;
  margin_after_ads_total: number | null;
  loss_sku_count: number;
  sku_with_ads_count: number;
  sku_with_stockout_risk_count: number;
  top_problem_rule_code: string | null;
  top_problem_family: 'ads' | 'inventory' | 'logistics' | 'margin' | 'general' | null;
  profit_growth_potential_30d: number;
  revenue_at_risk_30d: number;
  unattributed_cost_total: number;
  fallback_attributed_cost_total: number;
  unattributed_money_total: number;
  attribution_quality: 'exact_match' | 'fallback_match' | 'unattributed';
  confidence: 'high' | 'medium' | 'low';
  quality_disclaimer: string;
  total_units_sold: number;
  sold_sku_count: number;
  sku_with_stock_count: number;
  sku_without_sales_count: number;
  avg_selling_price: number | null;
  median_selling_price: number | null;
  avg_profit_per_sku: number | null;
  avg_margin_per_sku: number | null;
  median_margin_per_sku: number | null;
  expenses_coverage_ratio: number | null;
  ads_coverage_ratio: number | null;
  cost_input_coverage_ratio: number | null;
  fallback_cogs_ratio: number | null;
  profit_confidence: 'reliable' | 'estimated' | 'preliminary';
  profit_confidence_label: string;
  expenses_quality_label: string;
  cost_quality_label: string;
  store_health_status: 'losing_money' | 'at_risk' | 'healthy';
  store_health_message: string;
  snapshot_id: string | null;
  snapshot_publish_status: 'draft' | 'partial' | 'ready' | 'published' | 'blocked' | 'unknown';
  latest_snapshot_publish_status: 'draft' | 'partial' | 'ready' | 'published' | 'blocked' | null;
  snapshot_usable_for_ui: boolean;
  snapshot_is_last_good_fallback: boolean;
};

export type ActionRow = {
  run_date: string;
  period_start: string;
  period_end: string;
  marketplace: 'ozon' | 'wb';
  offer_id: string;
  product_name: string | null;
  action_code: string;
  action_type: string;
  primary_rule_code: string;
  rule_label: string;
  action_title: string;
  problem_title: string;
  problem_summary: string;
  what_to_do: string;
  where_to_do_it: string;
  first_step: string;
  check_after_window: string;
  success_signal: string;
  if_not_improved: string;
  do_not_do: string;
  expected_effect_note: string;
  action_window_days: number;
  attribution_quality: 'exact_match' | 'fallback_match' | 'unattributed';
  confidence: 'high' | 'medium' | 'low';
  estimated_gain_if_fixed: number | null;
  effect_type: 'profit_uplift' | 'revenue_at_risk' | 'loss_reduction' | 'working_capital_release' | null;
  effect_value: number | null;
  estimated_profit_uplift_30d: number | null;
  estimated_revenue_protection_30d: number | null;
  revenue_at_risk_30d: number | null;
  revenue_at_risk_per_day: number | null;
  revenue_at_risk_3d: number | null;
  revenue_at_risk_7d: number | null;
  revenue_at_risk_14d: number | null;
  days_of_stock_30d: number | null;
  expected_replenishment_date: string | null;
  planned_replenishment_qty: number | null;
  replenishment_status: string | null;
  stockout_effect_confidence: 'high' | 'medium' | 'low' | null;
  action_priority: number;
  action_priority_score: number;
  recommendation: RecommendationPayload;
};

export type ProductRow = {
  marketplace: 'ozon' | 'wb';
  offer_id: string;
  product_name: string | null;
  revenue_gross: number | null;
  profit_after_ads: number | null;
  ads_spend: number | null;
  logistics_amount: number | null;
  margin_after_ads: number | null;
  rule_code: string | null;
  primary_rule_code: string | null;
  action_code: string | null;
  action_type: string | null;
  rule_label: string;
  action_title: string;
  what_to_do: string;
  estimated_gain_if_fixed: number | null;
  recommendation: RecommendationPayload;
};

export type PlaybookRow = {
  run_date: string;
  period_start: string;
  period_end: string;
  marketplace: 'ozon' | 'wb';
  offer_id: string;
  product_name: string | null;
  primary_rule_code: string;
  action_code: string;
  rule_label: string;
  action_title: string;
  problem_title: string;
  playbook_title: string;
  problem_summary: string;
  what_to_do: string;
  where_to_do_it: string;
  first_step: string;
  action_intensity_hint: string;
  action_window_days: number;
  attribution_quality: 'exact_match' | 'fallback_match' | 'unattributed';
  confidence: 'high' | 'medium' | 'low';
  action_rationale: string;
  why_this_window: string;
  check_after_window: string;
  not_improved_criteria: string;
  diagnostic_priority_1: string;
  diagnostic_priority_2: string;
  diagnostic_priority_3: string;
  decision_path_summary: string;
  if_not_improved: string;
  do_not_do: string;
  expected_effect_note: string;
  success_signal: string;
  estimated_gain_if_fixed: number | null;
  effect_type: 'profit_uplift' | 'revenue_at_risk' | 'loss_reduction' | 'working_capital_release' | null;
  effect_value: number | null;
  recommendation: RecommendationPayload;
  days_of_stock_30d: number | null;
  revenue_at_risk_per_day: number | null;
  revenue_at_risk_7d: number | null;
};

export type DataQualityRow = {
  run_date: string;
  period_start: string;
  period_end: string;
  marketplace: 'ozon' | 'wb';
  negative_revenue_sku_count: number;
  negative_revenue_note: string;
  zero_revenue_with_sales_sku_count: number;
  zero_revenue_note: string;
  negative_logistics_sku_count: number;
  negative_logistics_note: string;
  stock_without_sales_sku_count: number;
  stock_without_sales_note: string;
  duplicate_posting_count: number;
  duplicate_posting_note: string;
  unattributed_row_count: number;
  unattributed_cost_total: number;
  unattributed_note: string;
  fallback_attributed_row_count: number;
  fallback_attributed_cost_total: number;
  fallback_attributed_note: string;
  notes: string;
};

export type ProductExtremeRow = {
  marketplace: 'ozon' | 'wb';
  offer_id: string;
  product_name: string | null;
  revenue_gross: number | null;
  profit_after_ads: number | null;
  margin_after_ads: number | null;
};

export type ProductExtremes = {
  top_profitable: ProductExtremeRow[];
  worst_by_profit: ProductExtremeRow[];
};

export type RuleMapRow = {
  marketplace: 'ozon' | 'wb';
  rule_code: string;
  hit_count: number;
};

export type StoreOverview = {
  period_start: string;
  period_end: string;
  marketplace: 'ozon' | 'wb';
  daily: Array<{
    date: string;
    revenue_total: number;
    profit_after_ads_total: number;
    ads_spend_total: number;
  }>;
  cost_structure: Array<{
    name: string;
    value: number;
  }>;
  financial_breakdown: Array<{
    name: string;
    value: number;
    kind: 'inflow' | 'expense' | 'result';
  }>;
  coverage: {
    expenses_coverage_ratio: number | null;
    ads_coverage_ratio: number | null;
    cost_input_coverage_ratio: number | null;
    fallback_cogs_ratio: number | null;
    unattributed_money_total: number;
    profit_confidence: 'reliable' | 'estimated' | 'preliminary';
    profit_confidence_label: string;
    expenses_quality_label: string;
    cost_quality_label: string;
  };
};

export type InternalDataHealth = {
  ingestion: Array<{
    source: string;
    run_id: string;
    status: string;
    raw_rows: number;
    normalized_rows: number;
    loaded_rows: number;
    retries: number;
    error_type: string | null;
  }>;
  completeness: Array<{
    source: string;
    expected_days: number;
    actual_days: number;
    missing_days: number;
    coverage_ratio: number;
    usable_for_publish: boolean;
    source_criticality: string;
    status: string;
    error_type: string | null;
    retry_exhausted: boolean;
    partial_reason: string | null;
    error_reason: string;
  }>;
  attribution: Array<{
    money_bucket: string;
    money_total: number;
    money_attributed: number;
    money_unattributed: number;
    attribution_by_method: Record<string, number>;
  }>;
  costs: {
    sku_manual_cogs: number;
    sku_category_fallback: number;
    sku_global_fallback: number;
    missing_cogs_coverage: number;
  };
  snapshots: Array<{
    snapshot_id: string;
    period_from: string;
    period_to: string;
    publish_status: string;
    published_at: string | null;
    is_last_good: boolean;
    blocking_sources: string[];
  }>;
};
