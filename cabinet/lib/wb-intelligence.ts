import { readFile } from 'node:fs/promises';
import path from 'node:path';

export type WbIntelligenceProblem = {
  sku: string;
  nazvanie: string;
  problem_code_ru: string;
  problem_name_ru: string;
  kritichnost: string;
  summa_riska: number;
  glavnaya_prichina_ru: string;
  vtoraya_prichina_ru: string | null;
  blok_prichiny_ru: string;
  tip_deystviya_ru: string;
  pervoe_deystvie_ru: string;
  prioritet_chislo: number;
  prioritet_ru: string;
  osnovano_na_fakte_ili_ocenke: string;
  uroven_uverennosti: string;
  short_formulation_ru: string;
  medium_formulation_ru: string;
  long_formulation_ru: string;
  viruchka: number;
  reklama: number;
  operacionnaya_pribyl: number;
  marzha_operacionnaya: number | null;
  data_hygiene_flag: boolean;
};

export type WbIntelligenceAction = {
  sku: string;
  nazvanie: string;
  tip_deystviya_ru: string;
  pervoe_deystvie_ru: string;
  pochemu_eto_deystvie_ru: string;
  ozhidaemoe_napravlenie_effekta_ru: string;
  uroven_uverennosti: string;
  summa_riska: number;
};

export type WbIntelligenceSkuDecision = {
  sku: string;
  nazvanie: string;
  klass_sku_ru: string;
  klass_sku_kod: string;
  itogovy_status_ru: string;
  glavnaya_problema_ru: string;
  glavnaya_prichina_ru: string;
  vtoraya_prichina_ru: string | null;
  summa_riska: number;
  pervoe_deystvie_ru: string;
  chto_ne_delat_ru: string;
  osnovanie_rekomendacii_ru: string;
  uroven_uverennosti: string;
  osnovano_na_fakte_ili_ocenke: string;
  prioritet_chislo: number;
  prioritet_ru: string;
  korotkaya_kartochka_ru: string;
  srednyaya_kartochka_ru: string;
  dlinnaya_kartochka_ru: string;
};

export type WbStoreActionSummary = {
  nomer_prioriteta: number;
  blok_zadachi_ru: string;
  opisanie_ru: string;
  summa_riska: number;
  skolko_sku_zatronuto: number;
  pochemu_eto_vazhno_ru: string;
  pervyy_shag_ru: string;
  uroven_uverennosti: string;
};

export type WbDoNotTouchSku = {
  sku: string;
  nazvanie: string;
  klass_sku_ru: string;
  pochemu_silnyi_ru: string;
  chto_ne_delat_ru: string;
};

export type WbIntelligenceTopSku = {
  sku: string;
  nazvanie: string;
  viruchka: number;
  operacionnaya_pribyl: number;
  marzha_operacionnaya: number | null;
  comment_ru: string;
  chto_ne_trogat_ru: string;
};

export type WbIntelligenceDaily = {
  day: string;
  viruchka: number;
  reklama: number;
  operacionnaya_pribyl: number;
  marzha_operacionnaya: number | null;
  anomaliya_ru: string | null;
};

export type WbIntelligenceSummary = {
  seller_id: string;
  period_start: string;
  period_end: string;
  sku_total: number;
  product_sku_total: number;
  revenue_total: number;
  ad_spend_total: number;
  commission_total: number;
  logistics_total: number;
  services_total: number;
  penalties_total: number;
  operacionnaya_pribyl_total: number;
  marzha_operacionnaya_total: number | null;
  pribyl_posle_sebestoimosti_total: number;
  marzha_posle_sebestoimosti_total: number | null;
  critical_problem_sku_count: number;
  high_problem_sku_count: number;
  fact_problem_risk_total: number;
  data_hygiene_risk_total: number;
};

export type WbIntelligenceSnapshot = {
  generated_at: string;
  schema: string;
  seller_id: string;
  period_start: string;
  period_end: string;
  store_summary: WbIntelligenceSummary;
  sku_decisions: WbIntelligenceSkuDecision[];
  store_actions: WbStoreActionSummary[];
  top_do_not_touch: WbDoNotTouchSku[];
  top_problem_cards: WbIntelligenceProblem[];
  top_actions: WbIntelligenceAction[];
  top_profitable: WbIntelligenceTopSku[];
  data_hygiene: Array<{
    tip_problemy: string;
    opisanie: string;
    summa: number;
    vliyanie_na_ekonomiku: string;
    chto_s_etim_delat: string;
  }>;
  ready_phrases: string[];
  hidden_phrases: string[];
  daily_dynamics: WbIntelligenceDaily[];
  root_cause_summary: Array<{
    blok_prichiny_ru: string;
    issue_count: number;
    summa_riska: number;
  }>;
  segmentation: Array<{
    sku: string;
    nazvanie: string;
    segment_ru: string;
    operacionnaya_pribyl: number;
    marzha_operacionnaya: number | null;
  }>;
};

const SNAPSHOT_PATH = path.join(process.cwd(), 'data', 'wb_profit_intelligence_ru_v1.json');

export async function getWbIntelligenceSnapshot(): Promise<WbIntelligenceSnapshot | null> {
  try {
    const raw = await readFile(SNAPSHOT_PATH, 'utf-8');
    return JSON.parse(raw) as WbIntelligenceSnapshot;
  } catch {
    return null;
  }
}
