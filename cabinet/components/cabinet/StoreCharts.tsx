'use client';

import {
  CartesianGrid,
  Area,
  AreaChart,
  Line,
  LineChart,
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts';
import { formatMoney, formatPercent } from '@/lib/format';

type DailyPoint = {
  date: string;
  revenue_total: number;
  profit_after_ads_total: number;
  ads_spend_total: number;
};

type CostSlice = {
  name: string;
  value: number;
};

type Props = {
  daily: DailyPoint[];
  costStructure: CostSlice[];
};

const pieColors = ['#22d3ee', '#34d399', '#f59e0b', '#f97316', '#f43f5e'];
const compactMoneyFmt = new Intl.NumberFormat('ru-RU', {
  notation: 'compact',
  maximumFractionDigits: 1
});
const dayTickFmt = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'short'
});
const dayTooltipFmt = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'long'
});
const RADIAN = Math.PI / 180;

const metricLabels: Record<string, string> = {
  revenue_total: 'Выручка',
  profit_after_ads_total: 'Прибыль после рекламы',
  ads_spend_total: 'Расходы на рекламу'
};

function toNumber(value: unknown): number {
  if (typeof value === 'number') return value;
  if (typeof value === 'string') return Number(value) || 0;
  return 0;
}

function formatAxisMoney(value: unknown): string {
  return `${compactMoneyFmt.format(toNumber(value))} ₽`;
}

function formatDayLabel(value: unknown): string {
  if (typeof value !== 'string') return String(value ?? '');
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return dayTickFmt.format(parsed);
}

function formatDayTooltip(value: unknown): string {
  if (typeof value !== 'string') return String(value ?? '');
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return dayTooltipFmt.format(parsed);
}

type PieLabelProps = {
  cx?: number;
  cy?: number;
  midAngle?: number;
  outerRadius?: number;
  percent?: number;
  name?: string;
  value?: number;
};

function renderCostLabel(props: PieLabelProps) {
  const {
    cx = 0,
    cy = 0,
    midAngle = 0,
    outerRadius = 0,
    percent = 0,
    name = '',
    value = 0
  } = props;
  const radius = outerRadius + 20;
  const x = cx + radius * Math.cos(-midAngle * RADIAN);
  const y = cy + radius * Math.sin(-midAngle * RADIAN);
  const textAnchor = x > cx ? 'start' : 'end';
  return (
    <text x={x} y={y} fill="#e2e8f0" textAnchor={textAnchor} dominantBaseline="central" fontSize={11}>
      {`${name} - ${formatPercent(percent)} - ${formatMoney(value)}`}
    </text>
  );
}

export function StoreCharts({ daily, costStructure }: Props) {
  const positiveCostStructure = [...costStructure]
    .filter((slice) => toNumber(slice.value) > 0)
    .sort((a, b) => toNumber(b.value) - toNumber(a.value));

  const totalCost = positiveCostStructure.reduce((acc, slice) => acc + toNumber(slice.value), 0);
  const negativeAdjustments = costStructure
    .filter((slice) => toNumber(slice.value) < 0)
    .reduce((acc, slice) => acc + toNumber(slice.value), 0);

  const uniqueRevenuePoints = new Set(daily.map((point) => point.revenue_total)).size;
  const uniqueProfitPoints = new Set(daily.map((point) => point.profit_after_ads_total)).size;
  const uniqueAdsPoints = new Set(daily.map((point) => point.ads_spend_total)).size;
  const chartLooksFlat =
    daily.length > 1 && uniqueRevenuePoints === 1 && uniqueProfitPoints === 1 && uniqueAdsPoints === 1;

  return (
    <div className="mt-4 grid gap-4 lg:grid-cols-2">
      <section className="panel section">
        <h3 className="text-lg font-semibold">Выручка по дням</h3>
        <div className="mt-3 h-64">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={daily}>
              <defs>
                <linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#22d3ee" stopOpacity={0.55} />
                  <stop offset="95%" stopColor="#22d3ee" stopOpacity={0.03} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="rgba(148,163,184,0.15)" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={formatDayLabel} />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={formatAxisMoney} />
              <Tooltip
                labelFormatter={formatDayTooltip}
                formatter={(value: unknown, name: unknown) => [
                  formatMoney(toNumber(value)),
                  metricLabels[String(name)] || String(name)
                ]}
                contentStyle={{
                  backgroundColor: 'rgba(2, 6, 23, 0.92)',
                  border: '1px solid rgba(148, 163, 184, 0.35)',
                  borderRadius: 12
                }}
                cursor={{ stroke: 'rgba(148,163,184,0.28)', strokeWidth: 1 }}
              />
              <Area
                type="monotone"
                dataKey="revenue_total"
                stroke="#22d3ee"
                strokeWidth={2}
                fillOpacity={1}
                fill="url(#revenueFill)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="panel section">
        <h3 className="text-lg font-semibold">Прибыль после рекламы</h3>
        <div className="mt-3 h-64">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={daily}>
              <CartesianGrid stroke="rgba(148,163,184,0.15)" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={formatDayLabel} />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={formatAxisMoney} />
              <Tooltip
                labelFormatter={formatDayTooltip}
                formatter={(value: unknown, name: unknown) => [
                  formatMoney(toNumber(value)),
                  metricLabels[String(name)] || String(name)
                ]}
                contentStyle={{
                  backgroundColor: 'rgba(2, 6, 23, 0.92)',
                  border: '1px solid rgba(148, 163, 184, 0.35)',
                  borderRadius: 12
                }}
                cursor={{ stroke: 'rgba(148,163,184,0.28)', strokeWidth: 1 }}
              />
              <Line
                type="monotone"
                dataKey="profit_after_ads_total"
                stroke="#34d399"
                strokeWidth={2.4}
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="panel section">
        <h3 className="text-lg font-semibold">Реклама по дням</h3>
        <div className="mt-3 h-64">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={daily}>
              <defs>
                <linearGradient id="adsFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.55} />
                  <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="rgba(148,163,184,0.15)" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={formatDayLabel} />
              <YAxis tick={{ fill: '#94a3b8', fontSize: 11 }} tickFormatter={formatAxisMoney} />
              <Tooltip
                labelFormatter={formatDayTooltip}
                formatter={(value: unknown, name: unknown) => [
                  formatMoney(toNumber(value)),
                  metricLabels[String(name)] || String(name)
                ]}
                contentStyle={{
                  backgroundColor: 'rgba(2, 6, 23, 0.92)',
                  border: '1px solid rgba(148, 163, 184, 0.35)',
                  borderRadius: 12
                }}
                cursor={{ stroke: 'rgba(148,163,184,0.28)', strokeWidth: 1 }}
              />
              <Area
                type="monotone"
                dataKey="ads_spend_total"
                stroke="#f59e0b"
                strokeWidth={2}
                fillOpacity={1}
                fill="url(#adsFill)"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        {chartLooksFlat ? (
          <p className="mt-3 rounded-xl border border-amber-400/35 bg-amber-500/10 px-3 py-2 text-xs text-amber-100">
            Дневная динамика выглядит слишком ровной. Обычно это признак тестового или переагрегированного источника.
          </p>
        ) : null}
      </section>

      <section className="panel section">
        <h3 className="text-lg font-semibold">Структура расходов за период</h3>
        <div className="mt-3 h-64">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={positiveCostStructure}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                innerRadius={48}
                outerRadius={88}
                paddingAngle={2}
                labelLine={{ stroke: 'rgba(148,163,184,0.45)' }}
                label={renderCostLabel}
              >
                {positiveCostStructure.map((slice, idx) => (
                  <Cell key={slice.name} fill={pieColors[idx % pieColors.length]} />
                ))}
              </Pie>
              <Tooltip
                formatter={(value: unknown, name: unknown) => {
                  const numericValue = toNumber(value);
                  const share = totalCost > 0 ? numericValue / totalCost : 0;
                  return [`${formatMoney(numericValue)} (${formatPercent(share)})`, String(name)];
                }}
                contentStyle={{
                  backgroundColor: 'rgba(2, 6, 23, 0.92)',
                  border: '1px solid rgba(148, 163, 184, 0.35)',
                  borderRadius: 12
                }}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
        <div className="mt-3 rounded-xl border border-cyan-400/30 bg-cyan-500/10 px-3 py-2">
          <p className="m-0 text-xs uppercase tracking-wide text-cyan-100/80">Итого расходов</p>
          <p className="m-0 text-xl font-semibold text-cyan-50">{formatMoney(totalCost)}</p>
          {negativeAdjustments < 0 ? (
            <p className="m-0 text-xs text-amber-100/85">
              Корректировки/возвраты: {formatMoney(negativeAdjustments)}
            </p>
          ) : null}
        </div>
      </section>
    </div>
  );
}
