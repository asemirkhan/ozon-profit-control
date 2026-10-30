import type { AdsProfitDrivenProduct, AdsProfitDrivenSnapshot } from '@/lib/ads-profit-driven';
import { formatMoney, formatPercent } from '@/lib/format';
import type { ProductDetail, ProfitDashboardSnapshot } from '@/lib/profit-dashboard';

type AttentionCategory = 'ads' | 'logistics' | 'commission';
type AttentionFocus = 'ads' | 'logistics' | 'commission';
type AttentionStatus = 'HYPOTHESIS' | 'ACTION';
type AttentionTone = 'ok' | 'warning' | 'critical';
type SectionTone = 'ok' | 'warning';

type TrendMetrics = {
  value7d: number;
  value30d: number;
  profit7d: number | null;
  profit30d: number | null;
  orders7d: number;
  orders30d: number;
  total7d: number;
  total30d: number;
};

export type ProductAttentionSignal = {
  sku: string;
  nazvanie: string;
  category: AttentionCategory;
  focus: AttentionFocus;
  reasonKey: string;
  reasonLabel: string;
  status: AttentionStatus;
  title: string;
  body: string;
  impactLine?: string;
  impact: number;
  href: string;
  value7d: number;
  value30d: number;
  profit7d: number | null;
  profit30d: number | null;
};

export type StoreAttentionItem = {
  key: string;
  title: string;
  detail?: string;
  countLabel?: string;
  href?: string;
  linkLabel?: string;
  linkMode?: 'aside' | 'inline';
};

export type StoreAttentionSection = {
  key: string;
  label: 'Реклама' | 'Логистика' | 'Комиссия';
  tone: SectionTone;
  items: StoreAttentionItem[];
};

export type StoreAttentionState = {
  hasAttention: boolean;
  tone: AttentionTone;
  statusLabel: string;
  sections: StoreAttentionSection[];
};

function sumBy<T>(rows: T[], selector: (row: T) => number | null | undefined): number {
  return rows.reduce((acc, row) => acc + (selector(row) ?? 0), 0);
}

function safeDiv(numerator: number, denominator: number): number | null {
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator <= 0) return null;
  return numerator / denominator;
}

function lastDays<T extends { day: string }>(rows: T[], days: number): T[] {
  return [...rows]
    .filter((row) => Boolean(row.day))
    .sort((a, b) => a.day.localeCompare(b.day))
    .slice(-days);
}

function formatAverageLines(value7d: number, value30d: number) {
  return `Среднее за 7 дней: ${formatMoney(value7d)}\nВ среднем за 30 дней: ${formatMoney(value30d)}`;
}

function buildProfitLine(profit7d: number | null, profit30d: number | null) {
  if (profit7d === null || profit30d === null) return undefined;
  return `Среднее за 7 дней: ${formatMoney(profit7d)} прибыли с заказа\nВ среднем за 30 дней: ${formatMoney(profit30d)} прибыли с заказа`;
}

function formatProductCount(count: number) {
  const mod10 = count % 10;
  const mod100 = count % 100;

  if (mod10 === 1 && mod100 !== 11) return `${count} товар`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return `${count} товара`;
  return `${count} товаров`;
}

function buildTrendMetrics(
  product: ProductDetail,
  valueGetter: (row: ProductDetail['daily'][number]) => number
): TrendMetrics | null {
  const rows30 = [...product.daily].filter((row) => row.units > 0);
  const rows7 = lastDays(rows30, 7);

  if (!rows30.length || !rows7.length) return null;

  const orders30d = sumBy(rows30, (row) => row.units);
  const orders7d = sumBy(rows7, (row) => row.units);
  if (orders30d <= 0 || orders7d < 5) return null;

  const total30d = sumBy(rows30, valueGetter);
  const total7d = sumBy(rows7, valueGetter);
  const profit30d = safeDiv(sumBy(rows30, (row) => row.operacionnaya_pribyl), orders30d);
  const profit7d = safeDiv(sumBy(rows7, (row) => row.operacionnaya_pribyl), orders7d);
  const value30d = safeDiv(total30d, orders30d);
  const value7d = safeDiv(total7d, orders7d);

  if (value30d === null || value7d === null) return null;

  return {
    value7d,
    value30d,
    profit7d,
    profit30d,
    orders7d,
    orders30d,
    total7d,
    total30d,
  };
}

function buildCostSignals(
  snapshot: ProfitDashboardSnapshot,
  category: AttentionCategory,
  metricName: string,
  valueGetter: (row: ProductDetail['daily'][number]) => number
): ProductAttentionSignal[] {
  const signals: ProductAttentionSignal[] = [];

  for (const product of snapshot.product_details) {
    const metrics = buildTrendMetrics(product, valueGetter);
    if (!metrics) continue;
    if (metrics.value7d <= metrics.value30d * 1.15) continue;

    signals.push({
      sku: product.sku,
      nazvanie: product.nazvanie,
      category,
      focus: category,
      reasonKey: category === 'logistics' ? 'logistics_growth' : 'commission_growth',
      reasonLabel: category === 'logistics' ? 'Рост логистики на заказ' : 'Рост комиссии на заказ',
      status: 'HYPOTHESIS',
      title: `За последние 7 дней ${metricName} выросла по сравнению со средним за 30 дней`,
      body: formatAverageLines(metrics.value7d, metrics.value30d),
      impactLine: buildProfitLine(metrics.profit7d, metrics.profit30d),
      impact: metrics.total30d,
      href: `/products/${product.sku}?focus=${category}&reason=${category === 'logistics' ? 'logistics_growth' : 'commission_growth'}`,
      value7d: metrics.value7d,
      value30d: metrics.value30d,
      profit7d: metrics.profit7d,
      profit30d: metrics.profit30d,
    });
  }

  return signals;
}

function buildAdsSignals(
  snapshot: ProfitDashboardSnapshot,
  adsSnapshot: AdsProfitDrivenSnapshot | null
): ProductAttentionSignal[] {
  if (!adsSnapshot || !Array.isArray(adsSnapshot.products)) return [];

  const productMap = new Map(snapshot.products.rows.map((row) => [row.sku, row]));
  const signals: ProductAttentionSignal[] = [];

  for (const item of adsSnapshot.products) {
    const product = productMap.get(item.sku);
    if (!product || !item.period_30) continue;
    if (item.status !== 'HYPOTHESIS' && item.status !== 'ACTION') continue;

    const value7d = item.period_7?.cost_per_order ?? item.period_30.cost_per_order ?? 0;
    const value30d = item.period_30.cost_per_order ?? 0;

    signals.push({
      sku: item.sku,
      nazvanie: product.nazvanie,
      category: 'ads',
      focus: 'ads',
      reasonKey: item.status === 'ACTION' ? 'not_profitable' : 'cpo_growth',
      reasonLabel: item.status === 'ACTION' ? 'Реклама не окупается' : 'Рост стоимости заказа',
      status: item.status,
      title:
        item.status === 'ACTION'
          ? 'За последние 7 дней прибыль с заказа после рекламы упала ниже нуля'
          : 'За последние 7 дней стоимость заказа выросла по сравнению со средним за 30 дней',
      body:
        item.period_7 && item.period_30.cost_per_order !== null && item.period_7.cost_per_order !== null
          ? formatAverageLines(item.period_7.cost_per_order, item.period_30.cost_per_order)
          : `Среднее за 7 дней: ${formatMoney(value7d)}\nВ среднем за 30 дней: ${formatMoney(value30d)}`,
      impactLine: buildProfitLine(
        item.period_7?.profit_per_order_after_ads ?? null,
        item.period_30.profit_per_order_after_ads
      ),
      impact: item.period_30.spend,
      href: `/products/${item.sku}?focus=ads&reason=${item.status === 'ACTION' ? 'not_profitable' : 'cpo_growth'}`,
      value7d,
      value30d,
      profit7d: item.period_7?.profit_per_order_after_ads ?? null,
      profit30d: item.period_30.profit_per_order_after_ads ?? null,
    });
  }

  if ((adsSnapshot.summary.top3_spend_share ?? 0) >= 70) {
    const concentrationProducts = [...adsSnapshot.products]
      .filter((item) => (item.period_30?.spend ?? 0) >= 100 && (item.period_30?.orders ?? 0) >= 5)
      .sort((a, b) => (b.period_30?.spend ?? 0) - (a.period_30?.spend ?? 0))
      .slice(0, 3);

    for (const item of concentrationProducts) {
      const product = productMap.get(item.sku);
      if (!product || !item.period_30) continue;
      const value7d = item.period_7?.cost_per_order ?? item.period_30.cost_per_order ?? 0;
      const value30d = item.period_30.cost_per_order ?? 0;

      signals.push({
        sku: item.sku,
        nazvanie: product.nazvanie,
        category: 'ads',
        focus: 'ads',
        reasonKey: 'budget_concentration',
        reasonLabel: 'Концентрация рекламного бюджета',
        status: 'HYPOTHESIS',
        title: 'Основной рекламный бюджет сосредоточен в нескольких товарах',
        body: `На товар приходится ${formatMoney(item.period_30.spend)} рекламных расходов за 30 дней`,
        impactLine:
          adsSnapshot.summary.top3_spend_share !== null && adsSnapshot.summary.top3_spend_share !== undefined
            ? `На три товара приходится ${formatPercent((adsSnapshot.summary.top3_spend_share ?? 0) / 100)} рекламного бюджета`
            : undefined,
        impact: item.period_30.spend,
        href: `/products/${item.sku}?focus=ads&reason=budget_concentration`,
        value7d,
        value30d,
        profit7d: item.period_7?.profit_per_order_after_ads ?? null,
        profit30d: item.period_30.profit_per_order_after_ads ?? null,
      });
    }
  }

  return signals;
}

function metricAvailability(
  snapshot: ProfitDashboardSnapshot,
  valueGetter: (row: ProductDetail['daily'][number]) => number
) {
  return snapshot.product_details.reduce((count, product) => count + (buildTrendMetrics(product, valueGetter) ? 1 : 0), 0);
}

export function buildProductAttentionSignals(
  snapshot: ProfitDashboardSnapshot,
  adsSnapshot: AdsProfitDrivenSnapshot | null
): ProductAttentionSignal[] {
  const signals = [
    ...buildAdsSignals(snapshot, adsSnapshot),
    ...buildCostSignals(snapshot, 'logistics', 'логистика на заказ', (row) => row.logistika),
    ...buildCostSignals(snapshot, 'commission', 'комиссия на заказ', (row) => row.komissiya),
  ];

  return signals.sort((a, b) => b.impact - a.impact);
}

export function buildStoreAttentionState(
  snapshot: ProfitDashboardSnapshot,
  adsSnapshot: AdsProfitDrivenSnapshot | null
): StoreAttentionState {
  const productSignals = buildProductAttentionSignals(snapshot, adsSnapshot);
  const adsSignals = productSignals.filter((item) => item.category === 'ads' && item.reasonKey === 'cpo_growth');
  const adsActionSignals = productSignals.filter((item) => item.category === 'ads' && item.status === 'ACTION');
  const logisticsSignals = productSignals.filter((item) => item.category === 'logistics');
  const commissionSignals = productSignals.filter((item) => item.category === 'commission');
  const commissionDataCoverage = metricAvailability(snapshot, (row) => row.komissiya);
  const adsSnapshotSummary = adsSnapshot?.summary ?? null;

  const sections: StoreAttentionSection[] = [];

  const adsItems: StoreAttentionItem[] = [];
  if (adsSignals.length) {
    adsItems.push({
      key: 'ads-cpo-growth',
      title: 'Есть товары, где выросла стоимость заказа за последние 7 дней',
      countLabel: formatProductCount(adsSignals.length),
      href: '/products?mode=attention&type=ads&reason=cpo_growth',
      linkLabel: 'посмотреть товары',
    });
  }
  if ((adsSnapshotSummary?.top3_spend_share ?? 0) >= 70) {
    adsItems.push({
      key: 'ads-budget-concentration',
      title: 'Основной рекламный бюджет сосредоточен в нескольких товарах',
      detail:
        adsSnapshotSummary?.top3_spend_share !== null && adsSnapshotSummary?.top3_spend_share !== undefined
          ? `Топ-3 товара дают ${Math.round(adsSnapshotSummary.top3_spend_share)}% рекламных расходов`
          : undefined,
      href: '/products?mode=attention&type=ads&reason=budget_concentration',
      linkLabel: 'посмотреть товары',
      linkMode: 'inline',
    });
  }
  if (!adsItems.length) {
    adsItems.push({
      key: 'ads-ok',
      title: 'Критичных отклонений по рекламе не выявлено',
    });
  }
  sections.push({
    key: 'ads',
    label: 'Реклама',
    tone: adsItems.some((item) => item.href) ? 'warning' : 'ok',
    items: adsItems,
  });

  sections.push({
    key: 'logistics',
    label: 'Логистика',
    tone: logisticsSignals.length ? 'warning' : 'ok',
    items: logisticsSignals.length
      ? [
          {
            key: 'logistics-growth',
            title: 'Есть товары, где выросла логистика на заказ за последние 7 дней',
            countLabel: formatProductCount(logisticsSignals.length),
            href: '/products?mode=attention&type=logistics&reason=logistics_growth',
            linkLabel: 'посмотреть товары',
          },
        ]
      : [
          {
            key: 'logistics-ok',
            title: 'Критичных отклонений по логистике не выявлено',
          },
        ],
  });

  if (commissionDataCoverage >= 3) {
    sections.push({
      key: 'commission',
      label: 'Комиссия',
      tone: commissionSignals.length ? 'warning' : 'ok',
      items: commissionSignals.length
        ? [
            {
              key: 'commission-growth',
              title: 'Есть товары, где выросла комиссия на заказ за последние 7 дней',
              countLabel: formatProductCount(commissionSignals.length),
              href: '/products?mode=attention&type=commission&reason=commission_growth',
              linkLabel: 'посмотреть товары',
            },
          ]
        : [
            {
              key: 'commission-ok',
              title: 'Критичных отклонений по комиссии не выявлено',
            },
          ],
    });
  }

  const attentionItemsCount = sections.reduce(
    (count, section) => count + section.items.filter((item) => item.href || item.title.includes('Есть товары')).length,
    0
  );
  const tone: AttentionTone =
    adsActionSignals.length > 0 || attentionItemsCount >= 3 ? 'critical' : attentionItemsCount > 0 ? 'warning' : 'ok';

  return {
    hasAttention: tone !== 'ok',
    tone,
    statusLabel: tone === 'ok' ? 'В норме' : tone === 'critical' ? 'Требует внимания' : 'Есть зоны внимания',
    sections,
  };
}

export function getSignalsForSku(
  snapshot: ProfitDashboardSnapshot,
  adsSnapshot: AdsProfitDrivenSnapshot | null,
  sku: string
): ProductAttentionSignal[] {
  return buildProductAttentionSignals(snapshot, adsSnapshot).filter((item) => item.sku === sku);
}
