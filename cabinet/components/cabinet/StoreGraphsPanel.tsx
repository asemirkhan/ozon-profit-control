'use client';

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

type ChartRow = {
  day: string;
  revenue: number;
  profitAfterAds: number;
  orders: number;
  expenses: number;
  deltaRevenueToBase: number;
  deltaProfitToBase: number;
  deltaOrdersToBase: number;
};

type StoreGraphsPanelProps = {
  rows: ChartRow[];
};

function formatDateLabel(value: string) {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) return value;
  return `${String(date.getDate()).padStart(2, '0')}.${String(date.getMonth() + 1).padStart(2, '0')}`;
}

function formatRub(value: number | string) {
  return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 }).format(Number(value) || 0)} ₽`;
}

function formatCount(value: number | string) {
  return new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 1 }).format(Number(value) || 0);
}

export function StoreGraphsPanel({ rows }: StoreGraphsPanelProps) {
  return (
    <div className="dashboard-chart-grid">
      <section className="panel section">
        <div className="section-heading">
          <div>
            <h2 className="section-title">Выручка и прибыль после рекламы</h2>
            <p className="section-note">Показываем текущую дневную динамику на полностью подтвержденном слое.</p>
          </div>
        </div>
        <div className="chart-shell__chart">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={rows}>
              <defs>
                <linearGradient id="revenueFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#7c3aed" stopOpacity={0.28} />
                  <stop offset="95%" stopColor="#7c3aed" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="profitFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#c084fc" stopOpacity={0.24} />
                  <stop offset="95%" stopColor="#c084fc" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="#e9ddff" vertical={false} />
              <XAxis dataKey="day" tickFormatter={formatDateLabel} stroke="#7d6b9a" tickLine={false} axisLine={false} />
              <YAxis stroke="#7d6b9a" tickLine={false} axisLine={false} tickFormatter={(value) => `${Math.round(value / 1000)}к`} />
              <Tooltip
                formatter={(value: number, key: string) => [formatRub(value), key === 'revenue' ? 'Выручка' : 'Результат']}
                labelFormatter={formatDateLabel}
              />
              <Legend />
              <Area type="monotone" dataKey="revenue" name="Выручка" stroke="#7c3aed" fill="url(#revenueFill)" strokeWidth={2.4} />
              <Area
                type="monotone"
                dataKey="profitAfterAds"
                name="Результат"
                stroke="#c084fc"
                fill="url(#profitFill)"
                strokeWidth={2.4}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="panel section">
        <div className="section-heading">
          <div>
            <h2 className="section-title">Заказы и расходы</h2>
            <p className="section-note">Смотрим, где просадка в заказах и как на этом фоне меняются основные дневные расходы.</p>
          </div>
        </div>
        <div className="chart-shell__chart">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows}>
              <CartesianGrid stroke="#e9ddff" vertical={false} />
              <XAxis dataKey="day" tickFormatter={formatDateLabel} stroke="#7d6b9a" tickLine={false} axisLine={false} />
              <YAxis yAxisId="left" stroke="#7d6b9a" tickLine={false} axisLine={false} tickFormatter={formatCount} />
              <YAxis yAxisId="right" orientation="right" stroke="#7d6b9a" tickLine={false} axisLine={false} tickFormatter={(value) => `${Math.round(value / 1000)}к`} />
              <Tooltip
                formatter={(value: number, key: string) => [
                  key === 'orders' ? formatCount(value) : formatRub(value),
                  key === 'orders' ? 'Заказы' : 'Расходы',
                ]}
                labelFormatter={formatDateLabel}
              />
              <Legend />
              <Line yAxisId="left" type="monotone" dataKey="orders" name="Заказы" stroke="#7c3aed" strokeWidth={2.4} dot={false} />
              <Line yAxisId="right" type="monotone" dataKey="expenses" name="Расходы" stroke="#ef4444" strokeWidth={2.4} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      </section>
    </div>
  );
}
