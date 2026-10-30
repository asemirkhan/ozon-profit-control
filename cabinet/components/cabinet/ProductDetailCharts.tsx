'use client';

import { useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { formatMoney } from '@/lib/format';

type ChartTab = 'income' | 'costs' | 'structure';
type CostMode = 'summary' | 'categories';
type CostFilter = 'all' | 'reklama' | 'logistika' | 'komissiya' | 'platnye_uslugi' | 'shtrafy_i_uderzhaniya';

type DailyRow = {
  day: string;
  viruchka: number;
  reklama: number;
  komissiya: number;
  logistika: number;
  platnye_uslugi: number;
  shtrafy_i_uderzhaniya: number;
  operacionnaya_pribyl: number;
};

type Props = {
  daily: DailyRow[];
};

const chartTabs: Array<{ key: ChartTab; label: string }> = [
  { key: 'income', label: 'Доходы' },
  { key: 'costs', label: 'Расходы' },
  { key: 'structure', label: 'Структура' },
];

const costModes: Array<{ key: CostMode; label: string }> = [
  { key: 'summary', label: 'Сводно' },
  { key: 'categories', label: 'По статьям' },
];

const costFilters: Array<{ key: CostFilter; label: string; color: string }> = [
  { key: 'all', label: 'Все статьи', color: '#cbd5e1' },
  { key: 'reklama', label: 'Реклама', color: '#f59e0b' },
  { key: 'logistika', label: 'Логистика', color: '#ef4444' },
  { key: 'komissiya', label: 'Комиссия', color: '#38bdf8' },
  { key: 'platnye_uslugi', label: 'Услуги WB', color: '#14b8a6' },
  { key: 'shtrafy_i_uderzhaniya', label: 'Удержания', color: '#f97316' },
];

const dayTickFmt = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'short',
});

const compactMoneyFmt = new Intl.NumberFormat('ru-RU', {
  notation: 'compact',
  maximumFractionDigits: 1,
});

function formatDay(value: unknown): string {
  if (typeof value !== 'string') return String(value ?? '');
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return dayTickFmt.format(parsed);
}

function formatAxisMoney(value: unknown): string {
  const amount = typeof value === 'number' ? value : Number(value || 0);
  return `${compactMoneyFmt.format(amount)} ₽`;
}

const chartAxisColor = '#6e628c';
const chartGridColor = 'rgba(108, 70, 193, 0.12)';
const chartTooltipStyle = {
  background: 'rgba(255, 255, 255, 0.98)',
  border: '1px solid rgba(108, 70, 193, 0.14)',
  borderRadius: '16px',
  color: '#251b3d',
  boxShadow: '0 16px 36px rgba(73, 44, 138, 0.12)',
};

export function ProductDetailCharts({ daily }: Props) {
  const [activeTab, setActiveTab] = useState<ChartTab>('income');
  const [activeCostMode, setActiveCostMode] = useState<CostMode>('summary');
  const [activeCostFilter, setActiveCostFilter] = useState<CostFilter>('all');
  const activeCost = costFilters.find((item) => item.key === activeCostFilter) || costFilters[0];
  const rows = daily.map((row) => ({
    ...row,
    total_costs:
      row.reklama +
      row.logistika +
      row.komissiya +
      row.platnye_uslugi +
      row.shtrafy_i_uderzhaniya,
  }));

  return (
    <section className="panel section">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="tab-nav">
          {chartTabs.map((item) => (
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
      </div>

      {activeTab === 'costs' ? (
        <>
          <div className="tab-nav mt-4">
            {costModes.map((item) => (
              <button
                key={item.key}
                type="button"
                className={`tab-link ${activeCostMode === item.key ? 'tab-link--active' : ''}`}
                onClick={() => setActiveCostMode(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>
          {activeCostMode === 'categories' ? (
            <div className="tab-nav mt-4">
              {costFilters.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  className={`tab-link ${activeCostFilter === item.key ? 'tab-link--active' : ''}`}
                  onClick={() => setActiveCostFilter(item.key)}
                >
                  {item.label}
                </button>
              ))}
            </div>
          ) : null}
        </>
      ) : null}

      <div className="mt-4 h-[380px]">
        <ResponsiveContainer width="100%" height="100%">
          {activeTab === 'income' ? (
            <LineChart data={rows} margin={{ top: 12, right: 16, left: 8, bottom: 20 }}>
              <CartesianGrid stroke={chartGridColor} vertical={false} />
              <XAxis
                dataKey="day"
                tickFormatter={formatDay}
                tick={{ fill: chartAxisColor, fontSize: 11 }}
                label={{ value: 'День', position: 'insideBottom', offset: -10, fill: chartAxisColor }}
              />
              <YAxis
                tickFormatter={formatAxisMoney}
                tick={{ fill: chartAxisColor, fontSize: 11 }}
                label={{ value: 'Рубли', angle: -90, position: 'insideLeft', fill: chartAxisColor }}
              />
              <Tooltip
                labelFormatter={(value) => formatDay(value)}
                formatter={(value: number, name: string) => [formatMoney(value), name]}
                contentStyle={chartTooltipStyle}
              />
              <Legend verticalAlign="top" height={28} wrapperStyle={{ color: chartAxisColor, fontSize: '12px' }} />
              <Line type="monotone" dataKey="viruchka" name="Выручка" stroke="#22d3ee" strokeWidth={2.4} dot={false} />
              <Line
                type="monotone"
                dataKey="operacionnaya_pribyl"
                name="Операционная прибыль"
                stroke="#34d399"
                strokeWidth={2.4}
                dot={false}
              />
            </LineChart>
          ) : activeTab === 'costs' ? (
            <LineChart data={rows} margin={{ top: 12, right: 16, left: 8, bottom: 20 }}>
              <CartesianGrid stroke={chartGridColor} vertical={false} />
              <XAxis
                dataKey="day"
                tickFormatter={formatDay}
                tick={{ fill: chartAxisColor, fontSize: 11 }}
                label={{ value: 'День', position: 'insideBottom', offset: -10, fill: chartAxisColor }}
              />
              <YAxis
                tickFormatter={formatAxisMoney}
                tick={{ fill: chartAxisColor, fontSize: 11 }}
                label={{ value: 'Рубли', angle: -90, position: 'insideLeft', fill: chartAxisColor }}
              />
              <Tooltip
                labelFormatter={(value) => formatDay(value)}
                formatter={(value: number, name: string) => [formatMoney(value), name]}
                contentStyle={chartTooltipStyle}
              />
              <Legend verticalAlign="top" height={28} wrapperStyle={{ color: chartAxisColor, fontSize: '12px' }} />
              {activeCostMode === 'summary' ? (
                <Line type="monotone" dataKey="total_costs" name="Суммарные расходы" stroke="#f97316" strokeWidth={2.6} dot={false} />
              ) : activeCostFilter === 'all' ? (
                <>
                  <Line type="monotone" dataKey="reklama" name="Реклама" stroke="#f59e0b" strokeWidth={2.3} dot={false} />
                  <Line type="monotone" dataKey="logistika" name="Логистика" stroke="#ef4444" strokeWidth={2.3} dot={false} />
                  <Line type="monotone" dataKey="komissiya" name="Комиссия" stroke="#38bdf8" strokeWidth={2.3} dot={false} />
                  <Line type="monotone" dataKey="platnye_uslugi" name="Услуги WB" stroke="#14b8a6" strokeWidth={2.3} dot={false} />
                  <Line type="monotone" dataKey="shtrafy_i_uderzhaniya" name="Удержания" stroke="#f97316" strokeWidth={2.3} dot={false} />
                </>
              ) : (
                <Line type="monotone" dataKey={activeCost.key} name={activeCost.label} stroke={activeCost.color} strokeWidth={2.6} dot={false} />
              )}
            </LineChart>
          ) : (
            <BarChart data={rows} margin={{ top: 12, right: 16, left: 8, bottom: 20 }}>
              <CartesianGrid stroke={chartGridColor} vertical={false} />
              <XAxis
                dataKey="day"
                tickFormatter={formatDay}
                tick={{ fill: chartAxisColor, fontSize: 11 }}
                label={{ value: 'День', position: 'insideBottom', offset: -10, fill: chartAxisColor }}
              />
              <YAxis
                tickFormatter={formatAxisMoney}
                tick={{ fill: chartAxisColor, fontSize: 11 }}
                label={{ value: 'Рубли', angle: -90, position: 'insideLeft', fill: chartAxisColor }}
              />
              <Tooltip
                labelFormatter={(value) => formatDay(value)}
                formatter={(value: number, name: string) => [formatMoney(value), name]}
                contentStyle={chartTooltipStyle}
              />
              <Legend verticalAlign="top" height={28} wrapperStyle={{ color: chartAxisColor, fontSize: '12px' }} />
              <Bar dataKey="reklama" name="Реклама" stackId="costs" fill="#f59e0b" radius={[6, 6, 0, 0]} />
              <Bar dataKey="logistika" name="Логистика" stackId="costs" fill="#ef4444" />
              <Bar dataKey="komissiya" name="Комиссия" stackId="costs" fill="#38bdf8" />
              <Bar dataKey="platnye_uslugi" name="Услуги WB" stackId="costs" fill="#14b8a6" />
              <Bar dataKey="shtrafy_i_uderzhaniya" name="Удержания" stackId="costs" fill="#f97316" />
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
    </section>
  );
}
