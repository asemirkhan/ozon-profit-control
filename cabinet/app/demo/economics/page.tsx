import { unstable_noStore as noStore } from 'next/cache';

import { PageHeader } from '@/components/cabinet/PageHeader';
import { getFictionalStorePack } from '@/lib/fictional-store';
import { formatMoney } from '@/lib/format';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

function delta(value: number) {
  const formatted = formatMoney(Math.abs(value));
  return value > 0 ? `+${formatted}` : value < 0 ? `−${formatted}` : formatted;
}

function moneyClass(value: number, positiveIsGood = true) {
  if (value === 0) return 'simulation-delta simulation-delta--neutral';
  const good = positiveIsGood ? value > 0 : value < 0;
  return `simulation-delta ${good ? 'simulation-delta--good' : 'simulation-delta--bad'}`;
}

export default async function DemoEconomicsPage() {
  noStore();
  const pack = await getFictionalStorePack();
  const previousExpenses = pack.store.previous_revenue - pack.store.previous_result;
  const currentExpenses = pack.store.current_revenue - pack.store.current_result;
  const metrics = [
    { label: 'Выручка', previous: pack.store.previous_revenue, current: pack.store.current_revenue, positiveIsGood: true },
    { label: 'Расходы', previous: previousExpenses, current: currentExpenses, positiveIsGood: false },
    { label: 'Результат', previous: pack.store.previous_result, current: pack.store.current_result, positiveIsGood: true },
  ];
  const growth = [...pack.products].filter((row) => row.result_delta > 0).sort((a, b) => b.result_delta - a.result_delta).slice(0, 5);
  const decline = [...pack.products].filter((row) => row.result_delta < 0).sort((a, b) => a.result_delta - b.result_delta).slice(0, 5);
  const actions = pack.recommendations
    .filter((row) => row.status === 'action')
    .map((row) => ({
      ...row,
      product: pack.products.find((product) => product.product_id === row.product_id)!,
    }))
    .sort((a, b) => b.product.financial_impact - a.product.financial_impact)
    .slice(0, 3);

  return (
    <div className="page-stack simulation-page demo-page">
      <section className="panel section simulation-surface simulation-period-card">
        <div className="simulation-page-heading">
          <PageHeader title="LUNARO HOME" subtitle="Вымышленный магазин домашнего текстиля" />
          <span className="demo-badge">Демонстрационные данные</span>
        </div>
        <div className="simulation-period-toolbar">
          <div className="simulation-period-toolbar__copy">
            <span>Сравнение</span>
            <strong>июнь 2026 / май 2026</strong>
            <small>Два завершённых календарных месяца</small>
          </div>
          <div className="demo-period-note">Последний полный день: 25.07.2026</div>
        </div>
      </section>

      <section className="simulation-kpi-grid">
        {metrics.map((metric) => {
          const change = metric.current - metric.previous;
          return (
            <article key={metric.label} className="simulation-kpi-card simulation-kpi-card--light">
              <p className="simulation-kpi-card__label">{metric.label}</p>
              <p className="simulation-kpi-card__value">{formatMoney(metric.current)}</p>
              <p className={moneyClass(change, metric.positiveIsGood)}>{delta(change)}</p>
              <p className="simulation-kpi-card__baseline">Май: {formatMoney(metric.previous)}</p>
            </article>
          );
        })}
      </section>

      <section className="simulation-store-grid">
        {[
          { title: 'Главный рост', rows: growth, good: true },
          { title: 'Главные потери', rows: decline, good: false },
        ].map((group) => (
          <article key={group.title} className="panel section simulation-surface">
            <div className="section-heading">
              <h2 className="section-title">{group.title}</h2>
            </div>
            <div className="demo-ranking">
              {group.rows.map((row, index) => (
                <a key={row.product_id} href={`/demo/products/${row.product_id}`} className="demo-ranking__row">
                  <span className="demo-ranking__index">{index + 1}</span>
                  <span className="demo-ranking__name">
                    <strong>{row.name}</strong>
                    <small>{row.group}</small>
                  </span>
                  <strong className={moneyClass(row.result_delta)}>{delta(row.result_delta)}</strong>
                </a>
              ))}
            </div>
          </article>
        ))}
      </section>

      <section className="panel section simulation-surface">
        <div className="section-heading demo-section-heading">
          <div>
            <h2 className="section-title">Что требует действия</h2>
            <p className="section-note">Три наибольших подтверждённых денежных влияния.</p>
          </div>
          <a href="/demo/products" className="inline-link">Все товары →</a>
        </div>
        <div className="demo-actions">
          {actions.map((row, index) => (
            <a key={row.product_id} href={`/demo/products/${row.product_id}`} className="demo-action-card">
              <span className="demo-action-card__index">{index + 1}</span>
              <div>
                <strong>{row.short_problem}</strong>
                <p>{row.action}</p>
                <small>{row.product.name} · влияние {formatMoney(row.product.financial_impact)}</small>
              </div>
            </a>
          ))}
        </div>
      </section>
    </div>
  );
}
