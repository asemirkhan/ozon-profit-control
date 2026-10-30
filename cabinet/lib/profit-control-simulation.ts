import { existsSync } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { unstable_noStore as noStore } from 'next/cache';

type CsvRow = Record<string, string>;

export type SimulationHorizon = '28d' | '7d' | '1d';

export type StoreMetricCard = {
  key: 'revenue' | 'expenses' | 'result';
  label: string;
  currentValue: number;
  previousValue: number | null;
  deltaValue: number;
  significant: boolean | null;
};

export type StoreListRow = {
  normalizedProductKey: string;
  offerId: string;
  sku: string;
  productName: string;
  groupName: string;
  imageUrl: string | null;
  resultCurrent: number | null;
  resultPrevious: number | null;
  resultDelta: number;
  href: string;
};

export type StoreProblemBlock = {
  key: string;
  title: string;
  valueText: string;
  recommendation: string;
  href: string;
  products: Array<{
    normalizedProductKey: string;
    productName: string;
    valueText: string;
    href: string;
  }>;
};

export type SignalBlock = {
  key: string;
  title: string;
  valueText: string;
  href: string;
  products: Array<{
    normalizedProductKey: string;
    productName: string;
    valueText: string;
    href: string;
  }>;
};

export type ProductExpenseLine = {
  key: string;
  label: string;
  prev: number;
  curr: number;
  delta: number;
  expandable: boolean;
};

export type ProductDepthRow = {
  label: string;
  prev: string;
  curr: string;
};

export type ProductDepthSection = {
  key: 'ads' | 'logistics' | 'storage' | 'other';
  title: string;
  rows: ProductDepthRow[];
};

export type ProductDetailData = {
  normalizedProductKey: string;
  offerId: string;
  sku: string;
  productName: string;
  groupName: string;
  periodLabel: string;
  currentPeriodLabel: string;
  previousPeriodLabel: string;
  ordersPrev: number;
  ordersCurr: number;
  ordersDelta: number;
  cancelledOrdersPrev: number;
  cancelledOrdersCurr: number;
  fulfilledOrdersPrev: number;
  fulfilledOrdersCurr: number;
  salesUnitsPrev: number;
  salesUnitsCurr: number;
  salesUnitsDelta: number;
  conversionPrev: number | null;
  conversionCurr: number | null;
  conversionDelta: number | null;
  revenuePrev: number;
  revenueCurr: number;
  revenueDelta: number;
  expensesPrev: number;
  expensesCurr: number;
  expensesDelta: number;
  resultPrev: number;
  resultCurr: number;
  resultDelta: number;
  imageUrl: string | null;
  graphPackProductId: string | null;
  problemLabels: string[];
  recommendationLabels: string[];
  recommendationDetails: RecommendationV2 | null;
  expenseLines: ProductExpenseLine[];
  unitEconomicsLines: ProductExpenseLine[];
  depthSections: ProductDepthSection[];
  state: {
    ratingStart: string;
    ratingEnd: string;
    reviewCountStart: string;
    reviewCountEnd: string;
    avgPositionStart: string;
    avgPositionEnd: string;
  };
  signals: {
    resultDelta7d: number;
    revenueDelta7d: number;
    sharp1d: boolean;
  };
};

export type RecommendationV2 = {
  product_key: string;
  scenario_code: string;
  decision: 'no_action' | 'positive_status' | 'signal_only' | 'action' | 'urgent_action' | 'diagnostic_check' | 'data_warning' | 'blocked';
  priority_score: number;
  title: string;
  short_problem: string;
  full_problem: string;
  action: string | null;
  check_period: string | null;
  success_criterion: string | null;
  next_step: string | null;
  possible_explanations: string[];
  blocked_reasons: string[];
  confidence: string;
  problem_status: string;
  cause_status: string;
  cause: string;
  action_type: string;
  impact_amount: number;
  why_this_action: string | null;
  audit_checks: Array<{ code: string; label: string; passed: boolean; detail: string }>;
};

type RecommendationPackV2 = {
  generatedAt: string;
  recommendations: RecommendationV2[];
};

export type ProblemListRow = {
  normalizedProductKey: string;
  offerId: string;
  sku: string;
  productName: string;
  groupName: string;
  resultCurr: number;
  resultDelta: number;
  problemLabels: string[];
  strong7d: boolean;
  strong1d: boolean;
  href: string;
};

export type SimulationStorePageData = {
  confirmedThrough: string;
  current28dWindow: string;
  previous28dWindow: string;
  current7dWindow: string;
  previous7dWindow: string;
  current1dDate: string;
  baselineMethod1d: string;
  activeHorizon: SimulationHorizon;
  metricCards: StoreMetricCard[];
  topGrowth: StoreListRow[];
  topDecline: StoreListRow[];
  problemBlocks28d: StoreProblemBlock[];
  recommendationBlocks: StoreProblemBlock[];
  signalBlocks: SignalBlock[];
};

export type ProblemFilterKey =
  | 'all'
  | 'result_down'
  | 'sales_units_down'
  | 'orders_down'
  | 'avg_price_down'
  | 'cost_per_unit_up'
  | 'ads_spend_up'
  | 'ad_efficiency_down'
  | 'logistics_up'
  | 'storage_up'
  | 'stock_limited'
  | 'rapid_7d_drop'
  | 'sharp_1d_signal';

export type ProblemProductsPageData = {
  confirmedThrough: string;
  periodLabel: string;
  rows: ProblemListRow[];
  filterOptions: Array<{ key: ProblemFilterKey; label: string }>;
};

type ProductRecord = {
  normalizedProductKey: string;
  offerId: string;
  sku: string;
  productName: string;
  groupName: string;
  revenuePrev: number;
  revenueCurr: number;
  revenueDelta: number;
  expensesPrev: number;
  expensesCurr: number;
  expensesDelta: number;
  resultPrev: number;
  resultCurr: number;
  resultDelta: number;
  ordersPrev: number;
  ordersCurr: number;
  ordersDelta: number;
  fulfilledRatePrev: number | null;
  fulfilledRateCurr: number | null;
  cancelledOrdersPrev: number;
  cancelledOrdersCurr: number;
  salesUnitsPrev: number;
  salesUnitsCurr: number;
  salesUnitsDelta: number;
  avgRealizedPricePrev: number;
  avgRealizedPriceCurr: number;
  avgRealizedPriceDelta: number;
  adsSpendPrev: number;
  adsSpendCurr: number;
  adsSpendDelta: number;
  adAttributedOrdersPrev: number;
  adAttributedOrdersCurr: number;
  adAttributedRevenuePrev: number;
  adAttributedRevenueCurr: number;
  logisticsPrev: number;
  logisticsCurr: number;
  logisticsDelta: number;
  storagePrev: number;
  storageCurr: number;
  storageDelta: number;
  acquiringPrev: number;
  acquiringCurr: number;
  acquiringDelta: number;
  commissionPrev: number;
  commissionCurr: number;
  commissionDelta: number;
  otherPrev: number;
  otherCurr: number;
  otherDelta: number;
  stockDaysLimitedPrev: number;
  stockDaysLimitedCurr: number;
  flags: Record<string, string>;
  unitEconomics: {
    expensePerSoldUnitPrev: number | null;
    expensePerSoldUnitCurr: number | null;
    expensePerSoldUnitDelta: number | null;
    logisticsPerSoldUnitPrev: number | null;
    logisticsPerSoldUnitCurr: number | null;
    storagePerSoldUnitPrev: number | null;
    storagePerSoldUnitCurr: number | null;
    adsPerSoldUnitPrev: number | null;
    adsPerSoldUnitCurr: number | null;
    resultPerSoldUnitPrev: number | null;
    resultPerSoldUnitCurr: number | null;
  };
  signals: {
    resultDelta28d: number;
    revenueDelta28d: number;
    resultDelta7d: number;
    revenueDelta7d: number;
    resultBaselineDayValue: number;
    resultCurrentDayValue: number;
    resultDayDeviationPct: number;
    sharp1d: boolean;
  };
  state: {
    nmId: string;
    ratingStart: string;
    ratingEnd: string;
    reviewCountStart: string;
    reviewCountEnd: string;
    avgPositionStart: string;
    avgPositionEnd: string;
  };
  depthRows: Array<{
    expenseArea: string;
    subtypeName: string;
    prev: number;
    curr: number;
    delta: number;
    metricNote: string;
  }>;
};

type SimulationPack = {
  confirmedThrough: string;
  current28dWindow: string;
  previous28dWindow: string;
  current7dWindow: string;
  previous7dWindow: string;
  current1dDate: string;
  baselineMethod1d: string;
  storeHorizons: Record<
    '28d' | '7d',
    {
      revenuePrev: number;
      revenueCurr: number;
      expensesPrev: number;
      expensesCurr: number;
      resultPrev: number;
      resultCurr: number;
      adsPrev: number;
      adsCurr: number;
      commissionPrev: number;
      commissionCurr: number;
      logisticsPrev: number;
      logisticsCurr: number;
      storagePrev: number;
      storageCurr: number;
      acquiringPrev: number;
      acquiringCurr: number;
      returnsPrev: number;
      returnsCurr: number;
      otherPrev: number;
      otherCurr: number;
      storeOnlyPrev: number;
      storeOnlyCurr: number;
    }
  >;
  store1d: Record<
    'revenue_total' | 'expenses_total' | 'result_total',
    {
      baselineValue: number;
      currentValue: number;
      deviation: number;
      deviationPct: number;
      isSignificant: boolean;
    }
  >;
  products: ProductRecord[];
};

export type SimulationPackMeta = {
  generatedAt: string | null;
  lastModifiedAt: string;
  etag: string;
};

const APP_ROOT = process.cwd();
const ROOT = existsSync(path.join(APP_ROOT, 'artifacts')) ? APP_ROOT : path.resolve(APP_ROOT, '..');
const ARTIFACTS_DIR = path.join(ROOT, 'artifacts');
const SIM_DIR = path.join(ARTIFACTS_DIR, 'profit_control_recommendation_simulation_pack_v1');
const RECOMMENDATIONS_V2_PATH = path.join(ROOT, 'cabinet', 'data', 'profit_control_recommendations_v2.json');
const NORM_DIR = path.join(ARTIFACTS_DIR, 'profit_control_identity_normalization_stage_v1');
const PRODUCT_IMAGE_DIR = existsSync(path.join(APP_ROOT, 'public', 'wb-products'))
  ? path.join(APP_ROOT, 'public', 'wb-products')
  : path.join(ROOT, 'cabinet', 'public', 'wb-products');
const SIM_PACK_FILES = [
  'store_horizons.csv',
  'store_daily_signal_1d.csv',
  'all_products_28d_normalized.csv',
  'product_problem_flags.csv',
  'product_unit_economics.csv',
  'priority_product_expense_depth.csv',
  'product_state_metrics_history.csv',
  'product_signals_28d_7d_1d.csv',
].map((fileName) => path.join(SIM_DIR, fileName));

function toNumber(value: string | undefined): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

async function loadRecommendationsV2() {
  try {
    const payload = JSON.parse(await readFile(RECOMMENDATIONS_V2_PATH, 'utf8')) as RecommendationPackV2;
    return new Map(payload.recommendations.map((item) => [item.product_key, item]));
  } catch {
    return new Map<string, RecommendationV2>();
  }
}

function toNullableNumber(value: string | undefined): number | null {
  if (!value || value === 'unavailable') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function isTruthyFlag(value: string | undefined) {
  if (!value) return false;
  return value !== 'none' && value !== 'no' && value !== '0' && value !== 'unavailable';
}

function productWord(count: number) {
  const mod100 = count % 100;
  const mod10 = count % 10;
  if (mod100 >= 11 && mod100 <= 14) return 'товаров';
  if (mod10 === 1) return 'товар';
  if (mod10 >= 2 && mod10 <= 4) return 'товара';
  return 'товаров';
}

function productWordAfterOf(count: number) {
  return count === 1 ? 'товара' : 'товаров';
}

function displayProductName(name: string, sku: string) {
  if (!name || name === '~') {
    return sku || 'Товар';
  }
  return name;
}

function displayGroupName(name: string) {
  return !name || name === '~' ? 'Без группы' : name;
}

function slugForProductKey(normalizedProductKey: string) {
  return encodeURIComponent(normalizedProductKey);
}

function hrefForProduct(normalizedProductKey: string) {
  return `/products/${slugForProductKey(normalizedProductKey)}`;
}

function parseCsv(content: string): CsvRow[] {
  const rows: string[][] = [];
  let currentField = '';
  let currentRow: string[] = [];
  let insideQuotes = false;

  for (let i = 0; i < content.length; i += 1) {
    const char = content[i];
    const next = content[i + 1];

    if (char === '"') {
      if (insideQuotes && next === '"') {
        currentField += '"';
        i += 1;
      } else {
        insideQuotes = !insideQuotes;
      }
      continue;
    }

    if (char === ',' && !insideQuotes) {
      currentRow.push(currentField);
      currentField = '';
      continue;
    }

    if ((char === '\n' || char === '\r') && !insideQuotes) {
      if (char === '\r' && next === '\n') {
        i += 1;
      }
      currentRow.push(currentField);
      if (currentRow.some((item) => item !== '')) {
        rows.push(currentRow);
      }
      currentField = '';
      currentRow = [];
      continue;
    }

    currentField += char;
  }

  if (currentField !== '' || currentRow.length) {
    currentRow.push(currentField);
    rows.push(currentRow);
  }

  const [header, ...body] = rows;
  if (!header) return [];
  return body.map((row) =>
    header.reduce<CsvRow>((acc, key, index) => {
      acc[key] = row[index] ?? '';
      return acc;
    }, {}),
  );
}

async function readCsv(relativePath: string) {
  const filePath = path.isAbsolute(relativePath) ? relativePath : path.join(ROOT, relativePath);
  const raw = await readFile(filePath, 'utf-8');
  return parseCsv(raw);
}

function buildProblemLabelMap() {
  return {
    sales_units_down: 'снижение количества проданных товаров',
    orders_down: 'снижение заказов',
    avg_price_down: 'снижение средней цены продажи',
    cost_per_unit_up: 'рост затрат на единицу',
    ads_spend_up: 'рост рекламных расходов',
    ad_efficiency_down: 'снижение рекламной эффективности',
    logistics_up: 'рост логистики',
    storage_up: 'рост хранения',
    stock_limited: 'ограничение по наличию',
    rapid_7d_drop: 'сильный сигнал за 7 дней',
    sharp_1d_signal: 'сильный сигнал за 1 день',
    result_down: 'снижение результата',
  } as const;
}

const PROBLEM_LABELS = buildProblemLabelMap();

function visibleProducts(rows: ProductRecord[]) {
  return rows.filter((row) => row.productName !== '~' && row.offerId !== '~');
}

function topRowsBy<T>(rows: T[], selector: (row: T) => number, count: number, direction: 'asc' | 'desc') {
  const sorted = [...rows].sort((a, b) => selector(a) - selector(b));
  return direction === 'asc' ? sorted.slice(0, count) : sorted.reverse().slice(0, count);
}

function formatSignedCurrency(value: number) {
  return `${value > 0 ? '+' : value < 0 ? '−' : ''}${new Intl.NumberFormat('ru-RU', {
    style: 'currency',
    currency: 'RUB',
    maximumFractionDigits: 0,
  }).format(Math.abs(value))}`;
}

function formatSignedUnits(value: number) {
  return `${value > 0 ? '+' : value < 0 ? '−' : ''}${new Intl.NumberFormat('ru-RU', {
    maximumFractionDigits: 0,
  }).format(Math.abs(value))} шт`;
}

function formatRange(currentStart: string, currentEnd: string) {
  const display = (value: string) => {
    const [year, month, day] = value.split('-');
    return year && month && day ? `${day}.${month}.${year}` : value;
  };
  return `${display(currentStart)} — ${display(currentEnd)}`;
}

function splitMetricNote(note: string) {
  return note
    .split(';')
    .map((item) => item.trim())
    .filter(Boolean)
    .reduce<Record<string, string>>((acc, item) => {
      const [key, ...rest] = item.split('=');
      if (!key) return acc;
      acc[key.trim()] = rest.join('=').trim();
      return acc;
    }, {});
}

function numericText(value: string) {
  const normalized = value.replace(',', '.').trim();
  if (!normalized || normalized === '—' || normalized === 'unavailable' || normalized === 'no_main_wh_issue') {
    return null;
  }
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function hasUsefulDepthValue(value: string) {
  const parsed = numericText(value);
  if (parsed !== null) return Math.abs(parsed) > 0.0001;
  return value !== '—' && value !== 'unavailable' && value !== 'no_main_wh_issue';
}

function filterDepthRows(rows: ProductDepthRow[]) {
  return rows.filter((row) => hasUsefulDepthValue(row.prev) || hasUsefulDepthValue(row.curr));
}

function buildDepthSections(product: ProductRecord): ProductDepthSection[] {
  const groups = new Map<string, ProductRecord['depthRows']>();
  for (const row of product.depthRows) {
    const current = groups.get(row.expenseArea) ?? [];
    current.push(row);
    groups.set(row.expenseArea, current);
  }

  const adsRows = groups.get('ads') ?? [];
  const logisticsRows = groups.get('logistics') ?? [];
  const storageRows = groups.get('storage') ?? [];
  const otherRows = groups.get('other_product_expenses') ?? [];

  const firstAds = adsRows[0];
  const adsNote = firstAds ? splitMetricNote(firstAds.metricNote) : {};
  const firstStorage = storageRows[0];
  const storageNote = firstStorage ? splitMetricNote(firstStorage.metricNote) : {};

  const sections: ProductDepthSection[] = [];

  if (firstAds) {
    const rows = filterDepthRows([
      { label: 'Показы', prev: adsNote.views_prev ?? '0.00', curr: adsNote.views_curr ?? '0.00' },
      { label: 'Клики', prev: adsNote.clicks_prev ?? '0.00', curr: adsNote.clicks_curr ?? '0.00' },
      { label: 'Заказы по рекламной атрибуции', prev: adsNote.attributed_orders_prev ?? '0.00', curr: adsNote.attributed_orders_curr ?? '0.00' },
      { label: 'Выручка по рекламной атрибуции', prev: adsNote.attributed_revenue_prev ?? '0.00', curr: adsNote.attributed_revenue_curr ?? '0.00' },
      { label: 'Расход на заказ', prev: adsNote.cost_per_attributed_order_prev ?? '0.00', curr: adsNote.cost_per_attributed_order_curr ?? '0.00' },
      { label: 'Доля рекламных расходов', prev: adsNote.ad_share_of_revenue_prev ?? '0.00', curr: adsNote.ad_share_of_revenue_curr ?? '0.00' },
    ]);
    if (rows.length) {
      sections.push({
      key: 'ads',
      title: 'Реклама',
      rows,
    });
    }
  }

  if (logisticsRows.length) {
    const bySubtype = Object.fromEntries(logisticsRows.map((row) => [row.subtypeName, row]));
    const logisticsNote = splitMetricNote(bySubtype.logistics_core?.metricNote ?? '');
    const rows = filterDepthRows([
      {
        label: 'Логистика доставки',
        prev: (bySubtype.logistics_core?.prev ?? 0).toFixed(2),
        curr: (bySubtype.logistics_core?.curr ?? 0).toFixed(2),
      },
      {
        label: 'Возмещение перевозки / складских операций',
        prev: ((bySubtype.transport_warehouse_reimbursement ?? bySubtype['Возмещение издержек по перевозке/по складским операциям с товаром'])?.prev ?? 0).toFixed(2),
        curr: ((bySubtype.transport_warehouse_reimbursement ?? bySubtype['Возмещение издержек по перевозке/по складским операциям с товаром'])?.curr ?? 0).toFixed(2),
      },
      {
        label: 'ПВЗ-компоненты',
        prev: (bySubtype.pvz_issue_return_reimbursement?.prev ?? 0).toFixed(2),
        curr: (bySubtype.pvz_issue_return_reimbursement?.curr ?? 0).toFixed(2),
      },
      {
        label: 'Расход на проданную единицу',
        prev: logisticsNote.logistics_per_unit_prev ?? '0.00',
        curr: logisticsNote.logistics_per_unit_curr ?? '0.00',
      },
    ]);
    if (rows.length) {
      sections.push({
      key: 'logistics',
      title: 'Логистика',
      rows,
    });
    }
  }

  if (firstStorage) {
    const rows = filterDepthRows([
      { label: 'Хранение на единицу', prev: storageNote.storage_per_unit_prev ?? '0.00', curr: storageNote.storage_per_unit_curr ?? '0.00' },
      { label: 'Дни ограничений / медленного оборота', prev: '0', curr: storageNote.limited_stock_days_curr ?? '0' },
      { label: 'Разбивка по складам', prev: '—', curr: storageNote.warehouse_split ?? 'unavailable' },
    ]);
    if (rows.length) {
      sections.push({
      key: 'storage',
      title: 'Хранение',
      rows,
    });
    }
  }

  if (otherRows.length) {
    const rows = filterDepthRows(
      otherRows.map((row) => ({
        label: row.subtypeName,
        prev: row.prev.toFixed(2),
        curr: row.curr.toFixed(2),
      })),
    );
    if (rows.length) {
      sections.push({
      key: 'other',
      title: 'Прочие товарные расходы',
      rows,
    });
    }
  }

  return sections;
}

function buildProductProblemLabels(product: ProductRecord) {
  const orderedKeys: Array<keyof typeof PROBLEM_LABELS> = [
    'sales_units_down',
    'orders_down',
    'avg_price_down',
    'cost_per_unit_up',
    'ads_spend_up',
    'ad_efficiency_down',
    'logistics_up',
    'storage_up',
    'stock_limited',
    'rapid_7d_drop',
    'sharp_1d_signal',
    'result_down',
  ];

  return orderedKeys.filter((key) => isTruthyFlag(product.flags[key])).map((key) => PROBLEM_LABELS[key]);
}

function buildStoreProblems(products: ProductRecord[]): StoreProblemBlock[] {
  const visible = visibleProducts(products);
  const definitions = [
    {
      key: 'sales_units_down',
      title: 'снижение количества проданных товаров',
      valueBuilder: (rows: ProductRecord[]) => formatSignedUnits(rows.reduce((sum, row) => sum + row.salesUnitsDelta, 0)),
      productValueBuilder: (row: ProductRecord) => formatSignedUnits(row.salesUnitsDelta),
      score: (row: ProductRecord) => Math.abs(row.salesUnitsDelta),
      recommendation: 'проверить товары со снижением количества продаж',
    },
    {
      key: 'cost_per_unit_up',
      title: 'рост затрат на единицу',
      valueBuilder: (rows: ProductRecord[]) =>
        formatSignedCurrency(
          rows.reduce((sum, row) => {
            const delta = row.unitEconomics.expensePerSoldUnitDelta ?? 0;
            return sum + Math.max(delta, 0) * Math.max(row.salesUnitsCurr, 0);
          }, 0),
        ),
      productValueBuilder: (row: ProductRecord) =>
        formatSignedCurrency((Math.max(row.unitEconomics.expensePerSoldUnitDelta ?? 0, 0) || 0) * Math.max(row.salesUnitsCurr, 0)),
      score: (row: ProductRecord) => Math.max(row.unitEconomics.expensePerSoldUnitDelta ?? 0, 0) * Math.max(row.salesUnitsCurr, 0),
      recommendation: 'проверить товары с ростом затрат на единицу',
    },
    {
      key: 'ads_spend_up',
      title: 'рост рекламных расходов',
      valueBuilder: (rows: ProductRecord[]) => formatSignedCurrency(rows.reduce((sum, row) => sum + Math.max(row.adsSpendDelta, 0), 0)),
      productValueBuilder: (row: ProductRecord) => formatSignedCurrency(Math.max(row.adsSpendDelta, 0)),
      score: (row: ProductRecord) => Math.max(row.adsSpendDelta, 0),
      recommendation: 'сократить неэффективные рекламные расходы',
    },
    {
      key: 'logistics_up',
      title: 'рост логистики',
      valueBuilder: (rows: ProductRecord[]) => formatSignedCurrency(rows.reduce((sum, row) => sum + Math.max(row.logisticsDelta, 0), 0)),
      productValueBuilder: (row: ProductRecord) => formatSignedCurrency(Math.max(row.logisticsDelta, 0)),
      score: (row: ProductRecord) => Math.max(row.logisticsDelta, 0),
      recommendation: 'разобрать рост логистики по проблемным товарам',
    },
    {
      key: 'avg_price_down',
      title: 'снижение средней цены продажи',
      valueBuilder: (rows: ProductRecord[]) =>
        formatSignedCurrency(
          rows.reduce((sum, row) => sum + Math.min(row.avgRealizedPriceDelta, 0) * Math.max(row.salesUnitsCurr, 0), 0),
        ),
      productValueBuilder: (row: ProductRecord) =>
        formatSignedCurrency(Math.min(row.avgRealizedPriceDelta, 0) * Math.max(row.salesUnitsCurr, 0)),
      score: (row: ProductRecord) => Math.abs(Math.min(row.avgRealizedPriceDelta, 0) * Math.max(row.salesUnitsCurr, 0)),
      recommendation: 'проверить товары со снижением средней цены продажи',
    },
  ] as const;

  return definitions
    .map((definition) => {
      const rows = visible.filter((row) => isTruthyFlag(row.flags[definition.key]));
      if (!rows.length) return null;
      const ranked = [...rows].sort((a, b) => definition.score(b) - definition.score(a)).slice(0, 5);
      return {
        key: definition.key,
        title: `${definition.title} по ${rows.length} товарам`,
        valueText: definition.valueBuilder(rows),
        recommendation: definition.recommendation,
        href: `/products?filter=${definition.key}`,
        products: ranked.map((row) => ({
          normalizedProductKey: row.normalizedProductKey,
          productName: row.productName,
          valueText: definition.productValueBuilder(row),
          href: hrefForProduct(row.normalizedProductKey),
        })),
      } satisfies StoreProblemBlock;
    })
    .filter(Boolean)
    .slice(0, 3) as StoreProblemBlock[];
}

function buildSignalBlocks(products: ProductRecord[]): SignalBlock[] {
  const visible = visibleProducts(products);
  const blocks: SignalBlock[] = [];

  const revenue7d = visible.filter((row) => row.signals.revenueDelta7d < 0);
  if (revenue7d.length) {
    const total = revenue7d.reduce((sum, row) => sum + row.signals.revenueDelta7d, 0);
    blocks.push({
      key: 'revenue_7d',
      title: `значительное снижение выручки по ${revenue7d.length} товарам`,
      valueText: formatSignedCurrency(total),
      href: '/products?filter=rapid_7d_drop',
      products: revenue7d
        .sort((a, b) => a.signals.revenueDelta7d - b.signals.revenueDelta7d)
        .slice(0, 5)
        .map((row) => ({
          normalizedProductKey: row.normalizedProductKey,
          productName: row.productName,
          valueText: formatSignedCurrency(row.signals.revenueDelta7d),
          href: hrefForProduct(row.normalizedProductKey),
        })),
    });
  }

  const units7d = visible.filter((row) => isTruthyFlag(row.flags.rapid_7d_drop) && row.salesUnitsDelta < 0);
  if (units7d.length) {
    const total = units7d.reduce((sum, row) => sum + row.salesUnitsDelta, 0);
    blocks.push({
      key: 'sales_7d',
      title: `значительное снижение количества проданных товаров по ${units7d.length} товарам`,
      valueText: formatSignedUnits(total),
      href: '/products?filter=rapid_7d_drop',
      products: units7d
        .sort((a, b) => a.salesUnitsDelta - b.salesUnitsDelta)
        .slice(0, 5)
        .map((row) => ({
          normalizedProductKey: row.normalizedProductKey,
          productName: row.productName,
          valueText: formatSignedUnits(row.salesUnitsDelta),
          href: hrefForProduct(row.normalizedProductKey),
        })),
    });
  }

  const cost1d = visible.filter((row) => isTruthyFlag(row.flags.sharp_1d_signal) && (row.unitEconomics.expensePerSoldUnitDelta ?? 0) > 0);
  if (cost1d.length) {
    const total = cost1d.reduce((sum, row) => sum + Math.max(row.unitEconomics.expensePerSoldUnitDelta ?? 0, 0) * Math.max(row.salesUnitsCurr, 0), 0);
    blocks.push({
      key: 'cost_1d',
      title: `увеличение затрат на единицу по ${cost1d.length} товарам`,
      valueText: formatSignedCurrency(total),
      href: '/products?filter=sharp_1d_signal',
      products: cost1d
        .sort(
          (a, b) =>
            Math.max((b.unitEconomics.expensePerSoldUnitDelta ?? 0) * Math.max(b.salesUnitsCurr, 0), 0) -
            Math.max((a.unitEconomics.expensePerSoldUnitDelta ?? 0) * Math.max(a.salesUnitsCurr, 0), 0),
        )
        .slice(0, 5)
        .map((row) => ({
          normalizedProductKey: row.normalizedProductKey,
          productName: row.productName,
          valueText: formatSignedCurrency(Math.max(row.unitEconomics.expensePerSoldUnitDelta ?? 0, 0) * Math.max(row.salesUnitsCurr, 0)),
          href: hrefForProduct(row.normalizedProductKey),
        })),
    });
  }

  return blocks;
}

async function loadSimulationPack(): Promise<SimulationPack> {
  const [storeHorizonsRows, storeSignalRows, allProductsRows, flagRows, unitRows, depthRows, stateRows] = await Promise.all([
    readCsv(path.join('artifacts', 'profit_control_recommendation_simulation_pack_v1', 'store_horizons.csv')),
    readCsv(path.join('artifacts', 'profit_control_recommendation_simulation_pack_v1', 'store_daily_signal_1d.csv')),
    readCsv(path.join('artifacts', 'profit_control_recommendation_simulation_pack_v1', 'all_products_28d_normalized.csv')),
    readCsv(path.join('artifacts', 'profit_control_recommendation_simulation_pack_v1', 'product_problem_flags.csv')),
    readCsv(path.join('artifacts', 'profit_control_recommendation_simulation_pack_v1', 'product_unit_economics.csv')),
    readCsv(path.join('artifacts', 'profit_control_recommendation_simulation_pack_v1', 'priority_product_expense_depth.csv')),
    readCsv(path.join('artifacts', 'profit_control_recommendation_simulation_pack_v1', 'product_state_metrics_history.csv')),
  ]);
  const signalRows = await readCsv(path.join('artifacts', 'profit_control_recommendation_simulation_pack_v1', 'product_signals_28d_7d_1d.csv'));

  const horizonsMap = Object.fromEntries(
    storeHorizonsRows.map((row) => [
      row.horizon,
      {
        revenuePrev: toNumber(row.revenue_prev),
        revenueCurr: toNumber(row.revenue_curr),
        expensesPrev: toNumber(row.expenses_prev),
        expensesCurr: toNumber(row.expenses_curr),
        resultPrev: toNumber(row.result_prev),
        resultCurr: toNumber(row.result_curr),
        adsPrev: toNumber(row.ads_prev),
        adsCurr: toNumber(row.ads_curr),
        commissionPrev: toNumber(row.commission_prev),
        commissionCurr: toNumber(row.commission_curr),
        logisticsPrev: toNumber(row.logistics_prev),
        logisticsCurr: toNumber(row.logistics_curr),
        storagePrev: toNumber(row.storage_prev),
        storageCurr: toNumber(row.storage_curr),
        acquiringPrev: toNumber(row.acquiring_prev),
        acquiringCurr: toNumber(row.acquiring_curr),
        returnsPrev: toNumber(row.returns_prev),
        returnsCurr: toNumber(row.returns_curr),
        otherPrev: toNumber(row.other_product_expenses_prev),
        otherCurr: toNumber(row.other_product_expenses_curr),
        storeOnlyPrev: toNumber(row.store_only_expenses_prev),
        storeOnlyCurr: toNumber(row.store_only_expenses_curr),
      },
    ]),
  ) as SimulationPack['storeHorizons'];

  const signal1dMap = Object.fromEntries(
    storeSignalRows.map((row) => [
      row.metric_name,
      {
        baselineValue: toNumber(row.baseline_day_value),
        currentValue: toNumber(row.current_day_value),
        deviation: toNumber(row.deviation),
        deviationPct: toNumber(row.deviation_pct),
        isSignificant: row.is_significant_after_outlier_cleaning === 'yes',
      },
    ]),
  ) as SimulationPack['store1d'];

  const flagsByKey = new Map(flagRows.map((row) => [row.normalized_product_key, row]));
  const unitByKey = new Map(unitRows.map((row) => [row.normalized_product_key, row]));
  const stateByKey = new Map(stateRows.map((row) => [row.normalized_product_key, row]));
  const signalByKey = new Map(signalRows.map((row) => [row.normalized_product_key, row]));
  const depthByKey = depthRows.reduce<Map<string, ProductRecord['depthRows']>>((acc, row) => {
    const current = acc.get(row.normalized_product_key) ?? [];
    current.push({
      expenseArea: row.expense_area,
      subtypeName: row.subtype_name,
      prev: toNumber(row.prev),
      curr: toNumber(row.curr),
      delta: toNumber(row.delta),
      metricNote: row.metric_note,
    });
    acc.set(row.normalized_product_key, current);
    return acc;
  }, new Map());

  const products = allProductsRows.map<ProductRecord>((row) => {
    const flags = flagsByKey.get(row.normalized_product_key) ?? {};
    const unit = unitByKey.get(row.normalized_product_key) ?? {};
    const signals = signalByKey.get(row.normalized_product_key) ?? {};
    const state = stateByKey.get(row.normalized_product_key) ?? {};
    return {
      normalizedProductKey: row.normalized_product_key,
      offerId: row.offer_id,
      sku: row.sku,
      productName: displayProductName(row.product_name, row.sku),
      groupName: displayGroupName(row.group_name),
      revenuePrev: toNumber(row.revenue_prev),
      revenueCurr: toNumber(row.revenue_curr),
      revenueDelta: toNumber(row.revenue_delta),
      expensesPrev: toNumber(row.expenses_prev),
      expensesCurr: toNumber(row.expenses_curr),
      expensesDelta: toNumber(row.expenses_delta),
      resultPrev: toNumber(row.result_prev),
      resultCurr: toNumber(row.result_curr),
      resultDelta: toNumber(row.result_delta),
      ordersPrev: toNumber(row.orders_prev),
      ordersCurr: toNumber(row.orders_curr),
      ordersDelta: toNumber(row.orders_delta),
      fulfilledRatePrev: toNullableNumber(row.fulfilled_rate_prev),
      fulfilledRateCurr: toNullableNumber(row.fulfilled_rate_curr),
      cancelledOrdersPrev: toNumber(row.cancelled_orders_prev),
      cancelledOrdersCurr: toNumber(row.cancelled_orders_curr),
      salesUnitsPrev: toNumber(row.sales_units_prev),
      salesUnitsCurr: toNumber(row.sales_units_curr),
      salesUnitsDelta: toNumber(row.sales_units_delta),
      avgRealizedPricePrev: toNumber(row.avg_realized_price_prev),
      avgRealizedPriceCurr: toNumber(row.avg_realized_price_curr),
      avgRealizedPriceDelta: toNumber(row.avg_realized_price_delta),
      adsSpendPrev: toNumber(row.ads_spend_prev),
      adsSpendCurr: toNumber(row.ads_spend_curr),
      adsSpendDelta: toNumber(row.ads_spend_delta),
      adAttributedOrdersPrev: toNumber(row.ad_attributed_orders_prev),
      adAttributedOrdersCurr: toNumber(row.ad_attributed_orders_curr),
      adAttributedRevenuePrev: toNumber(row.ad_attributed_revenue_prev),
      adAttributedRevenueCurr: toNumber(row.ad_attributed_revenue_curr),
      logisticsPrev: toNumber(row.logistics_prev),
      logisticsCurr: toNumber(row.logistics_curr),
      logisticsDelta: toNumber(row.logistics_delta),
      storagePrev: toNumber(row.storage_prev),
      storageCurr: toNumber(row.storage_curr),
      storageDelta: toNumber(row.storage_delta),
      acquiringPrev: toNumber(row.acquiring_prev),
      acquiringCurr: toNumber(row.acquiring_curr),
      acquiringDelta: toNumber(row.acquiring_delta),
      commissionPrev: toNumber(row.commission_prev),
      commissionCurr: toNumber(row.commission_curr),
      commissionDelta: toNumber(row.commission_delta),
      otherPrev: toNumber(row.other_product_expenses_prev),
      otherCurr: toNumber(row.other_product_expenses_curr),
      otherDelta: toNumber(row.other_product_expenses_delta),
      stockDaysLimitedPrev: toNumber(row.stock_days_limited_prev),
      stockDaysLimitedCurr: toNumber(row.stock_days_limited_curr),
      flags,
      unitEconomics: {
        expensePerSoldUnitPrev: toNullableNumber(unit.expense_per_sold_unit_prev),
        expensePerSoldUnitCurr: toNullableNumber(unit.expense_per_sold_unit_curr),
        expensePerSoldUnitDelta: toNullableNumber(unit.expense_per_sold_unit_delta),
        logisticsPerSoldUnitPrev: toNullableNumber(unit.logistics_per_sold_unit_prev),
        logisticsPerSoldUnitCurr: toNullableNumber(unit.logistics_per_sold_unit_curr),
        storagePerSoldUnitPrev: toNullableNumber(unit.storage_per_sold_unit_prev),
        storagePerSoldUnitCurr: toNullableNumber(unit.storage_per_sold_unit_curr),
        adsPerSoldUnitPrev: toNullableNumber(unit.ads_per_sold_unit_prev),
        adsPerSoldUnitCurr: toNullableNumber(unit.ads_per_sold_unit_curr),
        resultPerSoldUnitPrev: toNullableNumber(unit.result_per_sold_unit_prev),
        resultPerSoldUnitCurr: toNullableNumber(unit.result_per_sold_unit_curr),
      },
      signals: {
        resultDelta28d: toNumber(signals.result_delta_28d),
        revenueDelta28d: toNumber(signals.revenue_delta_28d),
        resultDelta7d: toNumber(signals.result_delta_7d),
        revenueDelta7d: toNumber(signals.revenue_delta_7d),
        resultBaselineDayValue: toNumber(signals.result_baseline_day_value),
        resultCurrentDayValue: toNumber(signals.result_current_day_value),
        resultDayDeviationPct: toNumber(signals.result_day_deviation_pct),
        sharp1d: signals.sharp_1d_signal === 'yes',
      },
      state: {
        nmId: state.nm_id ?? '',
        ratingStart: state.rating_start || 'unavailable',
        ratingEnd: state.rating_end || 'unavailable',
        reviewCountStart: state.review_count_start || 'unavailable',
        reviewCountEnd: state.review_count_end || 'unavailable',
        avgPositionStart: state.avg_position_start || 'unavailable',
        avgPositionEnd: state.avg_position_end || 'unavailable',
      },
      depthRows: depthByKey.get(row.normalized_product_key) ?? [],
    };
  });

  const horizon28 = storeHorizonsRows.find((row) => row.horizon === '28d');
  const horizon7 = storeHorizonsRows.find((row) => row.horizon === '7d');
  const oneDayRevenue = storeSignalRows.find((row) => row.metric_name === 'revenue_total');

  return {
    confirmedThrough: horizon28?.confirmed_through ? formatRange(horizon28.confirmed_through, horizon28.confirmed_through).split(' — ')[0] : '',
    current28dWindow: horizon28 ? formatRange(horizon28.current_start, horizon28.current_end) : '',
    previous28dWindow: horizon28 ? formatRange(horizon28.previous_start, horizon28.previous_end) : '',
    current7dWindow: horizon7 ? formatRange(horizon7.current_start, horizon7.current_end) : '',
    previous7dWindow: horizon7 ? formatRange(horizon7.previous_start, horizon7.previous_end) : '',
    current1dDate: oneDayRevenue?.current_day ? formatRange(oneDayRevenue.current_day, oneDayRevenue.current_day).split(' — ')[0] : '',
    baselineMethod1d: 'Медиана предыдущих сопоставимых дней недели после удаления выбросов',
    storeHorizons: horizonsMap,
    store1d: signal1dMap,
    products,
  };
}

export async function getSimulationPackMeta(): Promise<SimulationPackMeta> {
  const stats = await Promise.all(SIM_PACK_FILES.map((filePath) => stat(filePath)));
  const lastModifiedMs = Math.max(...stats.map((fileStat) => fileStat.mtimeMs));

  return {
    generatedAt: null,
    lastModifiedAt: new Date(lastModifiedMs).toISOString(),
    etag: stats
      .map((fileStat, index) => `${path.basename(SIM_PACK_FILES[index])}:${fileStat.size}:${Math.trunc(fileStat.mtimeMs)}`)
      .join('|'),
  };
}

let simulationCache: { etag: string; pack: SimulationPack } | null = null;

async function getSimulationPack() {
  noStore();
  const meta = await getSimulationPackMeta();
  if (!simulationCache || simulationCache.etag !== meta.etag) {
    simulationCache = {
      etag: meta.etag,
      pack: await loadSimulationPack(),
    };
  }
  return simulationCache.pack;
}

export async function getSimulationStorePageData(activeHorizon: SimulationHorizon): Promise<SimulationStorePageData> {
  const [pack, recommendations] = await Promise.all([getSimulationPack(), loadRecommendationsV2()]);
  const visible = visibleProducts(pack.products);
  const topGrowth28 = topRowsBy(visible.filter((row) => row.resultDelta > 0), (row) => row.resultDelta, 5, 'desc');
  const topDecline28 = topRowsBy(visible.filter((row) => row.resultDelta < 0), (row) => row.resultDelta, 5, 'asc');
  const topGrowth7 = topRowsBy(
    visible.filter((row) => row.signals.resultDelta7d > 0),
    (row) => row.signals.resultDelta7d,
    5,
    'desc',
  );
  const topDecline7 = topRowsBy(
    visible.filter((row) => row.signals.resultDelta7d < 0),
    (row) => row.signals.resultDelta7d,
    5,
    'asc',
  );
  const oneDayCandidates = visible.filter((row) => row.signals.sharp1d);
  const topGrowth1 = topRowsBy(
    oneDayCandidates.filter(
      (row) => row.signals.resultCurrentDayValue - row.signals.resultBaselineDayValue > 0,
    ),
    (row) => row.signals.resultCurrentDayValue - row.signals.resultBaselineDayValue,
    5,
    'desc',
  );
  const topDecline1 = topRowsBy(
    oneDayCandidates.filter(
      (row) => row.signals.resultCurrentDayValue - row.signals.resultBaselineDayValue < 0,
    ),
    (row) => row.signals.resultCurrentDayValue - row.signals.resultBaselineDayValue,
    5,
    'asc',
  );

  const metricCards: StoreMetricCard[] =
    activeHorizon === '1d'
      ? [
          {
            key: 'revenue',
            label: 'Выручка',
            currentValue: pack.store1d.revenue_total.currentValue,
            previousValue: pack.store1d.revenue_total.baselineValue,
            deltaValue: pack.store1d.revenue_total.deviation,
            significant: pack.store1d.revenue_total.isSignificant,
          },
          {
            key: 'expenses',
            label: 'Расходы',
            currentValue: pack.store1d.expenses_total.currentValue,
            previousValue: pack.store1d.expenses_total.baselineValue,
            deltaValue: pack.store1d.expenses_total.deviation,
            significant: pack.store1d.expenses_total.isSignificant,
          },
          {
            key: 'result',
            label: 'Результат',
            currentValue: pack.store1d.result_total.currentValue,
            previousValue: pack.store1d.result_total.baselineValue,
            deltaValue: pack.store1d.result_total.deviation,
            significant: pack.store1d.result_total.isSignificant,
          },
        ]
      : [
          {
            key: 'revenue',
            label: 'Выручка',
            currentValue: pack.storeHorizons[activeHorizon].revenueCurr,
            previousValue: pack.storeHorizons[activeHorizon].revenuePrev,
            deltaValue: pack.storeHorizons[activeHorizon].revenueCurr - pack.storeHorizons[activeHorizon].revenuePrev,
            significant: null,
          },
          {
            key: 'expenses',
            label: 'Расходы',
            currentValue: pack.storeHorizons[activeHorizon].expensesCurr,
            previousValue: pack.storeHorizons[activeHorizon].expensesPrev,
            deltaValue: pack.storeHorizons[activeHorizon].expensesCurr - pack.storeHorizons[activeHorizon].expensesPrev,
            significant: null,
          },
          {
            key: 'result',
            label: 'Результат',
            currentValue: pack.storeHorizons[activeHorizon].resultCurr,
            previousValue: pack.storeHorizons[activeHorizon].resultPrev,
            deltaValue: pack.storeHorizons[activeHorizon].resultCurr - pack.storeHorizons[activeHorizon].resultPrev,
            significant: null,
          },
        ];

  const buildList = (rows: ProductRecord[], mode: SimulationHorizon): StoreListRow[] =>
    rows.map((row) => ({
      normalizedProductKey: row.normalizedProductKey,
      offerId: row.offerId,
      sku: row.sku,
      productName: row.productName,
      groupName: row.groupName,
      imageUrl: row.state.nmId && row.state.nmId !== 'unavailable' ? `/wb-products/${row.state.nmId}.webp` : null,
      resultCurrent:
        mode === '28d'
          ? row.resultCurr
          : mode === '7d'
            ? row.signals.resultDelta7d
            : row.signals.resultCurrentDayValue,
      resultPrevious:
        mode === '28d'
          ? row.resultPrev
          : mode === '7d'
            ? 0
            : row.signals.resultBaselineDayValue,
      resultDelta:
        mode === '28d'
          ? row.resultDelta
          : mode === '7d'
            ? row.signals.resultDelta7d
            : row.signals.resultCurrentDayValue - row.signals.resultBaselineDayValue,
      href: hrefForProduct(row.normalizedProductKey),
    }));

  const topGrowth =
    activeHorizon === '28d'
      ? buildList(topGrowth28, '28d')
      : activeHorizon === '7d'
        ? buildList(topGrowth7, '7d')
        : buildList(topGrowth1, '1d');
  const topDecline =
    activeHorizon === '28d'
      ? buildList(topDecline28, '28d')
      : activeHorizon === '7d'
        ? buildList(topDecline7, '7d')
        : buildList(topDecline1, '1d');

  const productByKey = new Map(pack.products.map((product) => [product.normalizedProductKey, product]));
  const problemCandidates: Array<StoreProblemBlock & { impact: number }> = [];
  const addProblem = (
    key: string,
    title: string,
    filter: ProblemFilterKey,
    products: ProductRecord[],
    moneyChange: number,
    moneyLabel: string,
  ) => {
    if (!products.length || moneyChange <= 0) return;
    const sorted = [...products].sort((a, b) => a.resultDelta - b.resultDelta);
    const resultLoss = Math.abs(sorted.reduce((sum, product) => sum + Math.min(product.resultDelta, 0), 0));
    problemCandidates.push({
      key,
      title: `${title} у ${sorted.length} ${productWordAfterOf(sorted.length)}`,
      valueText: `${moneyLabel}: ${formatSignedCurrency(moneyLabel === 'Потеря выручки' ? -moneyChange : moneyChange)}`,
      recommendation: `Совокупный результат этих товаров снизился на ${formatSignedCurrency(resultLoss).replace(/^[+−]/, '')}`,
      href: `/products?filter=${filter}`,
      impact: moneyChange,
      products: sorted.map((product) => ({
        normalizedProductKey: product.normalizedProductKey,
        productName: product.productName,
        valueText: formatSignedCurrency(product.resultDelta),
        href: hrefForProduct(product.normalizedProductKey),
      })),
    });
  };

  const salesLossProducts = visible.filter(
    (product) => isTruthyFlag(product.flags.sales_units_down) && product.revenueDelta < 0 && product.resultDelta < 0,
  );
  addProblem(
    'sales_revenue_loss',
    'Продажи и выручка снизились',
    'sales_units_down',
    salesLossProducts,
    Math.abs(salesLossProducts.reduce((sum, product) => sum + product.revenueDelta, 0)),
    'Потеря выручки',
  );

  const adsGrowthProducts = visible.filter((product) => product.adsSpendDelta > 0 && product.resultDelta < 0);
  addProblem(
    'ads_cost_growth',
    'Расходы на рекламу выросли',
    'ads_spend_up',
    adsGrowthProducts,
    adsGrowthProducts.reduce((sum, product) => sum + product.adsSpendDelta, 0),
    'Дополнительные расходы',
  );

  const logisticsGrowthProducts = visible.filter((product) => product.logisticsDelta > 0 && product.resultDelta < 0);
  addProblem(
    'logistics_cost_growth',
    'Расходы на логистику выросли',
    'logistics_up',
    logisticsGrowthProducts,
    logisticsGrowthProducts.reduce((sum, product) => sum + product.logisticsDelta, 0),
    'Дополнительные расходы',
  );

  const storageGrowthProducts = visible.filter((product) => product.storageDelta > 0 && product.resultDelta < 0);
  addProblem(
    'storage_cost_growth',
    'Расходы на хранение выросли',
    'storage_up',
    storageGrowthProducts,
    storageGrowthProducts.reduce((sum, product) => sum + product.storageDelta, 0),
    'Дополнительные расходы',
  );

  const problemBlocks28d = problemCandidates
    .sort((a, b) => b.impact - a.impact)
    .slice(0, 3)
    .map(({ impact: _impact, ...problem }) => problem);

  const recommendationBlocks = [...recommendations.values()]
    .filter((item) => item.decision === 'urgent_action' || item.decision === 'action' || item.decision === 'diagnostic_check')
    .filter((item) => item.audit_checks.every((check) => check.passed))
    .sort((a, b) => b.impact_amount - a.impact_amount || b.priority_score - a.priority_score)
    .slice(0, 3)
    .map((item) => {
      const product = productByKey.get(item.product_key);
      return {
        key: item.scenario_code,
        title: item.title,
        valueText: item.impact_amount > 0
          ? `Потеря результата: ${formatSignedCurrency(-item.impact_amount)}`
          : product
            ? `Изменение результата товара: ${formatSignedCurrency(product.resultDelta)}`
            : item.short_problem,
        recommendation: item.action || item.title,
        href: hrefForProduct(item.product_key),
        products: product
          ? [{
              normalizedProductKey: product.normalizedProductKey,
              productName: product.productName,
              valueText: formatSignedCurrency(product.resultDelta),
              href: hrefForProduct(product.normalizedProductKey),
            }]
          : [],
      } satisfies StoreProblemBlock;
    });

  return {
    confirmedThrough: pack.confirmedThrough,
    current28dWindow: pack.current28dWindow,
    previous28dWindow: pack.previous28dWindow,
    current7dWindow: pack.current7dWindow,
    previous7dWindow: pack.previous7dWindow,
    current1dDate: pack.current1dDate,
    baselineMethod1d: pack.baselineMethod1d,
    activeHorizon,
    metricCards,
    topGrowth,
    topDecline,
    problemBlocks28d,
    recommendationBlocks,
    signalBlocks: buildSignalBlocks(pack.products),
  };
}

export async function getSimulationProblemProductsPageData(filter: ProblemFilterKey = 'all'): Promise<ProblemProductsPageData> {
  const pack = await getSimulationPack();
  const rows = visibleProducts(pack.products)
    .map<ProblemListRow>((row) => ({
      normalizedProductKey: row.normalizedProductKey,
      offerId: row.offerId,
      sku: row.sku,
      productName: row.productName,
      groupName: row.groupName,
      resultCurr: row.resultCurr,
      resultDelta: row.resultDelta,
      problemLabels: buildProductProblemLabels(row).slice(0, 3),
      strong7d: isTruthyFlag(row.flags.rapid_7d_drop),
      strong1d: isTruthyFlag(row.flags.sharp_1d_signal),
      href: hrefForProduct(row.normalizedProductKey),
    }))
    .filter((row) => row.problemLabels.length > 0);

  const filtered =
    filter === 'all'
      ? rows
      : rows.filter((row) => {
          const product = pack.products.find((item) => item.normalizedProductKey === row.normalizedProductKey);
          if (!product) return false;
          if (filter === 'result_down') return product.resultDelta < 0;
          return isTruthyFlag(product.flags[filter]);
        });

  return {
    confirmedThrough: pack.confirmedThrough,
    periodLabel: pack.current28dWindow,
    rows: filtered.sort((a, b) => a.resultDelta - b.resultDelta),
    filterOptions: [
      { key: 'all', label: 'Все проблемы' },
      { key: 'result_down', label: 'Снижение результата' },
      { key: 'sales_units_down', label: 'Снижение количества проданных товаров' },
      { key: 'orders_down', label: 'Снижение заказов' },
      { key: 'avg_price_down', label: 'Снижение средней цены продажи' },
      { key: 'cost_per_unit_up', label: 'Рост затрат на единицу' },
      { key: 'ads_spend_up', label: 'Рост рекламных расходов' },
      { key: 'ad_efficiency_down', label: 'Снижение рекламной эффективности' },
      { key: 'logistics_up', label: 'Рост логистики' },
      { key: 'storage_up', label: 'Рост хранения' },
      { key: 'stock_limited', label: 'Ограничение по наличию' },
      { key: 'rapid_7d_drop', label: 'Сильный сигнал за 7 дней' },
      { key: 'sharp_1d_signal', label: 'Сильный сигнал за 1 день' },
    ],
  };
}

async function resolveProductImageUrl(product: ProductRecord) {
  const candidates = [product.state.nmId, product.sku, product.offerId]
    .filter(Boolean)
    .map((value) => path.join(PRODUCT_IMAGE_DIR, `${value}.webp`));
  for (const candidate of candidates) {
    try {
      await readFile(candidate);
      const name = path.basename(candidate);
      return `/wb-products/${name}`;
    } catch {
      // keep probing
    }
  }
  return null;
}

export async function getSimulationProductDetail(normalizedProductKey: string): Promise<ProductDetailData | null> {
  const [pack, recommendations] = await Promise.all([getSimulationPack(), loadRecommendationsV2()]);
  const decodedKey = decodeURIComponent(normalizedProductKey);
  const product = pack.products.find(
    (row) =>
      row.normalizedProductKey === decodedKey ||
      row.sku === decodedKey ||
      row.offerId === decodedKey,
  );
  if (!product || product.productName === '~' || product.offerId === '~') {
    return null;
  }

  const imageUrl = await resolveProductImageUrl(product);
  const graphPackProductId = product.state.nmId && product.state.nmId !== 'unavailable' ? product.state.nmId : null;
  const conversionPrev = product.fulfilledRatePrev;
  const conversionCurr = product.fulfilledRateCurr;

  return {
    normalizedProductKey: product.normalizedProductKey,
    offerId: product.offerId,
    sku: product.sku,
    productName: product.productName,
    groupName: product.groupName,
    periodLabel: `${pack.current28dWindow} против ${pack.previous28dWindow}`,
    currentPeriodLabel: pack.current28dWindow,
    previousPeriodLabel: pack.previous28dWindow,
    ordersPrev: product.ordersPrev,
    ordersCurr: product.ordersCurr,
    ordersDelta: product.ordersDelta,
    cancelledOrdersPrev: product.cancelledOrdersPrev,
    cancelledOrdersCurr: product.cancelledOrdersCurr,
    fulfilledOrdersPrev: Math.max(product.ordersPrev - product.cancelledOrdersPrev, 0),
    fulfilledOrdersCurr: Math.max(product.ordersCurr - product.cancelledOrdersCurr, 0),
    salesUnitsPrev: product.salesUnitsPrev,
    salesUnitsCurr: product.salesUnitsCurr,
    salesUnitsDelta: product.salesUnitsDelta,
    conversionPrev,
    conversionCurr,
    conversionDelta: conversionPrev !== null && conversionCurr !== null ? conversionCurr - conversionPrev : null,
    revenuePrev: product.revenuePrev,
    revenueCurr: product.revenueCurr,
    revenueDelta: product.revenueDelta,
    expensesPrev: product.expensesPrev,
    expensesCurr: product.expensesCurr,
    expensesDelta: product.expensesDelta,
    resultPrev: product.resultPrev,
    resultCurr: product.resultCurr,
    resultDelta: product.resultDelta,
    imageUrl,
    graphPackProductId,
    problemLabels: buildProductProblemLabels(product),
    recommendationLabels: recommendations.get(product.normalizedProductKey)?.action
      ? [recommendations.get(product.normalizedProductKey)!.action!]
      : [],
    recommendationDetails: recommendations.get(product.normalizedProductKey) ?? null,
    expenseLines: [
      { key: 'ads', label: 'Реклама', prev: product.adsSpendPrev, curr: product.adsSpendCurr, delta: product.adsSpendDelta, expandable: product.depthRows.some((row) => row.expenseArea === 'ads') },
      { key: 'commission', label: 'Комиссия', prev: product.commissionPrev, curr: product.commissionCurr, delta: product.commissionDelta, expandable: false },
      { key: 'logistics', label: 'Логистика', prev: product.logisticsPrev, curr: product.logisticsCurr, delta: product.logisticsDelta, expandable: product.depthRows.some((row) => row.expenseArea === 'logistics') },
      { key: 'storage', label: 'Хранение', prev: product.storagePrev, curr: product.storageCurr, delta: product.storageDelta, expandable: product.depthRows.some((row) => row.expenseArea === 'storage') },
      { key: 'acquiring', label: 'Эквайринг', prev: product.acquiringPrev, curr: product.acquiringCurr, delta: product.acquiringDelta, expandable: false },
      { key: 'other', label: 'Прочие товарные расходы', prev: product.otherPrev, curr: product.otherCurr, delta: product.otherDelta, expandable: product.depthRows.some((row) => row.expenseArea === 'other_product_expenses') },
      { key: 'expenses', label: 'Итого расходы', prev: product.expensesPrev, curr: product.expensesCurr, delta: product.expensesDelta, expandable: false },
      { key: 'revenue', label: 'Итого доход', prev: product.revenuePrev, curr: product.revenueCurr, delta: product.revenueDelta, expandable: false },
    ],
    unitEconomicsLines: [
      {
        key: 'expense_per_unit',
        label: 'Расходы на единицу',
        prev: product.unitEconomics.expensePerSoldUnitPrev ?? 0,
        curr: product.unitEconomics.expensePerSoldUnitCurr ?? 0,
        delta: product.unitEconomics.expensePerSoldUnitDelta ?? 0,
        expandable: false,
      },
      {
        key: 'logistics_per_unit',
        label: 'Логистика на единицу',
        prev: product.unitEconomics.logisticsPerSoldUnitPrev ?? 0,
        curr: product.unitEconomics.logisticsPerSoldUnitCurr ?? 0,
        delta: (product.unitEconomics.logisticsPerSoldUnitCurr ?? 0) - (product.unitEconomics.logisticsPerSoldUnitPrev ?? 0),
        expandable: false,
      },
      {
        key: 'storage_per_unit',
        label: 'Хранение на единицу',
        prev: product.unitEconomics.storagePerSoldUnitPrev ?? 0,
        curr: product.unitEconomics.storagePerSoldUnitCurr ?? 0,
        delta: (product.unitEconomics.storagePerSoldUnitCurr ?? 0) - (product.unitEconomics.storagePerSoldUnitPrev ?? 0),
        expandable: false,
      },
      {
        key: 'ads_per_unit',
        label: 'Реклама на единицу',
        prev: product.unitEconomics.adsPerSoldUnitPrev ?? 0,
        curr: product.unitEconomics.adsPerSoldUnitCurr ?? 0,
        delta: (product.unitEconomics.adsPerSoldUnitCurr ?? 0) - (product.unitEconomics.adsPerSoldUnitPrev ?? 0),
        expandable: false,
      },
      {
        key: 'result_per_unit',
        label: 'Результат на единицу',
        prev: product.unitEconomics.resultPerSoldUnitPrev ?? 0,
        curr: product.unitEconomics.resultPerSoldUnitCurr ?? 0,
        delta: (product.unitEconomics.resultPerSoldUnitCurr ?? 0) - (product.unitEconomics.resultPerSoldUnitPrev ?? 0),
        expandable: false,
      },
    ],
    depthSections: buildDepthSections(product),
    state: {
      ratingStart: product.state.ratingStart,
      ratingEnd: product.state.ratingEnd,
      reviewCountStart: product.state.reviewCountStart,
      reviewCountEnd: product.state.reviewCountEnd,
      avgPositionStart: product.state.avgPositionStart,
      avgPositionEnd: product.state.avgPositionEnd,
    },
    signals: {
      resultDelta7d: product.signals.resultDelta7d,
      revenueDelta7d: product.signals.revenueDelta7d,
      sharp1d: product.signals.sharp1d,
    },
  };
}
