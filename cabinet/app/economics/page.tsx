import { AttentionStatePanel } from '@/components/cabinet/AttentionStatePanel';
import { KpiCard } from '@/components/cabinet/KpiCard';
import { OwnerDashboardCharts } from '@/components/cabinet/OwnerDashboardCharts';
import { getAdsProfitDrivenSnapshot } from '@/lib/ads-profit-driven';
import { buildStoreAttentionState } from '@/lib/attention-engine';
import { resolveEstimatedCogs } from '@/lib/cogs';
import { formatMoney } from '@/lib/format';
import { getProfitDashboardSnapshot } from '@/lib/profit-dashboard';

type Snapshot = NonNullable<Awaited<ReturnType<typeof getProfitDashboardSnapshot>>>;

const shortMonth = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

function formatPeriodRange(startValue: string, endValue: string) {
  const start = new Date(`${startValue}T00:00:00`);
  const end = new Date(`${endValue}T00:00:00`);
  if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return null;

  return `${start.getDate()} ${shortMonth[start.getMonth()]} — ${end.getDate()} ${shortMonth[end.getMonth()]}`;
}

function safeDiv(numerator: number, denominator: number) {
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator <= 0) return null;
  return numerator / denominator;
}

export default async function EconomicsPage() {
  const [snapshot, adsProfitSnapshot] = await Promise.all([getProfitDashboardSnapshot(), getAdsProfitDrivenSnapshot()]);

  if (!snapshot) {
    return <div />;
  }

  const operationalCosts = snapshot.summary.wb_cost_total;
  const estimatedCostOfGoods = resolveEstimatedCogs({
    revenue: snapshot.summary.revenue_total,
    operatingProfit: snapshot.summary.operacionnaya_pribyl_total,
    profitAfterCogs: snapshot.summary.pribyl_posle_sebestoimosti_total,
  }).amount;
  const averageCheck = snapshot.summary.orders_total ? snapshot.summary.revenue_total / snapshot.summary.orders_total : null;
  const periodLabel = formatPeriodRange(snapshot.period_start, snapshot.period_end);
  const attentionState = buildStoreAttentionState(snapshot, adsProfitSnapshot);
  const averageProfitPerOrder =
    snapshot.summary.orders_total && snapshot.summary.orders_total > 0
      ? safeDiv(snapshot.summary.operacionnaya_pribyl_total, snapshot.summary.orders_total)
      : null;

  return (
    <div>
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="page-title title-with-icon">
            <span className="title-with-icon__glyph" aria-hidden="true">
              📊
            </span>
            <span>Экономика магазина</span>
          </h1>
        </div>
        {periodLabel ? <div className="glass-pill">{periodLabel}</div> : null}
      </header>

      <section className="panel section">
        <div className="kpi-grid">
          <KpiCard label="Выручка" value={formatMoney(snapshot.summary.revenue_total)} />
          <KpiCard label="Себестоимость" value={formatMoney(estimatedCostOfGoods)} hint="(оценка)" />
          <KpiCard label="Операционные расходы" value={formatMoney(operationalCosts)} />
          <KpiCard label="Реклама" value={formatMoney(snapshot.summary.ad_spend_total)} />
        </div>

        <div className="kpi-grid mt-4">
          <KpiCard label="Прибыль после рекламы" value={formatMoney(snapshot.summary.operacionnaya_pribyl_total)} />
          <KpiCard
            label="Маржа"
            value={
              snapshot.summary.operacionnaya_margin_total !== null
                ? `${Math.round(snapshot.summary.operacionnaya_margin_total * 100)}%`
                : ''
            }
          />
          <KpiCard label="Средний чек" value={averageCheck !== null ? formatMoney(averageCheck) : ''} />
          <KpiCard label="Прибыль с заказа" value={averageProfitPerOrder !== null ? formatMoney(averageProfitPerOrder) : ''} />
        </div>
      </section>

      <AttentionStatePanel title="Состояние бизнеса" tone={attentionState.tone} sections={attentionState.sections} />

      <OwnerDashboardCharts daily={snapshot.economics.daily} />
    </div>
  );
}
