import { AdsDynamicsChart } from '@/components/cabinet/AdsDynamicsChart';
import { KpiCard } from '@/components/cabinet/KpiCard';
import { PageHeader } from '@/components/cabinet/PageHeader';
import {
  getAdsProfitDrivenSnapshot,
  getAdsSignalSummary,
  type AdsProfitDrivenProduct,
} from '@/lib/ads-profit-driven';
import { formatCount, formatMoney, formatPercent } from '@/lib/format';
import { getProfitDashboardSnapshot } from '@/lib/profit-dashboard';

type Props = {
  searchParams?: {
    view?: string;
  };
};

type ViewKey = 'signals' | 'all';

function resolveView(value: string | undefined): ViewKey {
  if (value === 'all') return 'all';
  return 'signals';
}

function buildSignalText(signal: AdsProfitDrivenProduct) {
  if (signal.status === 'ACTION') {
    return 'Реклама не окупается';
  }

  if (
    signal.period_7?.cost_per_order !== null &&
    signal.period_7?.cost_per_order !== undefined &&
    signal.period_30?.cost_per_order !== null &&
    signal.period_30?.cost_per_order !== undefined
  ) {
    return `Стоимость заказа выросла с ${formatMoney(signal.period_30.cost_per_order)} до ${formatMoney(signal.period_7.cost_per_order)}`;
  }

  return 'Реклама стала менее эффективной';
}

export default async function AdsPage({ searchParams }: Props) {
  const [snapshot, adsProfitSnapshot] = await Promise.all([getProfitDashboardSnapshot(), getAdsProfitDrivenSnapshot()]);
  const view = resolveView(searchParams?.view);

  if (!snapshot) {
    return (
      <div>
        <PageHeader title="Реклама" />
      </div>
    );
  }

  const appHref = (path: string) => `/cabinet${path}`;
  const adsSignalSummary = getAdsSignalSummary(adsProfitSnapshot);
  const signalRows = (adsProfitSnapshot?.products || []).filter((row) => row.status === 'HYPOTHESIS' || row.status === 'ACTION');
  const allRows = (adsProfitSnapshot?.products || []).filter((row) => row.status !== 'NOISE');
  const filteredRows = view === 'all' ? allRows : signalRows;
  const totalOrders = filteredRows.reduce((sum, row) => sum + (row.period_30?.orders || 0), 0);
  const averageCostPerOrder = totalOrders > 0 ? filteredRows.reduce((sum, row) => sum + (row.period_30?.spend || 0), 0) / totalOrders : null;
  const insights: string[] = [];

  if (adsSignalSummary.hypothesisCount > 0) {
    insights.push(`По ${adsSignalSummary.hypothesisCount} товарам реклама стала менее эффективной за последние дни.`);
  }
  if (adsSignalSummary.top3SpendShare !== null && adsSignalSummary.top3SpendShare >= 70) {
    insights.push(`На top-3 товара приходится ${adsSignalSummary.top3SpendShare.toFixed(2)}% рекламного бюджета.`);
  }

  return (
    <div>
      <PageHeader title="Реклама" />

      <section className="panel section">
        <div className="kpi-grid">
          <KpiCard label="Расход" value={formatMoney(snapshot.ads.summary.spend_total)} />
          <KpiCard label="Доля от выручки" value={formatPercent(snapshot.ads.summary.spend_share_total)} />
          <KpiCard label="Заказы" value={formatCount(totalOrders)} />
          <KpiCard label="Стоимость заказа" value={averageCostPerOrder !== null ? formatMoney(averageCostPerOrder) : ''} />
        </div>
      </section>

      {insights.length ? (
        <section className="panel section">
          <h2 className="m-0 text-lg font-semibold">Что изменилось</h2>
          <div className="mt-4 space-y-3">
            {insights.map((line) => (
              <div key={line} className="signal-line">
                <span className="signal-line__tag">Реклама</span>
                <span className="signal-line__text">{line}</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <AdsDynamicsChart
        daily={snapshot.ads.daily.map((row) => ({
          day: row.day,
          spend: row.reklama,
        }))}
      />

      <section className="panel section">
        <div className="flex flex-wrap gap-2">
          <a href={appHref('/ads?view=signals')} className={`badge ${view === 'signals' ? 'badge-ok' : ''}`}>
            Сигналы
          </a>
          <a href={appHref('/ads?view=all')} className={`badge ${view === 'all' ? 'badge-ok' : ''}`}>
            Все товары
          </a>
        </div>
        <div className="table-wrap mt-4">
          <table className="table-base">
            <thead>
              <tr>
                <th>Товар</th>
                <th>Расход</th>
                <th>Заказы</th>
                <th>Стоимость заказа</th>
                <th>Прибыль до рекламы</th>
                <th>Прибыль после рекламы</th>
                <th>Статус</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((row) => (
                <tr key={row.sku}>
                  <td>
                    {row.product_name}
                    {row.status === 'HYPOTHESIS' || row.status === 'ACTION' ? (
                      <div className="mt-1 text-xs text-slate-400">{buildSignalText(row)}</div>
                    ) : null}
                  </td>
                  <td>{row.period_30 ? formatMoney(row.period_30.spend) : ''}</td>
                  <td>{row.period_30 ? formatCount(row.period_30.orders) : ''}</td>
                  <td>{row.period_30?.cost_per_order !== null && row.period_30?.cost_per_order !== undefined ? formatMoney(row.period_30.cost_per_order) : ''}</td>
                  <td>
                    {row.period_30?.profit_per_order_before_ads !== null && row.period_30?.profit_per_order_before_ads !== undefined
                      ? formatMoney(row.period_30.profit_per_order_before_ads)
                      : ''}
                  </td>
                  <td>
                    {row.period_30?.profit_per_order_after_ads !== null && row.period_30?.profit_per_order_after_ads !== undefined
                      ? formatMoney(row.period_30.profit_per_order_after_ads)
                      : ''}
                  </td>
                  <td>{row.status === 'HYPOTHESIS' || row.status === 'ACTION' ? row.status : 'OK'}</td>
                  <td>
                    <a href={appHref(`/products/${row.sku}`)} className="text-link">
                      Открыть
                    </a>
                  </td>
                </tr>
              ))}
              {filteredRows.length === 0 ? (
                <tr>
                  <td colSpan={8} className="text-slate-400">
                    Для этого режима товары не найдены.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
