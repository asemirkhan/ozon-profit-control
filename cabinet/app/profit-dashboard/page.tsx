import { KpiCard } from '@/components/cabinet/KpiCard';
import { PageHeader } from '@/components/cabinet/PageHeader';
import { getAdsProfitDrivenSnapshot, getAdsSignalSummary } from '@/lib/ads-profit-driven';
import { formatMoney, formatPercent } from '@/lib/format';
import { getProfitDashboardSnapshot } from '@/lib/profit-dashboard';

type Snapshot = NonNullable<Awaited<ReturnType<typeof getProfitDashboardSnapshot>>>;

function average(values: number[]) {
  if (!values.length) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function avgShare<T>(rows: T[], getRevenue: (row: T) => number, getAmount: (row: T) => number) {
  return average(rows.map((row) => {
    const revenue = getRevenue(row);
    if (revenue <= 0) return 0;
    return getAmount(row) / revenue;
  }));
}

function buildCostStructure(snapshot: Snapshot) {
  let acquiring = 0;

  for (const row of snapshot.product_details) {
    const categories = (row.cost_categories || {}) as Record<string, unknown>;
    acquiring += Number(categories.acquiring || 0);
  }

  const other = Math.max(
    0,
    snapshot.summary.wb_cost_total - snapshot.summary.logistics_total - snapshot.summary.commission_total - acquiring
  );

  return [
    { name: 'Реклама', amount: snapshot.summary.ad_spend_total, share: snapshot.summary.ad_share_total },
    { name: 'Логистика', amount: snapshot.summary.logistics_total, share: snapshot.summary.logistics_share_total },
    { name: 'Комиссия', amount: snapshot.summary.commission_total, share: snapshot.summary.commission_share_total },
    { name: 'Эквайринг', amount: acquiring, share: snapshot.summary.revenue_total > 0 ? acquiring / snapshot.summary.revenue_total : null },
    { name: 'Прочее', amount: other, share: snapshot.summary.revenue_total > 0 ? other / snapshot.summary.revenue_total : null },
  ].filter((row) => row.amount > 0);
}

function buildFacts(snapshot: Snapshot) {
  const start = snapshot.economics.daily.slice(0, 7);
  const end = snapshot.economics.daily.slice(-7);
  if (!start.length || !end.length) return [];

  const rows = [
    {
      label: 'Логистика',
      from: avgShare(start, (row) => row.viruchka, (row) => row.logistika),
      to: avgShare(end, (row) => row.viruchka, (row) => row.logistika),
    },
    {
      label: 'Удержания',
      from: avgShare(start, (row) => row.viruchka, (row) => row.shtrafy_i_uderzhaniya),
      to: avgShare(end, (row) => row.viruchka, (row) => row.shtrafy_i_uderzhaniya),
    },
  ];

  return rows
    .filter((row) => row.to - row.from >= 0.015)
    .map((row) => `${row.label} выросла с ${formatPercent(row.from)} до ${formatPercent(row.to)} за 7 дней`);
}

function buildAdsSectionLines(hypothesisCount: number, actionCount: number, top1SpendShare: number | null) {
  const lines: string[] = [];
  const itemWord = (count: number) => (count === 1 ? 'товару' : 'товарам');
  if (actionCount > 0) {
    lines.push(`По ${actionCount} ${itemWord(actionCount)} реклама не окупается.`);
  } else if (hypothesisCount > 0) {
    lines.push(`По ${hypothesisCount} ${itemWord(hypothesisCount)} реклама стала менее эффективной за последние дни.`);
  }

  if (top1SpendShare !== null && top1SpendShare >= 40) {
    lines.push(`Один товар даёт ${top1SpendShare.toFixed(2)}% рекламных расходов.`);
  }

  return lines.slice(0, 2);
}

export default async function ProfitDashboardPage() {
  const [snapshot, adsProfitSnapshot] = await Promise.all([getProfitDashboardSnapshot(), getAdsProfitDrivenSnapshot()]);

  if (!snapshot) return <div />;

  const totalExpenses = snapshot.summary.wb_cost_total + snapshot.summary.ad_spend_total;
  const costStructure = buildCostStructure(snapshot);
  const facts = buildFacts(snapshot);
  const adsSignalSummary = getAdsSignalSummary(adsProfitSnapshot);
  const adsSectionLines = buildAdsSectionLines(
    adsSignalSummary.hypothesisCount,
    adsSignalSummary.actionCount,
    adsSignalSummary.top1SpendShare
  );

  return (
    <div>
      <PageHeader title="Обзор" />

      <section className="panel section">
        <h2 className="m-0 text-lg font-semibold">Деньги</h2>
        <div className="kpi-grid mt-4">
          <KpiCard label="Выручка" value={formatMoney(snapshot.summary.revenue_total)} />
          <KpiCard label="Расходы всего" value={formatMoney(totalExpenses)} />
          <KpiCard label="Прибыль" value={formatMoney(snapshot.summary.operacionnaya_pribyl_total)} />
        </div>
      </section>

      <section className="panel section">
        <h2 className="m-0 text-lg font-semibold">Структура расходов</h2>
        <div className="table-wrap mt-4">
          <table className="table-base">
            <thead>
              <tr>
                <th>Статья</th>
                <th>Сумма</th>
                <th>Доля</th>
              </tr>
            </thead>
            <tbody>
              {costStructure.map((row) => (
                <tr key={row.name}>
                  <td>{row.name}</td>
                  <td>{formatMoney(row.amount)}</td>
                  <td>{formatPercent(row.share)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {adsSignalSummary.hasSignals && adsSectionLines.length ? (
        <section className="panel section">
          <h2 className="m-0 text-lg font-semibold">Реклама</h2>
          <div className="mt-4 space-y-3">
            {adsSectionLines.map((line, index) => (
              <p
                key={`${index}-${line}`}
                className="m-0 rounded-xl border border-amber-300/25 bg-amber-500/10 px-3 py-2 text-sm text-amber-100"
              >
                {line}
              </p>
            ))}
          </div>
        </section>
      ) : null}

      {facts.length ? (
        <section className="panel section">
          <h2 className="m-0 text-lg font-semibold">Факты</h2>
          <div className="mt-4 space-y-3">
            {facts.map((item) => (
              <p key={item} className="m-0 rounded-2xl border border-white/10 bg-slate-950/25 p-4 text-sm text-slate-200">
                {item}
              </p>
            ))}
          </div>
        </section>
      ) : null}
    </div>
  );
}
