import { KpiCard } from '@/components/cabinet/KpiCard';
import { PageHeader } from '@/components/cabinet/PageHeader';
import { WbCostShareChart } from '@/components/cabinet/WbCostShareChart';
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

function buildStructure(snapshot: Snapshot) {
  let logistics = 0;
  let commission = 0;
  let acquiring = 0;
  let storage = 0;
  let returns = 0;
  let other = 0;

  for (const row of snapshot.product_details) {
    const categories = (row.cost_categories || {}) as Record<string, unknown>;
    logistics += Number(categories.direct_logistics || 0) + Number(categories.warehouse_logistics_tail || 0);
    commission += Number(categories.commission_wb || 0);
    acquiring += Number(categories.acquiring || 0);
    storage += Number(categories.storage || 0);
    returns += Number(categories.reverse_logistics || 0);
    other += Number(categories.other_obligatory || 0) + Number(categories.withholdings || 0) + Number(categories.unidentified || 0);
  }

  return [
    { name: 'Логистика', amount: logistics, share: snapshot.summary.revenue_total > 0 ? logistics / snapshot.summary.revenue_total : null },
    { name: 'Комиссия', amount: commission, share: snapshot.summary.revenue_total > 0 ? commission / snapshot.summary.revenue_total : null },
    { name: 'Эквайринг', amount: acquiring, share: snapshot.summary.revenue_total > 0 ? acquiring / snapshot.summary.revenue_total : null },
    { name: 'Хранение', amount: storage, share: snapshot.summary.revenue_total > 0 ? storage / snapshot.summary.revenue_total : null },
    { name: 'Возвраты', amount: returns, share: snapshot.summary.revenue_total > 0 ? returns / snapshot.summary.revenue_total : null },
    { name: 'Прочее', amount: other, share: snapshot.summary.revenue_total > 0 ? other / snapshot.summary.revenue_total : null },
  ].filter((row) => row.amount > 0);
}

function buildChartRows(snapshot: Snapshot) {
  const economicsByDay = new Map(snapshot.economics.daily.map((row) => [row.day, row]));

  return snapshot.wb_costs.daily
    .map((row) => {
      const economics = economicsByDay.get(row.day);
      const revenue = economics?.viruchka || 0;
      if (revenue <= 0) return null;
      return {
        day: row.day,
        totalShare: row.wb_rashody / revenue,
        logisticsShare: row.logistika / revenue,
        commissionShare: row.komissiya / revenue,
        servicesShare: row.platnye_uslugi / revenue,
        withholdingsShare: row.shtrafy_i_uderzhaniya / revenue,
      };
    })
    .filter((row): row is NonNullable<typeof row> => Boolean(row));
}

function buildAnomalies(snapshot: Snapshot) {
  const daily = buildChartRows(snapshot);
  const start = daily.slice(0, 7);
  const end = daily.slice(-7);
  if (!start.length || !end.length) return [];

  const rows = [
    { label: 'Логистика', from: average(start.map((row) => row.logisticsShare || 0)), to: average(end.map((row) => row.logisticsShare || 0)) },
    { label: 'Услуги', from: average(start.map((row) => row.servicesShare || 0)), to: average(end.map((row) => row.servicesShare || 0)) },
    { label: 'Удержания', from: average(start.map((row) => row.withholdingsShare || 0)), to: average(end.map((row) => row.withholdingsShare || 0)) },
    { label: 'Расходы WB', from: average(start.map((row) => row.totalShare || 0)), to: average(end.map((row) => row.totalShare || 0)) },
  ];

  return rows
    .filter((row) => row.to - row.from >= 0.015)
    .map((row) => `${row.label} выросла с ${formatPercent(row.from)} до ${formatPercent(row.to)} за 7 дней`);
}

export default async function WbCostsPage() {
  const snapshot = await getProfitDashboardSnapshot();

  if (!snapshot) {
    return (
      <div>
        <PageHeader title="Расходы WB" />
      </div>
    );
  }

  const structure = buildStructure(snapshot);
  const chartRows = buildChartRows(snapshot);
  const anomalies = buildAnomalies(snapshot);

  return (
    <div>
      <PageHeader title="Расходы WB" />

      <section className="panel section">
        <div className="kpi-grid">
          <KpiCard label="Общие расходы" value={formatMoney(snapshot.summary.wb_cost_total)} />
          <KpiCard label="Доля от выручки" value={formatPercent(snapshot.summary.wb_cost_share_total)} />
        </div>
      </section>

      <section className="panel section">
        <h2 className="m-0 text-lg font-semibold">Структура</h2>
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
              {structure.map((row) => (
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

      <WbCostShareChart daily={chartRows} />

      {anomalies.length ? (
        <section className="panel section">
          <h2 className="m-0 text-lg font-semibold">Аномалии</h2>
          <div className="mt-4 space-y-3">
            {anomalies.map((item) => (
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
