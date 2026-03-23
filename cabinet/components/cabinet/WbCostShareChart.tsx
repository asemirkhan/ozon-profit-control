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

import { formatPercent } from '@/lib/format';

type DailyRow = {
  day: string;
  totalShare: number | null;
  logisticsShare: number | null;
  commissionShare: number | null;
  servicesShare: number | null;
  withholdingsShare: number | null;
};

type Props = {
  daily: DailyRow[];
};

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

export function WbCostShareChart({ daily }: Props) {
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
              tickFormatter={(value) => formatPercent(typeof value === 'number' ? value : Number(value || 0))}
              tick={{ fill: '#94a3b8', fontSize: 11 }}
            />
            <Tooltip
              formatter={(value: number, name: string) => [formatPercent(value), name]}
              contentStyle={{
                backgroundColor: 'rgba(2, 6, 23, 0.95)',
                border: '1px solid rgba(148,163,184,0.24)',
                borderRadius: 14,
              }}
            />
            <Legend verticalAlign="top" height={36} wrapperStyle={{ color: '#cbd5e1', fontSize: '12px' }} />
            <Line type="monotone" dataKey="totalShare" name="Все расходы WB" stroke="#22d3ee" strokeWidth={2.4} dot={false} />
            <Line type="monotone" dataKey="logisticsShare" name="Логистика" stroke="#ef4444" strokeWidth={2.2} dot={false} />
            <Line type="monotone" dataKey="commissionShare" name="Комиссия" stroke="#38bdf8" strokeWidth={2.2} dot={false} />
            <Line type="monotone" dataKey="servicesShare" name="Услуги" stroke="#14b8a6" strokeWidth={2.2} dot={false} />
            <Line type="monotone" dataKey="withholdingsShare" name="Удержания" stroke="#f97316" strokeWidth={2.2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </section>
  );
}
