'use client';

import { useMemo, useState } from 'react';
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

type DailyRow = {
  day: string;
  viruchka: number;
  reklama: number;
  komissiya: number;
  logistika: number;
  platnye_uslugi: number;
  shtrafy_i_uderzhaniya: number;
  wb_rashody: number;
  operacionnaya_pribyl: number;
  units?: number;
};

type Props = {
  daily: DailyRow[];
};

type ChartType = 'income' | 'expenses' | 'structure';
type ChartMode = 'total' | 'unit';
type ExpenseMode = 'summary' | 'categories';
type ExpenseCategory = 'all' | 'ads' | 'logistics' | 'commission' | 'services' | 'withholdings' | 'other';

const compactMoneyFmt = new Intl.NumberFormat('ru-RU', {
  notation: 'compact',
  maximumFractionDigits: 1,
});

const dayTickFmt = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'short',
});

const dayTooltipFmt = new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit',
  month: 'long',
});

const chartTabs: Array<{ key: ChartType; label: string }> = [
  { key: 'income', label: 'Доходы' },
  { key: 'expenses', label: 'Расходы' },
  { key: 'structure', label: 'Структура' },
];

const expenseTabs: Array<{ key: ExpenseMode; label: string }> = [
  { key: 'summary', label: 'Общие' },
  { key: 'categories', label: 'По статьям' },
];

const expenseCategories: Array<{ key: ExpenseCategory; label: string; color: string; dataKey?: string }> = [
  { key: 'all', label: 'Все статьи', color: '#cbd5e1' },
  { key: 'ads', label: 'Реклама', color: '#f59e0b', dataKey: 'ads' },
  { key: 'logistics', label: 'Логистика', color: '#ef4444', dataKey: 'logistics' },
  { key: 'commission', label: 'Комиссия', color: '#38bdf8', dataKey: 'commission' },
  { key: 'services', label: 'Услуги WB', color: '#14b8a6', dataKey: 'services' },
  { key: 'withholdings', label: 'Удержания', color: '#fb7185', dataKey: 'withholdings' },
  { key: 'other', label: 'Прочее', color: '#a78bfa', dataKey: 'other' },
];

function formatAxisMoney(value: unknown): string {
  const amount = typeof value === 'number' ? value : Number(value || 0);
  return `${compactMoneyFmt.format(amount)} ₽`;
}

function formatDay(value: unknown): string {
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

function buildRows(daily: DailyRow[], mode: ChartMode) {
  return daily.map((row) => {
    const units = row.units && row.units > 0 ? row.units : null;
    const divider = mode === 'unit' && units ? units : 1;
    const expenseTotal = row.reklama + row.wb_rashody;
    const other = Math.max(0, row.wb_rashody - row.logistika - row.komissiya - row.platnye_uslugi - row.shtrafy_i_uderzhaniya);

    return {
      day: row.day,
      revenue: row.viruchka / divider,
      profit: row.operacionnaya_pribyl / divider,
      expenseTotal: expenseTotal / divider,
      ads: row.reklama / divider,
      logistics: row.logistika / divider,
      commission: row.komissiya / divider,
      services: row.platnye_uslugi / divider,
      withholdings: row.shtrafy_i_uderzhaniya / divider,
      other: other / divider,
    };
  });
}

function tooltipFormatter(value: number, name: string) {
  return [formatMoney(value), name];
}

export function OwnerDashboardCharts({ daily }: Props) {
  const [chartType, setChartType] = useState<ChartType>('income');
  const [mode, setMode] = useState<ChartMode>('total');
  const [expenseMode, setExpenseMode] = useState<ExpenseMode>('summary');
  const [expenseCategory, setExpenseCategory] = useState<ExpenseCategory>('all');
  const rows = useMemo(() => buildRows(daily, mode), [daily, mode]);
  const selectedExpenseCategory = expenseCategories.find((item) => item.key === expenseCategory);
  const chartNode =
    chartType === 'income' ? (
      <LineChart data={rows} syncId="store-economics-chart" margin={{ top: 12, right: 20, left: 12, bottom: 12 }}>
        <CartesianGrid stroke="rgba(148,163,184,0.16)" vertical={false} />
        <XAxis dataKey="day" tickFormatter={formatDay} tick={{ fill: '#94a3b8', fontSize: 11 }} />
        <YAxis tickFormatter={formatAxisMoney} tick={{ fill: '#94a3b8', fontSize: 11 }} />
        <Tooltip
          labelFormatter={formatDayTooltip}
          formatter={tooltipFormatter}
          contentStyle={{
            backgroundColor: 'rgba(2, 6, 23, 0.95)',
            border: '1px solid rgba(148,163,184,0.24)',
            borderRadius: 14,
          }}
        />
        <Legend verticalAlign="top" height={36} wrapperStyle={{ color: '#cbd5e1', fontSize: '12px' }} />
        <Line type="monotone" dataKey="revenue" name={mode === 'total' ? 'Выручка' : 'Выручка на товар'} stroke="#22d3ee" strokeWidth={2.4} dot={false} />
        <Line type="monotone" dataKey="profit" name={mode === 'total' ? 'Прибыль' : 'Прибыль на товар'} stroke="#34d399" strokeWidth={2.4} dot={false} />
      </LineChart>
    ) : chartType === 'expenses' ? (
      <LineChart data={rows} syncId="store-economics-chart" margin={{ top: 12, right: 20, left: 12, bottom: 12 }}>
        <CartesianGrid stroke="rgba(148,163,184,0.16)" vertical={false} />
        <XAxis dataKey="day" tickFormatter={formatDay} tick={{ fill: '#94a3b8', fontSize: 11 }} />
        <YAxis tickFormatter={formatAxisMoney} tick={{ fill: '#94a3b8', fontSize: 11 }} />
        <Tooltip
          labelFormatter={formatDayTooltip}
          formatter={tooltipFormatter}
          contentStyle={{
            backgroundColor: 'rgba(2, 6, 23, 0.95)',
            border: '1px solid rgba(148,163,184,0.24)',
            borderRadius: 14,
          }}
        />
        <Legend verticalAlign="top" height={36} wrapperStyle={{ color: '#cbd5e1', fontSize: '12px' }} />
        {expenseMode === 'summary' ? (
          <Line
            type="monotone"
            dataKey="expenseTotal"
            name={mode === 'total' ? 'Суммарные расходы' : 'Суммарные расходы на товар'}
            stroke="#f97316"
            strokeWidth={2.6}
            dot={false}
          />
        ) : expenseCategory === 'all' ? (
          <>
            <Line type="monotone" dataKey="ads" name="Реклама" stroke="#f59e0b" strokeWidth={2.2} dot={false} />
            <Line type="monotone" dataKey="logistics" name="Логистика" stroke="#ef4444" strokeWidth={2.2} dot={false} />
            <Line type="monotone" dataKey="commission" name="Комиссия" stroke="#38bdf8" strokeWidth={2.2} dot={false} />
            <Line type="monotone" dataKey="services" name="Услуги WB" stroke="#14b8a6" strokeWidth={2.2} dot={false} />
            <Line type="monotone" dataKey="withholdings" name="Удержания" stroke="#fb7185" strokeWidth={2.2} dot={false} />
            <Line type="monotone" dataKey="other" name="Прочее" stroke="#a78bfa" strokeWidth={2.2} dot={false} />
          </>
        ) : (
          <Line
            type="monotone"
            dataKey={selectedExpenseCategory?.dataKey}
            name={selectedExpenseCategory?.label || ''}
            stroke={selectedExpenseCategory?.color || '#cbd5e1'}
            strokeWidth={2.6}
            dot={false}
          />
        )}
      </LineChart>
    ) : (
      <BarChart data={rows} syncId="store-economics-chart" margin={{ top: 12, right: 20, left: 12, bottom: 12 }}>
        <CartesianGrid stroke="rgba(148,163,184,0.16)" vertical={false} />
        <XAxis dataKey="day" tickFormatter={formatDay} tick={{ fill: '#94a3b8', fontSize: 11 }} />
        <YAxis tickFormatter={formatAxisMoney} tick={{ fill: '#94a3b8', fontSize: 11 }} />
        <Tooltip
          labelFormatter={formatDayTooltip}
          formatter={tooltipFormatter}
          contentStyle={{
            backgroundColor: 'rgba(2, 6, 23, 0.95)',
            border: '1px solid rgba(148,163,184,0.24)',
            borderRadius: 14,
          }}
        />
        <Legend verticalAlign="top" height={36} wrapperStyle={{ color: '#cbd5e1', fontSize: '12px' }} />
        <Bar dataKey="ads" stackId="costs" name="Реклама" fill="#f59e0b" radius={[4, 4, 0, 0]} />
        <Bar dataKey="logistics" stackId="costs" name="Логистика" fill="#ef4444" />
        <Bar dataKey="commission" stackId="costs" name="Комиссия" fill="#38bdf8" />
        <Bar dataKey="services" stackId="costs" name="Услуги WB" fill="#14b8a6" />
        <Bar dataKey="withholdings" stackId="costs" name="Удержания" fill="#fb7185" />
        <Bar dataKey="other" stackId="costs" name="Прочее" fill="#a78bfa" />
      </BarChart>
    );

  return (
    <section className="panel section">
      <div className="chart-shell">
        <div className="chart-shell__controls">
          <div className="tab-nav">
            {chartTabs.map((item) => (
              <button
                key={item.key}
                type="button"
                className={`tab-link ${chartType === item.key ? 'tab-link--active' : ''}`}
                onClick={() => setChartType(item.key)}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="chart-shell__controls-right">
            <div className="tab-nav">
              <button
                type="button"
                className={`tab-link ${mode === 'total' ? 'tab-link--active' : ''}`}
                onClick={() => setMode('total')}
              >
                Всего
              </button>
              <button
                type="button"
                className={`tab-link ${mode === 'unit' ? 'tab-link--active' : ''}`}
                onClick={() => setMode('unit')}
              >
                По товарам
              </button>
            </div>

            {chartType === 'expenses' ? (
              <>
                <div className="tab-nav">
                  {expenseTabs.map((item) => (
                    <button
                      key={item.key}
                      type="button"
                      className={`tab-link ${expenseMode === item.key ? 'tab-link--active' : ''}`}
                      onClick={() => setExpenseMode(item.key)}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>

                {expenseMode === 'categories' ? (
                  <div className="tab-nav">
                    {expenseCategories.map((item) => (
                      <button
                        key={item.key}
                        type="button"
                        className={`tab-link ${expenseCategory === item.key ? 'tab-link--active' : ''}`}
                        onClick={() => setExpenseCategory(item.key)}
                      >
                        {item.label}
                      </button>
                    ))}
                  </div>
                ) : null}
              </>
            ) : null}
          </div>
        </div>

        <div className="chart-shell__chart">
          <ResponsiveContainer width="100%" height="100%">
            {chartNode}
          </ResponsiveContainer>
        </div>
      </div>
    </section>
  );
}
