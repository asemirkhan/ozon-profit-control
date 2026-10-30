import { existsSync } from 'node:fs';
import { readFile, stat } from 'node:fs/promises';
import path from 'node:path';
import { unstable_noStore as noStore } from 'next/cache';

export type FinanceLayerLegend = {
  key: string;
  label: string;
  description: string;
};

export type FinanceBoundary = {
  confirmedThrough: string | null;
  note: string;
  freshTailDays: string[];
};

export type MoneyRange = Record<string, string>;

export type IncomePoint = {
  day: string;
  revenue: number;
  profitAfterAds: number | null;
  orders: number;
  expenses: number;
};

export type ExpensePoint = {
  day: string;
  total: number;
  ads: number;
  logistics: number;
  commission: number;
  acquiring: number;
  storage: number;
  withholding: number | null;
};

export type ExpenseBreakdownRow = {
  bucketGroup: string;
  bucketSubtype: string;
  label: string;
  amount: number;
  productLevelAmount: number;
  storeLevelAmount: number;
  mixedAmount: number;
  sourceLayer: string;
  attributionMethod: string;
  confidence: string;
  reason: string;
};

export type GeoRow = {
  name: string;
  revenueCurrentPerDay: number;
  revenueBasePerDay: number;
  revenueDeltaPerDay: number;
  revenueDeltaPct?: number;
  ordersCurrentPerDay: number;
  ordersBasePerDay: number;
  ordersDeltaPerDay: number;
  contribution: number;
  reliability: string;
  dimension?: string;
};

export type CompetitorRow = {
  nmId: string;
  name: string;
  price: number | null;
  rating: number | null;
  reviews: number | null;
  photoCount: number;
  hasVideo: boolean;
  descriptionFill: string;
  characteristicsCount: number;
  aboveUs: boolean;
  queries: string[];
  whyComparable: string;
  whyDangerous: string;
};

export type QueryPressureRow = {
  query: string;
  queryType: string;
  position: number | null;
  visibility: number | null;
  status: string;
  competitorsAbove: number;
  repeatedPressure: number;
  competitorList: string[];
};

export type ProductPack = {
  productId: string;
  productName: string;
  imageUrl: string | null;
  imageFallback: string;
  confidence: string;
  price: number | null;
  rating: number | null;
  feedbacks: number | null;
  confirmedCompetitorCount: number;
  higherSearchCompetitorCount: number;
  strongerCardCompetitorCount: number;
  repeatedPressureQueries: number;
  primaryProblemZone: string;
  secondaryProblemZone: string;
  whatHappened: string;
  whereProblem: string;
  whyMainProblem: string;
  provenFacts: string;
  stillHypothesis: string;
  whatToDoNow: string;
  whyThisAction: string;
  moneyInPlay30d: MoneyRange;
  moneyInPlayText: string;
  mainRisk: string;
  residualProfitAfterCalendarPerDay: number | null;
  ordersDeltaPerDay: number | null;
  revenueDeltaPerDay: number | null;
  profitAfterAdsDeltaPerDay: number | null;
  paidOrdersDeltaPerDay: number | null;
  nonPaidOrdersDeltaPerDay: number | null;
  daysOfStockOld: number | null;
  daysOfStockNew: number | null;
  stockExtensionDays: number | null;
  currentStockUnits: number | null;
  logisticsShare: number | null;
  storageShare: number | null;
  commissionShare: number | null;
  acquiringShare: number | null;
  withholdingShare: number | null;
  publicTitle: string;
  publicBrand: string;
  publicSubject: string;
  publicSupplier: string;
  machineSummary: string;
  finalSummary: Record<string, string>;
  competitors: CompetitorRow[];
  queryPressure: QueryPressureRow[];
  geoHotspots: GeoRow[];
  warehouseHotspots: GeoRow[];
  financeLabel: string;
  overview: {
    revenueTotal: number | null;
    orderCountTotal: number | null;
    averageCheckTotal: number | null;
    profitAfterAdsTotal: number | null;
    marginAfterAdsTotal: number | null;
    adSpendTotal: number | null;
    logisticsTotal: number | null;
    wbExpensesTotal: number | null;
    averageProfitPerOrder: number | null;
  };
  expenseBreakdown: ExpenseBreakdownRow[];
  windowMetrics: {
    currentUnitsPerDay: number | null;
    baseUnitsPerDay: number | null;
    currentOrdersPerDay: number | null;
    baseOrdersPerDay: number | null;
    currentRevenuePerDay: number | null;
    baseRevenuePerDay: number | null;
    currentProfitPerDay: number | null;
    baseProfitPerDay: number | null;
    currentAdSpendPerDay: number | null;
    baseAdSpendPerDay: number | null;
  };
  incomeRows: IncomePoint[];
  expenseRows: ExpensePoint[];
};

export type StoreWindowSummary = {
  metric: string;
  currentPerDay: number | null;
  basePerDay: number | null;
  deltaPerDay: number | null;
  deltaPct: number | null;
};

export type StorePack = {
  whatIsHappening: string;
  mainLosses: string;
  whatIsDangerousNow: string;
  scenarioGroupsNow: string;
  currentOrdersDragPerDay: number | null;
  currentRevenueDragPerDay: number | null;
  currentProfitDragPerDay: number | null;
  residualProfitDragAfterCalendarPerDay: number | null;
  paidOrdersDeltaPerDaySum: number | null;
  nonPaidOrdersDeltaPerDaySum: number | null;
  logisticsShareOfRevenuePool: number | null;
  storageShareOfRevenuePool: number | null;
  commissionShareOfRevenuePool: number | null;
  acquiringShareOfRevenuePool: number | null;
  scenarioMixNow: string;
  dailyCharts: Array<Record<string, number | string>>;
  incomeRows: IncomePoint[];
  expenseRows: ExpensePoint[];
  expenseBreakdown: ExpenseBreakdownRow[];
  regions: GeoRow[];
  warehouses: GeoRow[];
  windowSummary: StoreWindowSummary[];
  overview: {
    revenueTotal: number | null;
    operatingExpensesTotal: number | null;
    adSpendTotal: number | null;
    profitAfterAdsTotal: number | null;
    marginAfterAdsTotal: number | null;
    ordersTotal: number | null;
    averageCheck: number | null;
    averageProfitPerOrder: number | null;
  };
};

export type CabinetStructure = {
  economicsPage: string[];
  productsPage: string[];
  productPage: string[];
};

export type ProfitControlCabinetPack = {
  generatedAt: string;
  financeLayers: FinanceLayerLegend[];
  financeBoundary: FinanceBoundary;
  store: StorePack;
  products: ProductPack[];
  cabinetStructure: CabinetStructure;
};

export type ProfitControlCabinetPackMeta = {
  generatedAt: string | null;
  lastModifiedAt: string;
  etag: string;
};

function packFilePath() {
  const directPath = path.join(process.cwd(), 'data', 'profit_control_cabinet_human_pack_v1.json');
  const cabinetPath = path.join(process.cwd(), 'cabinet', 'data', 'profit_control_cabinet_human_pack_v1.json');
  return existsSync(directPath) ? directPath : cabinetPath;
}

async function readPackFile() {
  const filePath = packFilePath();
  const raw = await readFile(filePath, 'utf-8');
  return JSON.parse(raw) as ProfitControlCabinetPack;
}

export async function getProfitControlCabinetPackMeta(): Promise<ProfitControlCabinetPackMeta> {
  noStore();
  const filePath = packFilePath();
  const [raw, stats] = await Promise.all([readFile(filePath, 'utf-8'), stat(filePath)]);
  const parsed = JSON.parse(raw) as Partial<ProfitControlCabinetPack>;
  return {
    generatedAt: typeof parsed.generatedAt === 'string' ? parsed.generatedAt : null,
    lastModifiedAt: stats.mtime.toISOString(),
    etag: `${stats.size}-${Math.round(stats.mtimeMs)}`,
  };
}

export async function getProfitControlCabinetPack() {
  noStore();
  return readPackFile();
}

export async function getCabinetStorePack() {
  const pack = await getProfitControlCabinetPack();
  return pack.store;
}

export async function getCabinetProducts() {
  const pack = await getProfitControlCabinetPack();
  return pack.products;
}

export async function getCabinetProduct(productId: string) {
  const pack = await getProfitControlCabinetPack();
  return pack.products.find((item) => item.productId === productId) || null;
}
