import { unstable_noStore as noStore } from 'next/cache';

import { PageHeader } from '@/components/cabinet/PageHeader';
import {
  getSimulationProblemProductsPageData,
  type ProblemFilterKey,
} from '@/lib/profit-control-simulation';
import { formatMoney } from '@/lib/format';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

type Props = {
  searchParams?: {
    filter?: string;
  };
};

function resolveFilter(value: string | undefined): ProblemFilterKey {
  const allowed: ProblemFilterKey[] = [
    'all',
    'sales_units_down',
    'orders_down',
    'avg_price_down',
    'cost_per_unit_up',
    'ads_spend_up',
    'ad_efficiency_down',
    'logistics_up',
    'storage_up',
    'stock_limited',
    'rapid_7d_drop',
    'sharp_1d_signal',
  ];
  return allowed.includes((value ?? 'all') as ProblemFilterKey) ? ((value ?? 'all') as ProblemFilterKey) : 'all';
}

function formatDelta(value: number) {
  const abs = formatMoney(Math.abs(value));
  if (value > 0) return `+${abs}`;
  if (value < 0) return `−${abs}`;
  return abs;
}

function deltaClass(value: number) {
  if (value === 0) return 'simulation-delta simulation-delta--neutral';
  return `simulation-delta ${value > 0 ? 'simulation-delta--good' : 'simulation-delta--bad'}`;
}

export default async function ProductsPage({ searchParams }: Props) {
  noStore();
  const filter = resolveFilter(searchParams?.filter);
  const data = await getSimulationProblemProductsPageData(filter);

  return (
    <div className="page-stack simulation-page simulation-page--products">
      <PageHeader
        title="Проблемные товары"
        subtitle={`Период: ${data.periodLabel}`}
      />

      <section className="panel section simulation-surface">
        <div className="simulation-summary-strip">
          <div className="simulation-summary-card">
            <span className="simulation-summary-card__label">Товаров с подтверждёнными изменениями</span>
            <strong className="simulation-summary-card__value">{data.rows.length}</strong>
            <p className="simulation-summary-card__note">По выбранному виду изменения</p>
          </div>
          <div className="simulation-summary-card">
            <span className="simulation-summary-card__label">Данные по</span>
            <strong className="simulation-summary-card__value">{data.confirmedThrough}</strong>
          </div>
        </div>
      </section>

      <section className="panel section simulation-surface">
        <div className="chip-row chip-row--scroll">
          {data.filterOptions.map((item) => (
            <a
              key={item.key}
              href={`/products?filter=${item.key}`}
              className={`workbench-chip ${filter === item.key ? 'workbench-chip--active' : ''}`}
            >
              {item.label}
            </a>
          ))}
        </div>
      </section>

      <section className="panel section simulation-surface">
        <div className="simulation-problem-table">
          <div className="simulation-problem-table__head">
            <span>Товар</span>
            <span>Группа</span>
            <span>Результат</span>
            <span>Динамика</span>
            <span>Признаки проблемы</span>
            <span />
          </div>

          {data.rows.map((row) => (
            <div key={row.normalizedProductKey} className="simulation-problem-table__row">
              <div className="simulation-problem-table__product">
                <strong>{row.productName}</strong>
                <span>{row.sku}</span>
              </div>
              <span>{row.groupName}</span>
              <div className="simulation-problem-table__metric">
                <strong>{formatMoney(row.resultCurr)}</strong>
                <span>Текущий результат</span>
              </div>
              <div className="simulation-problem-table__metric">
                <strong className={deltaClass(row.resultDelta)}>{formatDelta(row.resultDelta)}</strong>
                <span>{row.resultDelta < 0 ? 'просадка к прошлому окну' : 'рост к прошлому окну'}</span>
              </div>
              <div className="simulation-problem-table__tags">
                {row.problemLabels.slice(0, 2).map((label) => (
                  <span key={label} className="soft-badge">
                    {label}
                  </span>
                ))}
                {row.problemLabels.length > 2 ? (
                  <span className="soft-badge">+{row.problemLabels.length - 2} признака</span>
                ) : null}
                {row.strong7d ? <span className="soft-badge soft-badge--warning">Ухудшение за 7 дней</span> : null}
                {row.strong1d ? <span className="soft-badge soft-badge--warning">Отклонение за день</span> : null}
              </div>
              <a href={row.href} className="inline-link">
                Открыть карточку
              </a>
            </div>
          ))}

          {data.rows.length === 0 ? (
            <div className="simulation-empty-note">По выбранному фильтру сейчас нет товаров.</div>
          ) : null}
        </div>
      </section>
    </div>
  );
}
