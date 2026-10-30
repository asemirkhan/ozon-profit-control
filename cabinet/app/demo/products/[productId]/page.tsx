import { unstable_noStore as noStore } from 'next/cache';

import { PageHeader } from '@/components/cabinet/PageHeader';
import { getFictionalProduct } from '@/lib/fictional-store';
import { formatMoney } from '@/lib/format';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

type Props = {
  params: { productId: string };
};

function signedMoney(value: number) {
  const formatted = formatMoney(Math.abs(value));
  return value > 0 ? `+${formatted}` : value < 0 ? `−${formatted}` : formatted;
}

function signedCount(value: number) {
  return value > 0 ? `+${value}` : `${value}`;
}

function changeClass(value: number, positiveIsGood = true) {
  if (value === 0) return 'simulation-delta simulation-delta--neutral';
  const good = positiveIsGood ? value > 0 : value < 0;
  return `simulation-delta ${good ? 'simulation-delta--good' : 'simulation-delta--bad'}`;
}

export default async function DemoProductPage({ params }: Props) {
  noStore();
  const data = await getFictionalProduct(params.productId);
  if (!data) {
    return <PageHeader title="Товар не найден" subtitle="Такого товара нет в демонстрационном наборе." />;
  }

  const { product, recommendation, evidence } = data;
  const kpis = [
    { label: 'Заказы', previous: product.prev_orders, current: product.curr_orders, money: false, positiveIsGood: true },
    { label: 'Продано единиц', previous: product.prev_units, current: product.curr_units, money: false, positiveIsGood: true },
    { label: 'Выручка', previous: product.prev_revenue, current: product.curr_revenue, money: true, positiveIsGood: true },
    { label: 'Результат', previous: product.prev_result, current: product.curr_result, money: true, positiveIsGood: true },
  ];
  const rows = [
    ['Реклама', product.prev_ads, product.curr_ads, false],
    ['Логистика', product.prev_logistics, product.curr_logistics, false],
    ['Хранение', product.prev_storage, product.curr_storage, false],
    ['Комиссия', product.prev_commission, product.curr_commission, false],
    ['Все расходы', product.prev_total_expenses, product.curr_total_expenses, false],
    ['Логистика на проданную единицу', product.prev_logistics_per_sold_unit, product.curr_logistics_per_sold_unit, false],
    ['Результат с единицы', product.prev_result_per_unit, product.curr_result_per_unit, true],
  ] as const;
  const actionAvailable = recommendation && !['blocked', 'no_action'].includes(recommendation.status);

  return (
    <div className="page-stack simulation-page demo-page">
      <a href="/demo/products" className="inline-link">← Все товары</a>

      <section className="panel section simulation-surface demo-product-hero">
        <div>
          <PageHeader title={product.name} subtitle={`${product.group} · ${product.vendor_code}`} />
          <p className="demo-period">Июнь 2026 против мая 2026</p>
        </div>
        <span className="demo-badge">Вымышленный товар</span>
      </section>

      <section className="demo-kpi-grid">
        {kpis.map((row) => {
          const difference = row.current - row.previous;
          return (
            <article key={row.label} className="simulation-kpi-card simulation-kpi-card--light">
              <p className="simulation-kpi-card__label">{row.label}</p>
              <p className="simulation-kpi-card__value">{row.money ? formatMoney(row.current) : row.current}</p>
              <p className={changeClass(difference, row.positiveIsGood)}>
                {row.money ? signedMoney(difference) : signedCount(difference)}
              </p>
              <p className="simulation-kpi-card__baseline">
                Май: {row.money ? formatMoney(row.previous) : row.previous}
              </p>
            </article>
          );
        })}
      </section>

      {recommendation ? (
        <section className="panel section simulation-surface demo-decision">
          <div className="demo-decision__problem">
            <span>Что произошло</span>
            <h2>{recommendation.short_problem}</h2>
            <p>{recommendation.confirmed_facts}</p>
          </div>

          <div className="demo-causal-line" aria-label="Причинная цепочка">
            <div>
              <span>Изменение</span>
              <strong>{recommendation.short_problem}</strong>
            </div>
            <span className="demo-causal-line__arrow">→</span>
            <div>
              <span>{evidence?.causal_status === 'proven' ? 'Подтверждённая причина' : 'Что нужно подтвердить'}</span>
              <strong>{recommendation.proven_cause}</strong>
            </div>
            <span className="demo-causal-line__arrow">→</span>
            <div>
              <span>Действие</span>
              <strong>{actionAvailable ? recommendation.action : recommendation.status === 'no_action' ? 'Действие не требуется' : 'Действие заблокировано'}</strong>
            </div>
          </div>

          {actionAvailable ? (
            <div className="demo-recommendation">
              <h3>{recommendation.action}</h3>
              <div className="demo-recommendation__checks">
                <p><span>Проверить через</span><strong>{recommendation.check_period_days} дней</strong></p>
                <p><span>Успех</span><strong>{recommendation.success_criterion}</strong></p>
                <p><span>Если не помогло</span><strong>{recommendation.next_step}</strong></p>
              </div>
            </div>
          ) : null}

          {evidence ? (
            <details className="demo-evidence">
              <summary>Почему система так решила</summary>
              <p>{evidence.evidence_text}</p>
              <dl>
                <div><dt>Источник</dt><dd>{evidence.dimension}</dd></div>
                <div><dt>Покрытие</dt><dd>{evidence.coverage_pct}% данных</dd></div>
                <div><dt>Денежное влияние</dt><dd>{formatMoney(evidence.contribution_rub)}</dd></div>
              </dl>
            </details>
          ) : null}
        </section>
      ) : null}

      <section className="panel section simulation-surface">
        <div className="section-heading">
          <div>
            <h2 className="section-title">Расходы и результат</h2>
            <p className="section-note">Только цифры за два завершённых месяца.</p>
          </div>
        </div>
        <div className="simulation-compare-table">
          <div className="simulation-compare-head">
            <span>Показатель</span><span>Май</span><span>Июнь</span><span>Изменение</span>
          </div>
          {rows.map(([label, previous, current, positiveIsGood]) => {
            const difference = current - previous;
            return (
              <div key={label} className="simulation-compare-row">
                <div className="simulation-compare-row__metric"><strong>{label}</strong></div>
                <div className="simulation-compare-row__value">{formatMoney(previous)}</div>
                <div className="simulation-compare-row__value">{formatMoney(current)}</div>
                <div className={changeClass(difference, positiveIsGood)}>{signedMoney(difference)}</div>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
