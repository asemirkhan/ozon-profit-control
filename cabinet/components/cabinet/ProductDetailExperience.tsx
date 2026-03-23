'use client';

import { useMemo, useState } from 'react';

import { AttentionStatePanel, type AttentionStateSection } from '@/components/cabinet/AttentionStatePanel';
import { ProductAdsProfitChart } from '@/components/cabinet/ProductAdsProfitChart';
import { KpiCard } from '@/components/cabinet/KpiCard';
import { ProductDetailCharts } from '@/components/cabinet/ProductDetailCharts';
import { ProductMetricTrendChart } from '@/components/cabinet/ProductMetricTrendChart';
import type { AdsProfitDrivenProduct } from '@/lib/ads-profit-driven';
import type { ProductAttentionSignal } from '@/lib/attention-engine';
import { resolveEstimatedCogs } from '@/lib/cogs';
import { formatCount, formatMoney, formatPercent } from '@/lib/format';
import type { CostStructureRow, ProductDetail } from '@/lib/profit-dashboard';

type BenchmarkRow = {
  metric_code: string;
  metric_ru: string;
  store_avg: number | null;
  top20_avg: number | null;
  top10_avg: number | null;
};

type DetailTab = 'overview' | 'ads' | 'logistics' | 'commission' | 'other';

type CostRow = {
  name: string;
  amount: number;
  shareRevenue: number | null;
};

type WindowMetrics = {
  total7d: number;
  total30d: number;
  avgDay7d: number;
  avgDay30d: number;
  value7d: number | null;
  value30d: number | null;
  orders7d: number;
  orders30d: number;
  profit7d: number | null;
  profit30d: number | null;
};

type KpiRow = {
  label: string;
  value: string | null;
  hint?: string | null;
};

type Props = {
  product: ProductDetail;
  benchmarks: BenchmarkRow[];
  adsSignal?: AdsProfitDrivenProduct | null;
  attentionSignals?: ProductAttentionSignal[];
  storeAdSpendTotal?: number | null;
  cogsBenchmarkRatio?: number | null;
  initialFocus?: DetailTab;
  initialReason?: string;
};

const detailTabs: Array<{ key: DetailTab; label: string }> = [
  { key: 'overview', label: 'Обзор товара' },
  { key: 'ads', label: 'Реклама' },
  { key: 'logistics', label: 'Логистика' },
  { key: 'commission', label: 'Комиссии' },
  { key: 'other', label: 'Прочее' },
];

const shortMonth = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

function mapCostName(name: string) {
  const value = name.toLowerCase();
  if (value.includes('прямая логистика')) return 'Доставка до покупателя';
  if (value.includes('обратная логистика')) return 'Возвраты';
  if (value.includes('складской хвост')) return 'Складская логистика';
  return name;
}

function buildCostRows(rows: CostStructureRow[], revenue: number) {
  const grouped = new Map<string, CostRow>();

  for (const row of rows) {
    const name = mapCostName(row.category_ru || row.name || 'Расход');
    const amount = row.amount ?? row.value ?? 0;
    const shareRevenue = row.share_revenue ?? (revenue > 0 ? amount / revenue : null);
    const existing = grouped.get(name);

    if (existing) {
      existing.amount += amount;
      existing.shareRevenue = revenue > 0 ? existing.amount / revenue : null;
      continue;
    }

    grouped.set(name, {
      name,
      amount,
      shareRevenue,
    });
  }

  return [...grouped.values()].filter((row) => row.amount > 0).sort((a, b) => b.amount - a.amount);
}

function renderKpiRows(rows: KpiRow[], className = 'kpi-grid') {
  return (
    <div className={className}>
      {rows.map((row) => (
        <KpiCard
          key={row.label}
          label={row.label}
          value={row.value ?? ''}
          hint={row.hint ?? undefined}
        />
      ))}
    </div>
  );
}

function formatPeriodRange(days: string[]) {
  const valid = days.filter(Boolean).sort();
  if (!valid.length) return null;

  const start = new Date(`${valid[0]}T00:00:00`);
  const end = new Date(`${valid[valid.length - 1]}T00:00:00`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null;

  const startLabel = `${start.getDate()} ${shortMonth[start.getMonth()]}`;
  const endLabel = `${end.getDate()} ${shortMonth[end.getMonth()]}`;
  return `${startLabel} — ${endLabel}`;
}

function sumBy<T>(rows: T[], selector: (row: T) => number | null | undefined) {
  return rows.reduce((acc, row) => acc + (selector(row) ?? 0), 0);
}

function safeDiv(numerator: number, denominator: number) {
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator <= 0) return null;
  return numerator / denominator;
}

function buildWindowMetrics(product: ProductDetail, selector: (row: ProductDetail['daily'][number]) => number): WindowMetrics | null {
  const rows30 = [...product.daily].filter((row) => row.units > 0);
  const rows7 = [...rows30].sort((a, b) => a.day.localeCompare(b.day)).slice(-7);
  if (!rows30.length || !rows7.length) return null;

  const orders30d = sumBy(rows30, (row) => row.units);
  const orders7d = sumBy(rows7, (row) => row.units);
  const total30d = sumBy(rows30, selector);
  const total7d = sumBy(rows7, selector);

  return {
    total7d,
    total30d,
    avgDay7d: total7d / rows7.length,
    avgDay30d: total30d / rows30.length,
    value7d: safeDiv(total7d, orders7d),
    value30d: safeDiv(total30d, orders30d),
    orders7d,
    orders30d,
    profit7d: safeDiv(sumBy(rows7, (row) => row.operacionnaya_pribyl), orders7d),
    profit30d: safeDiv(sumBy(rows30, (row) => row.operacionnaya_pribyl), orders30d),
  };
}

function buildAverageLines(current: number | null | undefined, previous: number | null | undefined) {
  if (current === null || current === undefined || previous === null || previous === undefined) return undefined;
  return `Среднее за 7 дней: ${formatMoney(current)}\nВ среднем за 30 дней: ${formatMoney(previous)}`;
}

function buildAdsDailyRows(product: ProductDetail) {
  return product.daily.map((row) => ({
    day: row.day,
    spend: row.reklama,
    orders: row.units,
    costPerOrder: safeDiv(row.reklama, row.units),
  }));
}

function buildMetricDailyRows(
  product: ProductDetail,
  selector: (row: ProductDetail['daily'][number]) => number
) {
  return product.daily.map((row) => ({
    day: row.day,
    total: selector(row),
    perOrder: safeDiv(selector(row), row.units),
  }));
}

function buildAdsBudgetMessage(product: ProductDetail, storeAdSpendTotal: number | null | undefined) {
  if (storeAdSpendTotal && storeAdSpendTotal > 0 && product.reklama > 0) {
    return `На товар приходится ${formatPercent(product.reklama / storeAdSpendTotal)} рекламного бюджета`;
  }

  if (product.reklama > 0) {
    return `На товар приходится ${formatMoney(product.reklama)} рекламных расходов за 30 дней`;
  }

  return null;
}

export function ProductDetailExperience({
  product,
  benchmarks: _benchmarks,
  adsSignal,
  attentionSignals = [],
  storeAdSpendTotal,
  cogsBenchmarkRatio,
  initialFocus = 'overview',
}: Props) {
  const [activeTab, setActiveTab] = useState<DetailTab>(initialFocus);

  const periodLabel = useMemo(() => formatPeriodRange(product.daily.map((row) => row.day)), [product.daily]);
  const operationalCosts = product.wb_rashody || 0;
  const estimatedCostOfGoods = resolveEstimatedCogs({
    revenue: product.viruchka,
    operatingProfit: product.operacionnaya_pribyl,
    profitAfterCogs: product.pribyl_posle_sebestoimosti,
    fallbackRatio: cogsBenchmarkRatio,
  });
  const averageCheck = product.units > 0 ? product.viruchka / product.units : null;
  const costRows = useMemo(() => buildCostRows(product.cost_structure || [], product.viruchka), [product.cost_structure, product.viruchka]);
  const logisticsRows = costRows.filter((row) =>
    ['Доставка до покупателя', 'Возвраты', 'Складская логистика', 'Хранение'].includes(row.name)
  );
  const commissionRows = costRows.filter((row) => row.name.toLowerCase().includes('комис'));
  const otherRows = costRows.filter(
    (row) => !logisticsRows.some((item) => item.name === row.name) && !commissionRows.some((item) => item.name === row.name)
  );
  const adsAttention = attentionSignals.find((item) => item.category === 'ads' && item.reasonKey !== 'budget_concentration');
  const adsBudgetAttention = attentionSignals.find((item) => item.category === 'ads' && item.reasonKey === 'budget_concentration');
  const logisticsAttention = attentionSignals.find((item) => item.category === 'logistics');
  const commissionAttention = attentionSignals.find((item) => item.category === 'commission');
  const logisticsMetrics = useMemo(() => buildWindowMetrics(product, (row) => row.logistika), [product]);
  const commissionMetrics = useMemo(() => buildWindowMetrics(product, (row) => row.komissiya), [product]);
  const otherMetrics = useMemo(
    () => buildWindowMetrics(product, (row) => row.platnye_uslugi + row.shtrafy_i_uderzhaniya),
    [product]
  );
  const adsMetrics = useMemo(() => buildWindowMetrics(product, (row) => row.reklama), [product]);
  const adsBudgetMessage = buildAdsBudgetMessage(product, storeAdSpendTotal);

  const stateSections = useMemo<AttentionStateSection[]>(() => {
    const sections: AttentionStateSection[] = [];

    const adsItems = [];
    if (adsAttention) {
      adsItems.push({
        key: 'ads-signal',
        title: adsAttention.title,
        detail: adsAttention.body,
      });
    }
    if (adsBudgetAttention && adsBudgetMessage) {
      adsItems.push({
        key: 'ads-budget',
        title: adsBudgetMessage,
      });
    } else if (adsBudgetAttention) {
      adsItems.push({
        key: 'ads-budget-signal',
        title: adsBudgetAttention.body,
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
      tone: adsItems.some((item) => item.key !== 'ads-ok') ? 'warning' : 'ok',
      items: adsItems,
    });

    sections.push({
      key: 'logistics',
      label: 'Логистика',
      tone: logisticsAttention ? 'warning' : 'ok',
      items: logisticsAttention
        ? [
            {
              key: 'logistics-signal',
              title: logisticsAttention.title,
              detail: logisticsAttention.body,
            },
          ]
        : [
            {
              key: 'logistics-ok',
              title: 'Критичных отклонений по логистике не выявлено',
            },
          ],
    });

    if (commissionMetrics) {
      sections.push({
        key: 'commission',
        label: 'Комиссия',
        tone: commissionAttention ? 'warning' : 'ok',
        items: commissionAttention
          ? [
              {
                key: 'commission-signal',
                title: commissionAttention.title,
                detail: commissionAttention.body,
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

    const otherGrowth =
      otherMetrics?.value7d !== null &&
      otherMetrics?.value7d !== undefined &&
      otherMetrics?.value30d !== null &&
      otherMetrics?.value30d !== undefined &&
      otherMetrics.value7d > otherMetrics.value30d * 1.15;

    sections.push({
      key: 'other',
      label: 'Прочее',
      tone: otherGrowth ? 'warning' : 'ok',
      items: otherGrowth
        ? [
            {
              key: 'other-signal',
              title: 'За последние 7 дней прочие расходы на заказ выросли',
              detail: buildAverageLines(otherMetrics?.value7d, otherMetrics?.value30d),
            },
          ]
        : [
            {
              key: 'other-ok',
              title: 'Критичных отклонений по прочим расходам не выявлено',
            },
          ],
    });

    return sections;
  }, [adsAttention, adsBudgetAttention, adsBudgetMessage, commissionAttention, commissionMetrics, logisticsAttention, otherMetrics]);

  const attentionCount = stateSections.reduce(
    (count, section) => count + section.items.filter((item) => item.title.startsWith('За последние 7 дней') || item.title.startsWith('На товар приходится')).length,
    0
  );
  const stateTone =
    adsSignal?.status === 'ACTION' || attentionCount >= 3 ? 'critical' : attentionCount > 0 ? 'warning' : 'ok';

  const overviewKpis: KpiRow[] = [
    {
      label: 'Выручка',
      value: formatMoney(product.viruchka),
    },
    {
      label: 'Операционные затраты',
      value: formatMoney(operationalCosts),
    },
    {
      label: 'Себестоимость',
      value: formatMoney(estimatedCostOfGoods.amount),
      hint: '(оценка)',
    },
    {
      label: 'Реклама',
      value: formatMoney(product.reklama),
    },
    {
      label: 'Прибыль после рекламы',
      value: formatMoney(product.operacionnaya_pribyl),
    },
    {
      label: 'Количество продаж',
      value: product.units !== null && product.units !== undefined ? formatCount(product.units) : '0',
    },
    {
      label: 'Средний чек',
      value: averageCheck !== null ? formatMoney(averageCheck) : '',
    },
    {
      label: 'Прибыль с заказа',
      value:
        product.pribyl_na_edinicu !== null && product.pribyl_na_edinicu !== undefined
          ? formatMoney(product.pribyl_na_edinicu)
          : '',
    },
  ];

  const adsKpis: KpiRow[] = [
    {
      label: 'Расходы в день',
      value: adsMetrics ? formatMoney(adsMetrics.avgDay7d) : formatMoney(product.reklama),
      hint: buildAverageLines(adsMetrics?.avgDay7d ?? null, adsMetrics?.avgDay30d ?? null) ?? undefined,
    },
    {
      label: 'Заказы в день',
      value: adsMetrics ? formatCount(adsMetrics.orders7d / 7) : formatCount(product.units),
      hint:
        adsMetrics !== null && adsMetrics !== undefined
          ? `Среднее за 7 дней: ${formatCount(adsMetrics.orders7d / 7)}\nВ среднем за 30 дней: ${formatCount(
              adsMetrics.orders30d / 30
            )}`
          : undefined,
    },
    {
      label: 'Стоимость заказа',
      value: adsMetrics?.value7d !== null && adsMetrics?.value7d !== undefined ? formatMoney(adsMetrics.value7d) : '',
      hint: buildAverageLines(adsMetrics?.value7d ?? null, adsMetrics?.value30d ?? null) ?? undefined,
    },
  ];

  const logisticsKpis: KpiRow[] = [
    {
      label: 'Логистика в день',
      value: logisticsMetrics ? formatMoney(logisticsMetrics.avgDay7d) : formatMoney(product.logistika),
      hint: buildAverageLines(logisticsMetrics?.avgDay7d ?? null, logisticsMetrics?.avgDay30d ?? null) ?? undefined,
    },
    {
      label: 'Логистика на заказ',
      value:
        logisticsMetrics?.value7d !== null && logisticsMetrics?.value7d !== undefined
          ? formatMoney(logisticsMetrics.value7d)
          : '',
      hint: buildAverageLines(logisticsMetrics?.value7d ?? null, logisticsMetrics?.value30d ?? null) ?? undefined,
    },
    {
      label: 'Заказы в день',
      value: logisticsMetrics ? formatCount(logisticsMetrics.orders7d / 7) : formatCount(product.units),
      hint:
        logisticsMetrics !== null && logisticsMetrics !== undefined
          ? `Среднее за 7 дней: ${formatCount(logisticsMetrics.orders7d / 7)}\nВ среднем за 30 дней: ${formatCount(
              logisticsMetrics.orders30d / 30
            )}`
          : undefined,
    },
  ];

  const commissionKpis: KpiRow[] = [
    {
      label: 'Комиссия в день',
      value: commissionMetrics ? formatMoney(commissionMetrics.avgDay7d) : formatMoney(product.komissiya),
      hint: buildAverageLines(commissionMetrics?.avgDay7d ?? null, commissionMetrics?.avgDay30d ?? null) ?? undefined,
    },
    {
      label: 'Комиссия на заказ',
      value:
        commissionMetrics?.value7d !== null && commissionMetrics?.value7d !== undefined
          ? formatMoney(commissionMetrics.value7d)
          : '',
      hint: buildAverageLines(commissionMetrics?.value7d ?? null, commissionMetrics?.value30d ?? null) ?? undefined,
    },
    {
      label: 'Заказы в день',
      value: commissionMetrics ? formatCount(commissionMetrics.orders7d / 7) : formatCount(product.units),
      hint:
        commissionMetrics !== null && commissionMetrics !== undefined
          ? `Среднее за 7 дней: ${formatCount(commissionMetrics.orders7d / 7)}\nВ среднем за 30 дней: ${formatCount(
              commissionMetrics.orders30d / 30
            )}`
          : undefined,
    },
  ];

  const otherKpis: KpiRow[] = [
    {
      label: 'Прочие расходы в день',
      value: otherMetrics ? formatMoney(otherMetrics.avgDay7d) : formatMoney(product.platnye_uslugi + product.shtrafy_i_uderzhaniya),
      hint: buildAverageLines(otherMetrics?.avgDay7d ?? null, otherMetrics?.avgDay30d ?? null) ?? undefined,
    },
    {
      label: 'Прочие расходы на заказ',
      value: otherMetrics?.value7d !== null && otherMetrics?.value7d !== undefined ? formatMoney(otherMetrics.value7d) : '',
      hint: buildAverageLines(otherMetrics?.value7d ?? null, otherMetrics?.value30d ?? null) ?? undefined,
    },
    {
      label: 'Заказы в день',
      value: otherMetrics ? formatCount(otherMetrics.orders7d / 7) : formatCount(product.units),
      hint:
        otherMetrics !== null && otherMetrics !== undefined
          ? `Среднее за 7 дней: ${formatCount(otherMetrics.orders7d / 7)}\nВ среднем за 30 дней: ${formatCount(
              otherMetrics.orders30d / 30
            )}`
          : undefined,
    },
  ];

  const activeKpis =
    activeTab === 'ads'
      ? adsKpis
      : activeTab === 'logistics'
        ? logisticsKpis
        : activeTab === 'commission'
          ? commissionKpis
          : activeTab === 'other'
            ? otherKpis
            : overviewKpis;

  const adsDailyRows = useMemo(() => buildAdsDailyRows(product), [product]);
  const logisticsDailyRows = useMemo(() => buildMetricDailyRows(product, (row) => row.logistika), [product]);
  const commissionDailyRows = useMemo(() => buildMetricDailyRows(product, (row) => row.komissiya), [product]);
  const otherDailyRows = useMemo(
    () => buildMetricDailyRows(product, (row) => row.platnye_uslugi + row.shtrafy_i_uderzhaniya),
    [product]
  );

  return (
    <div>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="page-title">{product.nazvanie}</h1>
          <p className="page-subtle">{product.sku}</p>
        </div>
        {periodLabel ? <div className="glass-pill">Период: {periodLabel}</div> : null}
      </header>

      <section className="panel section">
        <div className="tab-nav">
          {detailTabs.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`tab-link ${activeTab === item.key ? 'tab-link--active' : ''}`}
              onClick={() => setActiveTab(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </section>

      <section className="panel section">{renderKpiRows(activeKpis, 'kpi-grid')}</section>

      {activeTab === 'overview' ? (
        <>
          <AttentionStatePanel
            title="Состояние"
            tone={stateTone}
            statusTextOverride={stateTone === 'ok' ? 'Состояние товара: в норме' : 'Состояние товара: есть зоны внимания'}
            sections={stateSections}
          />
          {product.daily.length ? <ProductDetailCharts daily={product.daily} /> : null}
        </>
      ) : null}

      {activeTab === 'ads' ? (
        <>
          <section className="panel section">
            <h2 className="m-0 text-lg font-semibold">Реклама</h2>
            <div className="mt-4 space-y-3 text-sm text-slate-200">
              {adsAttention ? (
                <p className="m-0 whitespace-pre-line">
                  {[adsAttention.title, adsAttention.body].filter(Boolean).join('\n')}
                </p>
              ) : (
                <p className="m-0">Критичных отклонений по рекламе не выявлено</p>
              )}
              {adsBudgetMessage ? <p className="m-0">{adsBudgetMessage}</p> : null}
            </div>
          </section>

          <ProductAdsProfitChart daily={adsDailyRows} tone={adsSignal?.status === 'ACTION' ? 'action' : 'hypothesis'} />
        </>
      ) : null}

      {activeTab === 'logistics' && logisticsRows.length ? (
        <>
          <section className="panel section">
            <h2 className="m-0 text-lg font-semibold">Логистика</h2>
            <p className="mt-3 whitespace-pre-line text-sm text-slate-300">
              {logisticsAttention
                ? [logisticsAttention.title, logisticsAttention.body].filter(Boolean).join('\n')
                : 'Критичных отклонений по логистике не выявлено'}
            </p>
          </section>

          <ProductMetricTrendChart
            title="Динамика логистики"
            totalLabel="Логистика"
            perOrderLabel="Логистика на заказ"
            daily={logisticsDailyRows}
            color="#ef4444"
          />

          <section className="panel section">
            <div className="table-wrap mt-4">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Статья</th>
                    <th>Сумма</th>
                    <th>Доля</th>
                  </tr>
                </thead>
                <tbody>
                  {logisticsRows.map((row) => (
                    <tr key={row.name}>
                      <td>{row.name}</td>
                      <td>{formatMoney(row.amount)}</td>
                      <td>{row.shareRevenue !== null ? formatPercent(row.shareRevenue) : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}

      {activeTab === 'commission' && commissionRows.length ? (
        <>
          <section className="panel section">
            <h2 className="m-0 text-lg font-semibold">Комиссии</h2>
            <p className="mt-3 whitespace-pre-line text-sm text-slate-300">
              {commissionAttention
                ? [commissionAttention.title, commissionAttention.body].filter(Boolean).join('\n')
                : commissionMetrics
                  ? 'Критичных отклонений по комиссии не выявлено'
                  : 'Недостаточно данных, чтобы надёжно оценить комиссию'}
            </p>
          </section>

          <ProductMetricTrendChart
            title="Динамика комиссии"
            totalLabel="Комиссия"
            perOrderLabel="Комиссия на заказ"
            daily={commissionDailyRows}
            color="#38bdf8"
          />

          <section className="panel section">
            <div className="table-wrap mt-4">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Статья</th>
                    <th>Сумма</th>
                    <th>Доля</th>
                  </tr>
                </thead>
                <tbody>
                  {commissionRows.map((row) => (
                    <tr key={row.name}>
                      <td>{row.name}</td>
                      <td>{formatMoney(row.amount)}</td>
                      <td>{row.shareRevenue !== null ? formatPercent(row.shareRevenue) : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}

      {activeTab === 'other' && otherRows.length ? (
        <>
          <section className="panel section">
            <h2 className="m-0 text-lg font-semibold">Прочее</h2>
            <p className="mt-3 whitespace-pre-line text-sm text-slate-300">
              {otherMetrics &&
              otherMetrics.value7d !== null &&
              otherMetrics.value30d !== null &&
              otherMetrics.value7d > otherMetrics.value30d * 1.15
                ? `За последние 7 дней прочие расходы на заказ выросли\n${buildAverageLines(
                    otherMetrics.value7d,
                    otherMetrics.value30d
                  )}`
                : 'Критичных отклонений по прочим расходам не выявлено'}
            </p>
          </section>

          <ProductMetricTrendChart
            title="Динамика прочих расходов"
            totalLabel="Прочие расходы"
            perOrderLabel="Прочие расходы на заказ"
            daily={otherDailyRows}
            color="#f97316"
          />

          <section className="panel section">
            <div className="table-wrap mt-4">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Статья</th>
                    <th>Сумма</th>
                    <th>Доля</th>
                  </tr>
                </thead>
                <tbody>
                  {otherRows.map((row) => (
                    <tr key={row.name}>
                      <td>{row.name}</td>
                      <td>{formatMoney(row.amount)}</td>
                      <td>{row.shareRevenue !== null ? formatPercent(row.shareRevenue) : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}
    </div>
  );
}
