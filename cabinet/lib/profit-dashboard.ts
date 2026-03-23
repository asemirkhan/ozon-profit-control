import { readFile } from 'node:fs/promises';
import path from 'node:path';

export type SummaryMetric = {
  revenue_total: number;
  operacionnaya_pribyl_total: number;
  operacionnaya_margin_total: number | null;
  pribyl_posle_sebestoimosti_total: number;
  marzha_posle_sebestoimosti_total: number | null;
  ad_spend_total: number;
  ad_share_total: number | null;
  wb_cost_total: number;
  wb_cost_share_total: number | null;
  commission_total: number;
  commission_share_total: number | null;
  logistics_total: number;
  logistics_share_total: number | null;
  services_total: number;
  services_share_total: number | null;
  penalties_total: number;
  penalties_share_total: number | null;
  orders_total?: number;
  profit_per_order_total?: number | null;
  profitable_sku_count: number;
  attention_sku_count: number;
  loss_sku_count: number;
  strong_sku_count: number;
  loss_revenue_share: number | null;
  top5_profit_share: number | null;
  top5_risk_share: number | null;
};

export type DashboardTask = {
  nomer_prioriteta: number;
  zagolovok_ru: string;
  chto_ne_tak_ru: string;
  pochemu_vazhno_ru: string;
  shag_1_ru: string;
  shag_2_ru: string;
  shag_3_ru: string;
  skolko_sku_zatronuto: number;
  summa_riska: number;
  cta_href?: string;
};

export type RiskCard = {
  risk_key: string;
  risk_title: string;
  affected_sku_count: number;
  risk_amount: number;
  risk_amount_type: string;
  risk_definition: string;
  criticality: string;
  cta_label: string;
  cta_href: string;
  top_affected_skus: Array<{
    sku: string;
    nazvanie: string;
    summa_riska: number;
  }>;
};

export type ProductRow = {
  sku: string;
  nazvanie: string;
  klass_ru: string;
  status_ru: string;
  glavnaya_problema_ru: string;
  glavnaya_prichina_ru: string;
  viruchka: number;
  operacionnaya_pribyl: number;
  marzha_operacionnaya: number | null;
  reklama: number;
  wb_rashody: number;
  logistika: number;
  komissiya: number;
  platnye_uslugi: number;
  shtrafy_i_uderzhaniya: number;
  units: number;
  summa_riska: number;
  risk_family: string;
  risk_href: string;
  glavnaya_zona_davleniya_ru: string;
  glavnaya_zona_davleniya_summa: number;
  glavnaya_zona_davleniya_dolya: number | null;
  detail_href: string;
  main_block_code?: string;
  main_block_ru?: string;
  viruchka_na_edinicu?: number | null;
  pribyl_na_edinicu?: number | null;
  pribyl_posle_sebestoimosti?: number | null;
  marzha_posle_sebestoimosti?: number | null;
  reklama_na_edinicu?: number | null;
  wb_rashody_na_edinicu?: number | null;
  logistika_na_edinicu?: number | null;
  commission_amount?: number;
  logistics_amount?: number;
  storage_amount?: number;
  services_amount?: number;
  penalties_amount?: number;
  other_wb_amount?: number;
  other_wb_share?: number | null;
  storage_share?: number | null;
  commission_share?: number | null;
  logistics_share?: number | null;
  services_share?: number | null;
  penalties_share?: number | null;
  ad_share?: number | null;
  wb_cost_share?: number | null;
};

export type RecommendationCard = {
  sku: string;
  nazvanie: string;
  block_code?: string;
  status_ru: string;
  short_status_ru?: string;
  glavnyi_blok_ru?: string;
  fact_share?: number | null;
  norm_share?: number | null;
  top20_share?: number | null;
  deviation_share?: number | null;
  deviation_pp?: number | null;
  money_effect?: number;
  money_effect_type_code?: 'already_lost' | 'cut_costs' | 'increase_profit' | 'stop_loss';
  money_effect_type_ru?: string;
  output_type?: 'strong_action' | 'hypothesis_action' | 'signal_only';
  output_type_ru?: string;
  cause_status?: 'proven' | 'probable' | 'unknown';
  cause_status_ru?: string;
  cause_proven_flag?: boolean;
  action_available?: boolean;
  action_object_type?: string;
  action_object_id?: string | null;
  action_object_ru?: string;
  chto_proiskhodit_ru?: string;
  chto_eto_znachit_ru?: string;
  s_chem_sravnivaem_ru?: string;
  nasolko_otklonenie_ru?: string;
  skolko_deneg_stoit_ru?: string;
  iz_za_chego_ru?: string | null;
  shag_1_ru?: string | null;
  shag_2_ru?: string | null;
  shag_3_ru?: string | null;
  na_chto_smotret_ru?: string | null;
  kak_proverit_ru?: string | null;
  kak_ponyat_chto_luchshe_ru?: string | null;
  ozhidaemyi_effekt_ru?: string | null;
  chto_ne_delat_ru?: string | null;
};

export type DailyEconomicsRow = {
  day: string;
  viruchka: number;
  reklama: number;
  komissiya: number;
  logistika: number;
  platnye_uslugi: number;
  shtrafy_i_uderzhaniya: number;
  wb_rashody: number;
  operacionnaya_pribyl: number;
  marzha_operacionnaya: number | null;
  pribyl_posle_sebestoimosti: number;
  marzha_posle_sebestoimosti: number | null;
  units?: number;
  pribyl_na_edinicu?: number | null;
};

export type CostStructureRow = {
  category_code?: string;
  family_code?: string;
  family_ru?: string;
  client_safety_ru?: string;
  recommendation_allowed?: boolean;
  name?: string;
  category_ru?: string;
  amount?: number;
  value?: number;
  share_revenue?: number | null;
  share_wb_cost?: number | null;
  share_total_cost?: number | null;
  row_count?: number;
  sku_count?: number;
};

export type ProductAdsMetrics = {
  sku: string;
  nazvanie: string;
  detail_href?: string;
  impressions: number;
  clicks: number;
  orders_count: number;
  ad_revenue: number;
  ad_spend: number;
  ctr: number | null;
  cpc: number | null;
  cvr: number | null;
  drr: number | null;
  romi: number | null;
  operacionnaya_pribyl: number;
  pribyl_posle_reklamy_na_edinicu: number | null;
  status_ru: string;
  action_allowed: boolean;
  campaign_count: number;
  dominant_campaign_id?: string | null;
  dominant_campaign_spend?: number | null;
  dominant_campaign_orders?: number | null;
  revenue_total: number;
  ad_share_total: number | null;
};

export type ProductDetail = ProductRow & {
  pribyl_posle_sebestoimosti: number;
  marzha_posle_sebestoimosti: number | null;
  korrektirovki?: number;
  total_cost?: number;
  cost_categories?: Array<Record<string, unknown>>;
  cost_families?: Array<Record<string, unknown>>;
  daily: Array<{
    day: string;
    viruchka: number;
    reklama: number;
    komissiya: number;
    logistika: number;
    platnye_uslugi: number;
    shtrafy_i_uderzhaniya: number;
    wb_rashody: number;
    operacionnaya_pribyl: number;
    marzha_operacionnaya: number | null;
    units: number;
  }>;
  cost_structure: CostStructureRow[];
  recommendation?: RecommendationCard | null;
  ads_metrics?: ProductAdsMetrics | null;
};

export type ProfitDashboardSnapshot = {
  generated_at: string;
  schema: string;
  seller_id: string;
  period_start: string;
  period_end: string;
  summary: SummaryMetric;
  overview: {
    insights: string[];
    main_tasks: DashboardTask[];
    risk_cards: RiskCard[];
    growth_points?: ProductRow[];
    strong_skus?: ProductRow[];
    strong_products?: ProductRow[];
    attention_products?: RecommendationCard[];
    store_conclusion_ru?: string;
  };
  economics: {
    daily: DailyEconomicsRow[];
    cost_structure: CostStructureRow[];
    waterfall: Array<{
      name: string;
      value: number;
      kind: string;
    }>;
    quality_metrics: Array<{
      label: string;
      value: number | null;
      value_type: string;
    }>;
    scatter: Array<{
      sku: string;
      nazvanie: string;
      viruchka: number;
      marzha_operacionnaya: number | null;
      status_ru: string;
    }>;
    top_profit_products?: ProductRow[];
    top_overpay_products?: RecommendationCard[];
    top_logistics_products?: ProductRow[];
    top_ad_share_products?: ProductAdsMetrics[];
    top_profit_skus?: ProductRow[];
    top_risk_skus?: ProductRow[];
    top_logistics_skus?: ProductRow[];
    changes: string[];
    benchmarks?: Array<{
      metric_code: string;
      metric_ru: string;
      store_avg: number | null;
      top20_avg: number | null;
      top10_avg: number | null;
    }>;
  };
  risks: {
    registry: RiskCard[];
  };
  products: {
    rows: ProductRow[];
  };
  ads: {
    summary: {
      spend_total: number;
      spend_share_total: number | null;
      sku_with_ads_count: number;
      profitable_ads_count?: number;
      borderline_ads_count?: number;
      loss_ads_count?: number;
      unknown_ads_count?: number;
    };
    daily: Array<{
      day: string;
      reklama: number;
      viruchka: number;
      operacionnaya_pribyl: number;
    }>;
    top_spend: ProductAdsMetrics[];
    top_bad: ProductAdsMetrics[];
    top_good: ProductAdsMetrics[];
    rows?: ProductAdsMetrics[];
  };
  wb_costs: {
    summary: {
      wb_cost_total: number;
      wb_cost_share_total: number | null;
      commission_total: number;
      logistics_total: number;
      services_total: number;
      penalties_total: number;
    };
    daily: Array<{
      day: string;
      wb_rashody: number;
      komissiya: number;
      logistika: number;
      platnye_uslugi: number;
      shtrafy_i_uderzhaniya: number;
    }>;
    structure?: CostStructureRow[];
    breakdown?: CostStructureRow[];
    top_by_category?: Record<string, ProductRow[]>;
    top_wb_pressure?: ProductRow[];
    top_logistics_pressure?: ProductRow[];
    top_other_wb_pressure?: ProductRow[];
  };
  product_details: ProductDetail[];
  legacy: {
    main_tasks?: DashboardTask[];
    main_risks: string[];
    strong_skus: ProductRow[];
    attention_skus: RecommendationCard[];
  };
};

const SNAPSHOT_PATHS = [
  path.join(process.cwd(), 'data', 'wb_owner_dashboard_clean_ru_v4.json'),
  path.join(process.cwd(), 'data', 'wb_owner_dashboard_clean_ru_v1.json'),
];

export async function getProfitDashboardSnapshot(): Promise<ProfitDashboardSnapshot | null> {
  for (const snapshotPath of SNAPSHOT_PATHS) {
    try {
      const raw = await readFile(snapshotPath, 'utf-8');
      return JSON.parse(raw) as ProfitDashboardSnapshot;
    } catch {
      continue;
    }
  }
  return null;
}

export async function getProductDetailBySku(sku: string): Promise<ProductDetail | null> {
  const snapshot = await getProfitDashboardSnapshot();
  if (!snapshot) return null;
  return snapshot.product_details.find((item) => item.sku === sku) ?? null;
}
