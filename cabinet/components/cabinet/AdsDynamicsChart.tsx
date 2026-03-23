'use client';

import {
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

type DailyRow = {
  day: string;
  spend: number;
};

type Props = {
  daily: DailyRow[];
};

const compactMoneyFmt = new Intl.NumberFormat('ru-RU', {
  notation: 'compact',
  maximumFractionDigits: 1,
});

const dayTickFmt = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'short',
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

export function AdsDynamicsChart({ daily }: Props) {
  if (!daily.length) return null;

  return (
    <section className="panel section">
      <h2 className="m-0 text-lg font-semibold">Динамика</h2>
      <div className="mt-4 h-[360px]">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={daily} margin={{ top: 12, right: 20, left: 12, bottom: 24 }}>
            <CartesianGrid stroke="rgba(148,163,184,0.16)" vertical={false} />
            <XAxis
              dataKey="day"
              tickFormatter={formatDay}
              tick={{ fill: '#94a3b8', fontSize: 11 }}
              label={{ value: 'День', position: 'insideBottom', offset: -12, fill: '#94a3b8' }}
            />
            <YAxis
              tickFormatter={formatAxisMoney}
              tick={{ fill: '#94a3b8', fontSize: 11 }}
              label={{ value: 'Рубли', angle: -90, position: 'insideLeft', fill: '#94a3b8' }}
            />
            <Tooltip
              formatter={(value: number, name: string) => [formatMoney(value), name]}
              contentStyle={{
                backgroundColor: 'rgba(2, 6, 23, 0.95)',
                border: '1px solid rgba(148,163,184,0.24)',
                borderRadius: 14,
              }}
            />
            <Legend verticalAlign="top" height={36} wrapperStyle={{ color: '#cbd5e1', fontSize: '12px' }} />
            <Line type="monotone" dataKey="spend" name="Расход" stroke="#f59e0b" strokeWidth={2.4} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
