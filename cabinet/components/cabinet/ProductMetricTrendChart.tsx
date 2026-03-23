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

import { formatMoney } from '@/lib/format';

type ChartMode = 'total' | 'per_order';

type DailyRow = {
  day: string;
  total: number;
  perOrder: number | null;
};

type Props = {
  title: string;
  totalLabel: string;
  perOrderLabel: string;
  daily: DailyRow[];
  color?: string;
};

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

export function ProductMetricTrendChart({
  title,
  totalLabel,
  perOrderLabel,
  daily,
  color = '#38bdf8',
}: Props) {
  const [mode, setMode] = useState<ChartMode>('per_order');
  const rows = useMemo(
    () =>
      daily.map((row) => ({
        day: row.day,
        total: row.total,
        per_order: row.perOrder,
      })),
    [daily]
  );

  if (!rows.length) return null;

  return (
    <section className="panel section">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="m-0 text-lg font-semibold">{title}</h2>
        <div className="tab-nav">
          <button
            type="button"
            className={`tab-link ${mode === 'total' ? 'tab-link--active' : ''}`}
            onClick={() => setMode('total')}
          >
            {totalLabel}
          </button>
          <button
            type="button"
            className={`tab-link ${mode === 'per_order' ? 'tab-link--active' : ''}`}
            onClick={() => setMode('per_order')}
          >
            {perOrderLabel}
          </button>
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
              tickFormatter={formatAxisMoney}
              tick={{ fill: '#94a3b8', fontSize: 11 }}
              label={{ value: 'Рубли', angle: -90, position: 'insideLeft', fill: '#94a3b8' }}
            />
            <Tooltip
              labelFormatter={(value) => formatDay(value)}
              formatter={(value: unknown) => {
                const amount = typeof value === 'number' ? value : Number(value || 0);
                return [formatMoney(amount), mode === 'total' ? totalLabel : perOrderLabel];
              }}
            />
            <Line type="monotone" dataKey={mode === 'total' ? 'total' : 'per_order'} stroke={color} strokeWidth={2.6} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
