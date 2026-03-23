'use client';

import { useMemo, useState } from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

import { formatCount, formatMoney } from '@/lib/format';

type MetricMode = 'spend' | 'orders' | 'cost_per_order';

type DailyRow = {
  day: string;
  spend: number;
  orders: number;
  costPerOrder: number | null;
};

type Props = {
  daily: DailyRow[];
  tone?: 'hypothesis' | 'action';
};

const modeOptions: Array<{ key: MetricMode; label: string }> = [
  { key: 'spend', label: 'Расходы' },
  { key: 'orders', label: 'Заказы' },
  { key: 'cost_per_order', label: 'Стоимость заказа' },
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

function formatAxisCount(value: unknown): string {
  const amount = typeof value === 'number' ? value : Number(value || 0);
  return compactMoneyFmt.format(amount);
}

export function ProductAdsProfitChart({ daily, tone = 'hypothesis' }: Props) {
  const [mode, setMode] = useState<MetricMode>('cost_per_order');
  const rows = useMemo(
    () =>
      daily.map((row) => ({
        day: row.day,
        spend: row.spend,
        orders: row.orders,
        cost_per_order: row.costPerOrder,
      })),
    [daily]
  );

  if (!rows.length) return null;

  const color = tone === 'action' ? '#f87171' : '#f59e0b';
  const dataKey = mode === 'cost_per_order' ? 'cost_per_order' : mode;
  const label = mode === 'spend' ? 'Расходы' : mode === 'orders' ? 'Заказы' : 'Стоимость заказа';
  const axisFormatter = mode === 'orders' ? formatAxisCount : formatAxisMoney;

  return (
    <section className="panel section">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="m-0 text-lg font-semibold">Динамика рекламы</h2>
        <div className="tab-nav">
          {modeOptions.map((item) => (
            <button
              key={item.key}
              type="button"
              className={`tab-link ${mode === item.key ? 'tab-link--active' : ''}`}
              onClick={() => setMode(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4 h-[320px]">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 12, right: 16, left: 8, bottom: 20 }}>
            <CartesianGrid stroke="rgba(148,163,184,0.16)" vertical={false} />
            <XAxis
              dataKey="day"
              tickFormatter={formatDay}
              tick={{ fill: '#94a3b8', fontSize: 11 }}
              label={{ value: 'День', position: 'insideBottom', offset: -10, fill: '#94a3b8' }}
            />
            <YAxis
              tickFormatter={axisFormatter}
              tick={{ fill: '#94a3b8', fontSize: 11 }}
              label={{ value: mode === 'orders' ? 'Количество' : 'Рубли', angle: -90, position: 'insideLeft', fill: '#94a3b8' }}
            />
            <Tooltip
              labelFormatter={(value) => formatDay(value)}
              formatter={(value: unknown) => {
                const amount = typeof value === 'number' ? value : Number(value || 0);
                return [mode === 'orders' ? formatCount(amount) : formatMoney(amount), label];
              }}
            />
            <Line type="monotone" dataKey={dataKey} stroke={color} strokeWidth={2.6} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
