'use client';

import dynamic from 'next/dynamic';
import { useEffect, useMemo, useState } from 'react';
import { Area, AreaChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

type MetricPoint = {
  day: string;
  revenue: number;
  profitAfterAds: number | null;
  orders: number;
  expenses: number;
};

type ExpensePoint = {
  day: string;
  total: number;
  ads: number;
  logistics: number;
  commission: number;
  acquiring: number;
  storage: number;
  withholding: number | null;
};

type GeoRow = {
  name: string;
  revenueCurrentPerDay: number;
  revenueBasePerDay: number;
  revenueDeltaPerDay: number;
  revenueDeltaPct?: number | null;
  ordersCurrentPerDay: number;
  ordersBasePerDay: number;
  ordersDeltaPerDay: number;
  contribution: number;
  reliability: string;
};

type ProductGeoPack = {
  productId: string;
  productName: string;
  regions: GeoRow[];
  warehouses: GeoRow[];
};

type Props = {
  title?: string;
  incomeRows: MetricPoint[];
  expenseRows: ExpensePoint[];
  storeRegions: GeoRow[];
  storeWarehouses: GeoRow[];
  products: ProductGeoPack[];
};

type PrimaryMode = 'income' | 'expenses' | 'geo';
type ChartRange = '7d' | '28d' | '90d' | 'all';
type ExpenseMode = 'total' | 'items';
type ExpenseItem = 'all' | 'ads' | 'logistics' | 'commission' | 'acquiring' | 'storage' | 'withholding';
type GeoMode = 'cards' | 'map' | 'table';
type GeoMetric = 'revenue' | 'orders';
type GeoValueMode = 'period' | 'delta';
type GeoTableMode = 'regions' | 'warehouses';

type AggregatedGeoRow = GeoRow & {
  currentRevenuePeriod: number;
  baseRevenuePeriod: number;
  currentOrdersPeriod: number;
  baseOrdersPeriod: number;
  averageCheckCurrent: number | null;
  averageCheckBase: number | null;
  ordersDeltaPct: number | null;
};

type MapDisplayRow = {
  row: AggregatedGeoRow;
  coordinates: [number, number];
  value: number;
  valueLabel: string;
};

const InteractiveRussiaMap = dynamic(
  () => import('@/components/cabinet/InteractiveRussiaMap').then((module) => module.InteractiveRussiaMap),
  { ssr: false },
);

const EXPENSE_COLORS: Record<Exclude<ExpenseItem, 'all'>, string> = {
  ads: '#8b5cf6',
  logistics: '#f97316',
  commission: '#06b6d4',
  acquiring: '#ef4444',
  storage: '#7c3aed',
  withholding: '#ec4899',
};

const EXPENSE_LABELS: Record<Exclude<ExpenseItem, 'all'>, string> = {
  ads: 'Реклама',
  logistics: 'Логистика',
  commission: 'Комиссии',
  acquiring: 'Эквайринг',
  storage: 'Хранение',
  withholding: 'Корректировки и прочие списания',
};

const GEO_COORDINATES: Record<string, [number, number]> = {
  'Астраханская область': [48.04, 46.35],
  'Белгородская область': [36.59, 50.6],
  'Виртуальный Махачкала': [47.5, 42.98],
  Волгоград: [44.52, 48.71],
  Воронеж: [39.2, 51.67],
  'Воронежская область': [39.2, 51.67],
  'Екатеринбург - Перспективная 14': [60.6, 56.84],
  'Иркутская область': [104.29, 52.29],
  Казань: [49.11, 55.8],
  Котовск: [41.5, 52.58],
  Краснодар: [38.98, 45.04],
  'Краснодарский край': [39.0, 45.04],
  'Красноярский край': [92.85, 56.01],
  'Липецкая область': [39.57, 52.61],
  Москва: [37.62, 55.76],
  'Московская область': [38.0, 55.62],
  Невинномысск: [41.95, 44.63],
  'Нижегородская область': [44.0, 56.3],
  Новосибирск: [82.93, 55.03],
  'Новосибирская область': [82.93, 55.03],
  Пенза: [45.0, 53.2],
  'Пензенская область': [45.0, 53.2],
  'Пермский край': [56.25, 58.0],
  'Республика Дагестан': [47.5, 42.98],
  'Республика Крым': [34.1, 44.95],
  'Республика Саха (Якутия)': [129.73, 62.03],
  'Ростов-на-Дону': [39.7, 47.24],
  'Ростовская область': [39.7, 47.24],
  'Рязанская область': [39.74, 54.62],
  'Рязань (Тюшевское)': [39.74, 54.62],
  'Самара (Новосемейкино)': [50.35, 53.37],
  'Самарская область': [50.15, 53.2],
  'Санкт-Петербург': [30.33, 59.94],
  Сарапул: [56.48, 56.46],
  'Свердловская область': [60.6, 56.84],
  'Склад WB': [37.62, 55.76],
  'Склад продавца': [37.62, 55.76],
  'Ставропольский край': [41.97, 45.04],
  Тула: [37.62, 54.2],
  'Ханты-Мансийский автономный округ': [69.0, 61.0],
  Электросталь: [38.45, 55.79],
};

const chartTooltipStyle = {
  background: 'rgba(255, 255, 255, 0.96)',
  border: '1px solid rgba(108, 70, 193, 0.14)',
  borderRadius: '18px',
  color: '#251b3d',
  boxShadow: '0 18px 40px rgba(73, 44, 138, 0.12)',
};

function formatDateLabel(value: string) {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return `${String(date.getDate()).padStart(2, '0')}.${String(date.getMonth() + 1).padStart(2, '0')}`;
}

function formatRub(value: number | string | null | undefined) {
  return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(Number(value) || 0)} ₽`;
}

function formatCount(value: number | string | null | undefined) {
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(Number(value) || 0);
}

function formatOrders(value: number | string | null | undefined) {
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(Math.round(Number(value) || 0));
}

function formatPercent(value: number | undefined | null) {
  return `${((value || 0) * 100).toFixed(1).replace('.', ',')}%`;
}

function formatSignedMoney(value: number) {
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${formatRub(Math.abs(value))}`;
}

function formatSignedCount(value: number) {
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${formatCount(Math.abs(value))}`;
}

function safePercentDelta(current: number, base: number) {
  if (!Number.isFinite(base) || Math.abs(base) < 1e-9) return null;
  return (current - base) / base;
}

function safeDiv(numerator: number, denominator: number) {
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator <= 0) return null;
  return numerator / denominator;
}

function formatPeriodRange(start?: string, end?: string) {
  if (!start || !end) return '';
  return `${formatDateLabel(start)}–${formatDateLabel(end)}`;
}

function aggregateGeoRows(rows: GeoRow[], currentPeriodDays: number, basePeriodDays: number) {
  const grouped = new Map<string, AggregatedGeoRow>();

  for (const row of rows) {
    const current = grouped.get(row.name) ?? {
      name: row.name,
      revenueCurrentPerDay: 0,
      revenueBasePerDay: 0,
      revenueDeltaPerDay: 0,
      revenueDeltaPct: 0,
      ordersCurrentPerDay: 0,
      ordersBasePerDay: 0,
      ordersDeltaPerDay: 0,
      contribution: 0,
      reliability: row.reliability,
      currentRevenuePeriod: 0,
      baseRevenuePeriod: 0,
      currentOrdersPeriod: 0,
      baseOrdersPeriod: 0,
      averageCheckCurrent: null,
      averageCheckBase: null,
      ordersDeltaPct: 0,
    };

    current.revenueCurrentPerDay += row.revenueCurrentPerDay || 0;
    current.revenueBasePerDay += row.revenueBasePerDay || 0;
    current.revenueDeltaPerDay += row.revenueDeltaPerDay || 0;
    current.ordersCurrentPerDay += row.ordersCurrentPerDay || 0;
    current.ordersBasePerDay += row.ordersBasePerDay || 0;
    current.ordersDeltaPerDay += row.ordersDeltaPerDay || 0;
    current.contribution += row.contribution || 0;
    if (current.reliability !== 'нормально') current.reliability = row.reliability;

    grouped.set(row.name, current);
  }

  return Array.from(grouped.values())
    .map((row) => {
      const currentRevenuePeriod = row.revenueCurrentPerDay * currentPeriodDays;
      const baseRevenuePeriod = row.revenueBasePerDay * basePeriodDays;
      const currentOrdersPeriod = row.ordersCurrentPerDay * currentPeriodDays;
      const baseOrdersPeriod = row.ordersBasePerDay * basePeriodDays;

      return {
        ...row,
        currentRevenuePeriod,
        baseRevenuePeriod,
        currentOrdersPeriod,
        baseOrdersPeriod,
        averageCheckCurrent: safeDiv(currentRevenuePeriod, currentOrdersPeriod),
        averageCheckBase: safeDiv(baseRevenuePeriod, baseOrdersPeriod),
        revenueDeltaPct: safePercentDelta(row.revenueCurrentPerDay, row.revenueBasePerDay),
        ordersDeltaPct: safePercentDelta(row.ordersCurrentPerDay, row.ordersBasePerDay),
      };
    })
    .sort((a, b) => Math.abs(b.revenueDeltaPerDay) - Math.abs(a.revenueDeltaPerDay));
}

function expenseLabel(item: Exclude<ExpenseItem, 'all'>) {
  return EXPENSE_LABELS[item];
}

function currentMapValue(row: AggregatedGeoRow, metric: GeoMetric, valueMode: GeoValueMode) {
  if (valueMode === 'period') {
    return metric === 'orders' ? row.currentOrdersPeriod : row.currentRevenuePeriod;
  }
  return metric === 'orders' ? row.ordersDeltaPerDay : row.revenueDeltaPerDay;
}

function currentMapValueLabel(row: AggregatedGeoRow, metric: GeoMetric, valueMode: GeoValueMode) {
  const value = currentMapValue(row, metric, valueMode);
  if (valueMode === 'period') {
    return metric === 'orders' ? `${formatOrders(value)} заказов за период` : `${formatRub(value)} за период`;
  }
  return metric === 'orders' ? `${formatSignedCount(value)} к базе в среднем за день` : `${formatSignedMoney(value)} к базе в среднем за день`;
}

function compactMapValueLabel(row: AggregatedGeoRow, metric: GeoMetric, valueMode: GeoValueMode) {
  const value = currentMapValue(row, metric, valueMode);
  if (valueMode === 'period') {
    return metric === 'orders' ? formatOrders(value) : formatRub(value);
  }
  return metric === 'orders' ? formatSignedCount(value) : formatSignedMoney(value);
}

function geoMetricWords(metric: GeoMetric) {
  return metric === 'orders'
    ? {
        total: 'Заказы за текущий период',
        currentDaily: 'Заказы за текущий период в день',
        baseDaily: 'Заказы за базовый период в день',
        change: 'Изменение количества заказов в день к базе',
      }
    : {
        total: 'Выручка за текущий период',
        currentDaily: 'Выручка за текущий период в день',
        baseDaily: 'Выручка за базовый период в день',
        change: 'Изменение выручки в день к базе',
      };
}

function metricTotal(row: AggregatedGeoRow, metric: GeoMetric) {
  return metric === 'orders' ? row.currentOrdersPeriod : row.currentRevenuePeriod;
}

function metricCurrentDaily(row: AggregatedGeoRow, metric: GeoMetric) {
  return metric === 'orders' ? row.ordersCurrentPerDay : row.revenueCurrentPerDay;
}

function metricBaseDaily(row: AggregatedGeoRow, metric: GeoMetric) {
  return metric === 'orders' ? row.ordersBasePerDay : row.revenueBasePerDay;
}

function metricDeltaDaily(row: AggregatedGeoRow, metric: GeoMetric) {
  return metric === 'orders' ? row.ordersDeltaPerDay : row.revenueDeltaPerDay;
}

function metricDeltaPct(row: AggregatedGeoRow, metric: GeoMetric) {
  return metric === 'orders' ? row.ordersDeltaPct : row.revenueDeltaPct;
}

export function EconomicsWorkbench({ title = 'Динамика магазина', incomeRows, expenseRows, storeRegions, storeWarehouses, products: _products }: Props) {
  const [mode, setMode] = useState<PrimaryMode>('income');
  const [chartRange, setChartRange] = useState<ChartRange>('all');
  const [expenseMode, setExpenseMode] = useState<ExpenseMode>('total');
  const [expenseItem, setExpenseItem] = useState<ExpenseItem>('all');
  const [geoMode, setGeoMode] = useState<GeoMode>('map');
  const [geoMetric, setGeoMetric] = useState<GeoMetric>('revenue');
  const [geoValueMode, setGeoValueMode] = useState<GeoValueMode>('period');
  const [geoTableMode, setGeoTableMode] = useState<GeoTableMode>('regions');
  const [hoveredGeoName, setHoveredGeoName] = useState<string | null>(null);

  const chartRangeDays = chartRange === 'all' ? null : chartRange === '7d' ? 7 : chartRange === '28d' ? 28 : 90;
  const visibleIncomeRows = useMemo(
    () => (chartRangeDays === null ? incomeRows : incomeRows.slice(-chartRangeDays)),
    [chartRangeDays, incomeRows],
  );
  const visibleExpenseRows = useMemo(
    () => (chartRangeDays === null ? expenseRows : expenseRows.slice(-chartRangeDays)),
    [chartRangeDays, expenseRows],
  );
  const chartRangeLabel = formatPeriodRange(visibleIncomeRows[0]?.day, visibleIncomeRows[visibleIncomeRows.length - 1]?.day);

  const currentPeriodDays = Math.min(7, incomeRows.length || 7);
  const basePeriodDays = Math.min(28, Math.max(incomeRows.length - currentPeriodDays, 0)) || 28;
  const currentRangeLabel = formatPeriodRange(
    incomeRows[incomeRows.length - currentPeriodDays]?.day,
    incomeRows[incomeRows.length - 1]?.day,
  );
  const baseRangeLabel = formatPeriodRange(
    incomeRows[Math.max(0, incomeRows.length - currentPeriodDays - basePeriodDays)]?.day,
    incomeRows[Math.max(0, incomeRows.length - currentPeriodDays - 1)]?.day,
  );

  const rawGeoRows = useMemo(() => {
    return geoTableMode === 'regions' ? storeRegions : storeWarehouses;
  }, [geoTableMode, storeRegions, storeWarehouses]);

  const geoRows = useMemo(() => aggregateGeoRows(rawGeoRows, currentPeriodDays, basePeriodDays), [rawGeoRows, currentPeriodDays, basePeriodDays]);

  useEffect(() => {
    setHoveredGeoName(geoRows[0]?.name || null);
  }, [geoRows]);

  const mapRows = useMemo<MapDisplayRow[]>(() => {
    return geoRows
      .map((row) => {
        const coordinates = GEO_COORDINATES[row.name];
        if (!coordinates) return null;
        return {
          row,
          coordinates,
          value: currentMapValue(row, geoMetric, geoValueMode),
          valueLabel: compactMapValueLabel(row, geoMetric, geoValueMode),
        };
      })
      .filter(Boolean) as MapDisplayRow[];
  }, [geoRows, geoMetric, geoValueMode]);

  const mapMaxValue = useMemo(() => Math.max(...mapRows.map((row) => Math.abs(row.value)), 1), [mapRows]);
  const sortedGeoRows = useMemo(
    () => [...geoRows].sort((a, b) => Math.abs(currentMapValue(b, geoMetric, geoValueMode)) - Math.abs(currentMapValue(a, geoMetric, geoValueMode))),
    [geoRows, geoMetric, geoValueMode],
  );
  const activeGeoRow = useMemo(() => geoRows.find((row) => row.name === hoveredGeoName) || geoRows[0] || null, [geoRows, hoveredGeoName]);

  return (
    <section className="workbench">
      <div className="workbench__header">
        <div className="workbench__headline">
          <h2 className="workbench__title">{title}</h2>
          {mode !== 'geo' && chartRangeLabel ? <p className="section-note">Показаны данные за {chartRangeLabel}</p> : null}
        </div>
        <div className="workbench__toolbar">
          <div className="chip-row">
            <button type="button" className={`workbench-tab ${mode === 'income' ? 'workbench-tab--active' : ''}`} onClick={() => setMode('income')}>
              Доходы
            </button>
            <button type="button" className={`workbench-tab ${mode === 'expenses' ? 'workbench-tab--active' : ''}`} onClick={() => setMode('expenses')}>
              Расходы
            </button>
            <button type="button" className={`workbench-tab ${mode === 'geo' ? 'workbench-tab--active' : ''}`} onClick={() => setMode('geo')}>
              География
            </button>
          </div>
          {mode !== 'geo' ? (
            <div className="workbench-control-group">
              <span className="workbench-control-group__label">Период графика</span>
              <div className="chip-row">
                {[
                  ['7d', '7 дней'],
                  ['28d', '28 дней'],
                  ['90d', '90 дней'],
                  ['all', 'Вся история'],
                ].map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    className={`workbench-chip ${chartRange === key ? 'workbench-chip--active' : ''}`}
                    onClick={() => setChartRange(key as ChartRange)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </div>

      {mode === 'income' ? (
        <div className="workbench__content">
          <div className="workbench__chart">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={visibleIncomeRows}>
                <defs>
                  <linearGradient id="wbIncomeRevenue" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#7c3aed" stopOpacity={0.22} />
                    <stop offset="95%" stopColor="#7c3aed" stopOpacity={0.02} />
                  </linearGradient>
                  <linearGradient id="wbIncomeProfit" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#06b6d4" stopOpacity={0.2} />
                    <stop offset="95%" stopColor="#06b6d4" stopOpacity={0.02} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="rgba(108, 70, 193, 0.12)" vertical={false} />
                <XAxis dataKey="day" tickFormatter={formatDateLabel} tickLine={false} axisLine={false} stroke="rgba(37,27,61,0.48)" />
                <YAxis tickFormatter={(value) => `${Math.round(value / 1000)}к`} tickLine={false} axisLine={false} stroke="rgba(37,27,61,0.48)" />
                <Tooltip formatter={(value: number, name: string) => [formatRub(value), name]} labelFormatter={formatDateLabel} contentStyle={chartTooltipStyle} />
                <Legend />
                <Area type="monotone" dataKey="revenue" name="Выручка" stroke="#7c3aed" fill="url(#wbIncomeRevenue)" strokeWidth={2.6} />
                <Area type="monotone" connectNulls={false} dataKey="profitAfterAds" name="Результат" stroke="#06b6d4" fill="url(#wbIncomeProfit)" strokeWidth={2.6} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : null}

      {mode === 'expenses' ? (
        <div className="workbench__content">
          <div className="workbench__toolbar">
            <div className="chip-row">
              <button type="button" className={`workbench-chip ${expenseMode === 'total' ? 'workbench-chip--active' : ''}`} onClick={() => setExpenseMode('total')}>
                Общие затраты
              </button>
              <button type="button" className={`workbench-chip ${expenseMode === 'items' ? 'workbench-chip--active' : ''}`} onClick={() => setExpenseMode('items')}>
                По статьям
              </button>
            </div>

            {expenseMode === 'items' ? (
              <div className="chip-row">
                {[
                  ['all', 'Все статьи'],
                  ['ads', 'Реклама'],
                  ['logistics', 'Логистика'],
                  ['commission', 'Комиссии'],
                  ['acquiring', 'Эквайринг'],
                  ['storage', 'Хранение'],
                  ['withholding', 'Корректировки и прочие списания'],
                ].map(([key, label]) => (
                  <button key={key} type="button" className={`workbench-chip ${expenseItem === key ? 'workbench-chip--active' : ''}`} onClick={() => setExpenseItem(key as ExpenseItem)}>
                    {label}
                  </button>
                ))}
              </div>
            ) : null}
          </div>

          <div className="workbench__chart">
            <ResponsiveContainer width="100%" height="100%">
              {expenseMode === 'total' ? (
                <LineChart data={visibleExpenseRows}>
                  <CartesianGrid stroke="rgba(108, 70, 193, 0.12)" vertical={false} />
                  <XAxis dataKey="day" tickFormatter={formatDateLabel} tickLine={false} axisLine={false} stroke="rgba(37,27,61,0.48)" />
                  <YAxis tickFormatter={(value) => `${Math.round(value / 1000)}к`} tickLine={false} axisLine={false} stroke="rgba(37,27,61,0.48)" />
                  <Tooltip formatter={(value: number, name: string) => [formatRub(value), name]} labelFormatter={formatDateLabel} contentStyle={chartTooltipStyle} />
                  <Legend />
                  <Line type="monotone" dataKey="total" name="Общие затраты" stroke="#f97316" strokeWidth={2.6} dot={false} />
                </LineChart>
              ) : expenseItem === 'all' ? (
                <LineChart data={visibleExpenseRows}>
                  <CartesianGrid stroke="rgba(108, 70, 193, 0.12)" vertical={false} />
                  <XAxis dataKey="day" tickFormatter={formatDateLabel} tickLine={false} axisLine={false} stroke="rgba(37,27,61,0.48)" />
                  <YAxis tickFormatter={(value) => `${Math.round(value / 1000)}к`} tickLine={false} axisLine={false} stroke="rgba(37,27,61,0.48)" />
                  <Tooltip formatter={(value: number, name: string) => [formatRub(value), name]} labelFormatter={formatDateLabel} contentStyle={chartTooltipStyle} />
                  <Legend />
                  {Object.entries(EXPENSE_COLORS).map(([key, color]) => (
                    <Line key={key} type="monotone" dataKey={key} name={expenseLabel(key as Exclude<ExpenseItem, 'all'>)} stroke={color} strokeWidth={2.3} dot={false} />
                  ))}
                </LineChart>
              ) : (
                <LineChart data={visibleExpenseRows}>
                  <CartesianGrid stroke="rgba(108, 70, 193, 0.12)" vertical={false} />
                  <XAxis dataKey="day" tickFormatter={formatDateLabel} tickLine={false} axisLine={false} stroke="rgba(37,27,61,0.48)" />
                  <YAxis tickFormatter={(value) => `${Math.round(value / 1000)}к`} tickLine={false} axisLine={false} stroke="rgba(37,27,61,0.48)" />
                  <Tooltip formatter={(value: number, name: string) => [formatRub(value), name]} labelFormatter={formatDateLabel} contentStyle={chartTooltipStyle} />
                  <Legend />
                  <Line type="monotone" dataKey={expenseItem} name={expenseLabel(expenseItem)} stroke={EXPENSE_COLORS[expenseItem]} strokeWidth={2.6} dot={false} />
                </LineChart>
              )}
            </ResponsiveContainer>
          </div>
        </div>
      ) : null}

      {mode === 'geo' ? (
        <div className="workbench__content">
          <div className="workbench__toolbar workbench__toolbar--geo">
            <div className="workbench-toolbar-grid">
              <div className="workbench-control-group">
                <span className="workbench-control-group__label">Режим</span>
                <div className="chip-row">
                  <button type="button" className={`workbench-chip ${geoMode === 'cards' ? 'workbench-chip--active' : ''}`} onClick={() => setGeoMode('cards')}>
                    Карточки
                  </button>
                  <button type="button" className={`workbench-chip ${geoMode === 'map' ? 'workbench-chip--active' : ''}`} onClick={() => setGeoMode('map')}>
                    Карта
                  </button>
                  <button type="button" className={`workbench-chip ${geoMode === 'table' ? 'workbench-chip--active' : ''}`} onClick={() => setGeoMode('table')}>
                    Таблица
                  </button>
                </div>
              </div>

              <div className="workbench-control-group">
                <span className="workbench-control-group__label">Где показываем точки</span>
                <div className="chip-row">
                  <button type="button" className={`workbench-chip ${geoTableMode === 'regions' ? 'workbench-chip--active' : ''}`} onClick={() => setGeoTableMode('regions')}>
                    Города и регионы
                  </button>
                  <button type="button" className={`workbench-chip ${geoTableMode === 'warehouses' ? 'workbench-chip--active' : ''}`} onClick={() => setGeoTableMode('warehouses')}>
                    Склады
                  </button>
                </div>
              </div>
              <div className="workbench-control-group">
                <span className="workbench-control-group__label">Показатель</span>
                <div className="chip-row">
                  <button type="button" className={`workbench-chip ${geoMetric === 'revenue' ? 'workbench-chip--active' : ''}`} onClick={() => setGeoMetric('revenue')}>
                    Выручка
                  </button>
                  <button type="button" className={`workbench-chip ${geoMetric === 'orders' ? 'workbench-chip--active' : ''}`} onClick={() => setGeoMetric('orders')}>
                    Заказы
                  </button>
                </div>
              </div>
              {geoMode === 'map' ? (
                <>
                  <div className="workbench-control-group">
                    <span className="workbench-control-group__label">Какую метрику показываем</span>
                    <div className="chip-row">
                      <button type="button" className={`workbench-chip ${geoValueMode === 'period' ? 'workbench-chip--active' : ''}`} onClick={() => setGeoValueMode('period')}>
                        Сумма
                      </button>
                      <button type="button" className={`workbench-chip ${geoValueMode === 'delta' ? 'workbench-chip--active' : ''}`} onClick={() => setGeoValueMode('delta')}>
                        Изменение к базе
                      </button>
                    </div>
                  </div>
                </>
              ) : null}
            </div>

            {geoMode !== 'map' ? (
              <div className="workbench__toolbar-meta workbench__toolbar-meta--compact">
                <div className="workbench-geo-periods">
                  <div className="glass-pill workbench__period-pill workbench__period-pill--compact">Текущий период: {currentRangeLabel}</div>
                  <div className="glass-pill workbench__period-pill workbench__period-pill--secondary workbench__period-pill--compact">Базовый период: {baseRangeLabel}</div>
                </div>
              </div>
            ) : null}
          </div>

          {geoMode === 'cards' ? (
            <div className="workbench-card-grid">
              {sortedGeoRows.slice(0, 6).map((row) => (
                <article key={row.name} className="workbench-mini-card workbench-mini-card--detailed">
                  <div className="workbench-mini-card__title">{row.name}</div>
                  <dl className="workbench-mini-card__facts">
                    <div>
                      <dt>{geoMetricWords(geoMetric).total}</dt>
                      <dd>{geoMetric === 'orders' ? formatOrders(metricTotal(row, geoMetric)) : formatRub(metricTotal(row, geoMetric))}</dd>
                    </div>
                    <div>
                      <dt>{geoMetricWords(geoMetric).currentDaily}</dt>
                      <dd>{geoMetric === 'orders' ? formatOrders(metricCurrentDaily(row, geoMetric)) : formatRub(metricCurrentDaily(row, geoMetric))}</dd>
                    </div>
                    <div>
                      <dt>{geoMetricWords(geoMetric).baseDaily}</dt>
                      <dd>{geoMetric === 'orders' ? formatOrders(metricBaseDaily(row, geoMetric)) : formatRub(metricBaseDaily(row, geoMetric))}</dd>
                    </div>
                    <div>
                      <dt>{geoMetricWords(geoMetric).change}</dt>
                      <dd>
                        {geoMetric === 'orders' ? formatSignedCount(metricDeltaDaily(row, geoMetric)) : formatSignedMoney(metricDeltaDaily(row, geoMetric))}{' '}
                        <span className="workbench-mini-card__delta-meta">({formatPercent(metricDeltaPct(row, geoMetric))})</span>
                      </dd>
                    </div>
                  </dl>
                </article>
              ))}
            </div>
          ) : null}

          {geoMode === 'map' ? (
            <div className="workbench-geo-layout workbench-geo-layout--single">
              <div className="workbench-geo-canvas">
                <div className="workbench-geo-overlay">
                  <div className="workbench-geo-periods workbench-geo-periods--map">
                    <div className="glass-pill workbench__period-pill workbench__period-pill--map workbench__period-pill--compact">Текущий период: {currentRangeLabel}</div>
                    <div className="glass-pill workbench__period-pill workbench__period-pill--map workbench__period-pill--compact">Базовый период: {baseRangeLabel}</div>
                  </div>
                  {activeGeoRow ? (
                    <div className="workbench-geo-point-card">
                      <p className="workbench-geo-point-card__eyebrow">{geoTableMode === 'regions' ? 'Город или регион' : 'Склад'}</p>
                      <h3 className="workbench-geo-point-card__title">{activeGeoRow.name}</h3>
                      <dl className="workbench-geo-point-card__facts">
                        <div>
                          <dt>{geoMetricWords(geoMetric).total}</dt>
                          <dd>{geoMetric === 'orders' ? formatOrders(metricTotal(activeGeoRow, geoMetric)) : formatRub(metricTotal(activeGeoRow, geoMetric))}</dd>
                        </div>
                        <div>
                          <dt>{geoMetricWords(geoMetric).currentDaily}</dt>
                          <dd>{geoMetric === 'orders' ? formatOrders(metricCurrentDaily(activeGeoRow, geoMetric)) : formatRub(metricCurrentDaily(activeGeoRow, geoMetric))}</dd>
                        </div>
                        <div>
                          <dt>{geoMetricWords(geoMetric).baseDaily}</dt>
                          <dd>{geoMetric === 'orders' ? formatOrders(metricBaseDaily(activeGeoRow, geoMetric)) : formatRub(metricBaseDaily(activeGeoRow, geoMetric))}</dd>
                        </div>
                        <div>
                          <dt>{geoMetricWords(geoMetric).change}</dt>
                          <dd>
                            {geoMetric === 'orders' ? formatSignedCount(metricDeltaDaily(activeGeoRow, geoMetric)) : formatSignedMoney(metricDeltaDaily(activeGeoRow, geoMetric))}{' '}
                            <span className="workbench-mini-card__delta-meta">({formatPercent(metricDeltaPct(activeGeoRow, geoMetric))})</span>
                          </dd>
                        </div>
                      </dl>
                      <p className="workbench-geo-point-card__base-note">Базовый период: {baseRangeLabel}.</p>
                    </div>
                  ) : null}
                </div>

                <InteractiveRussiaMap mapRows={mapRows} mapMaxValue={mapMaxValue} geoValueMode={geoValueMode} onHover={setHoveredGeoName} />
              </div>
            </div>
          ) : null}

          {geoMode === 'table' ? (
            <div className="workbench-geo-table-wrap">
              <div className="table-wrap workbench__table">
                <table className="table-base workbench-table">
                  <thead>
                    <tr>
                      <th>{geoTableMode === 'regions' ? 'Город / регион' : 'Склад'}</th>
                      <th>{geoMetricWords(geoMetric).total}</th>
                      <th>{geoMetricWords(geoMetric).currentDaily}</th>
                      <th>{geoMetricWords(geoMetric).baseDaily}</th>
                      <th>{geoMetricWords(geoMetric).change}</th>
                      <th>Изменение к базе, %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sortedGeoRows.map((row) => (
                      <tr key={row.name}>
                        <td>{row.name}</td>
                        <td>{geoMetric === 'orders' ? formatOrders(metricTotal(row, geoMetric)) : formatRub(metricTotal(row, geoMetric))}</td>
                        <td>{geoMetric === 'orders' ? formatOrders(metricCurrentDaily(row, geoMetric)) : formatRub(metricCurrentDaily(row, geoMetric))}</td>
                        <td>{geoMetric === 'orders' ? formatOrders(metricBaseDaily(row, geoMetric)) : formatRub(metricBaseDaily(row, geoMetric))}</td>
                        <td>{geoMetric === 'orders' ? formatSignedCount(metricDeltaDaily(row, geoMetric)) : formatSignedMoney(metricDeltaDaily(row, geoMetric))}</td>
                        <td>{formatPercent(metricDeltaPct(row, geoMetric))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
