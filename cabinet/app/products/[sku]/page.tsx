import { unstable_noStore as noStore } from 'next/cache';

import { EconomicsWorkbench } from '@/components/cabinet/EconomicsWorkbench';
import { PageHeader } from '@/components/cabinet/PageHeader';
import { formatCount, formatMoney } from '@/lib/format';
import { getCabinetProduct } from '@/lib/profit-control-cabinet';
import { getSimulationProductDetail } from '@/lib/profit-control-simulation';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

type Props = {
  params: {
    sku: string;
  };
};

const decimalFmt = new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 2,
  minimumFractionDigits: 0,
});

function formatMoneyDelta(value: number) {
  const abs = formatMoney(Math.abs(value));
  if (value > 0) return `+${abs}`;
  if (value < 0) return `−${abs}`;
  return abs;
}

function formatCountDelta(value: number) {
  const abs = formatCount(Math.abs(value));
  if (value > 0) return `+${abs}`;
  if (value < 0) return `−${abs}`;
  return abs;
}

function formatRatio(value: number | null) {
  if (value === null) return '—';
  return decimalFmt.format(value);
}

function formatRatioDelta(value: number | null) {
  if (value === null) return '—';
  const abs = decimalFmt.format(Math.abs(value));
  if (value > 0) return `+${abs}`;
  if (value < 0) return `−${abs}`;
  return abs;
}

function deltaClass(value: number | null, positiveIsGood: boolean) {
  if (value === null || value === 0) return 'simulation-delta simulation-delta--neutral';
  const isGood = positiveIsGood ? value > 0 : value < 0;
  return `simulation-delta ${isGood ? 'simulation-delta--good' : 'simulation-delta--bad'}`;
}

export default async function ProductDetailPage({ params }: Props) {
  noStore();
  const product = await getSimulationProductDetail(params.sku);

  if (!product) {
    return (
      <div className="page-stack simulation-page simulation-page--product">
        <PageHeader title="Карточка товара" subtitle="По этому товару нет подтвержденных данных для новой карточки." />
      </div>
    );
  }

  const graphProduct = product.graphPackProductId ? await getCabinetProduct(product.graphPackProductId) : null;
  const depthByKey = new Map(product.depthSections.map((section) => [section.key, section]));
  const recommendation = product.recommendationDetails;
  const hasProblem = Boolean(
    recommendation
      && !['no_action', 'positive_status'].includes(recommendation.decision),
  );
  const hasAction = Boolean(recommendation?.action);

  return (
    <div className="page-stack simulation-page simulation-page--product">
      <a href="/products" className="inline-link simulation-back-link">
        ← Назад к товарам
      </a>

      <section className="panel section simulation-surface simulation-product-hero">
        <div className="simulation-product-hero__media">
          {product.imageUrl ? (
            <img className="simulation-product-hero__image" src={product.imageUrl} alt={product.productName} />
          ) : (
            <div className="simulation-product-hero__fallback">{product.productName.slice(0, 1)}</div>
          )}
        </div>
        <div className="simulation-product-hero__copy">
          <PageHeader title={product.productName} subtitle={product.periodLabel} />
          <div className="simulation-product-hero__meta">
            <span>Маркетплейс: Wildberries</span>
            <span>SKU: {product.sku}</span>
            <span>Артикул: {product.offerId}</span>
            <span>Группа: {product.groupName}</span>
          </div>
          {hasProblem && recommendation ? (
            <div className="simulation-problem-tags">
              <span className="soft-badge soft-badge--warning">{recommendation.title}</span>
            </div>
          ) : null}
        </div>
      </section>

      <section className="simulation-kpi-grid simulation-kpi-grid--product">
        <article className="simulation-kpi-card simulation-kpi-card--light">
          <p className="simulation-kpi-card__label">Выручка</p>
          <p className="simulation-kpi-card__value">{formatMoney(product.revenueCurr)}</p>
          <p className={deltaClass(product.revenueDelta, true)}>{formatMoneyDelta(product.revenueDelta)}</p>
          <p className="simulation-kpi-card__baseline">Было: {formatMoney(product.revenuePrev)}</p>
        </article>

        <article className="simulation-kpi-card simulation-kpi-card--light">
          <p className="simulation-kpi-card__label">Результат</p>
          <p className="simulation-kpi-card__value">{formatMoney(product.resultCurr)}</p>
          <p className={deltaClass(product.resultDelta, true)}>{formatMoneyDelta(product.resultDelta)}</p>
          <p className="simulation-kpi-card__baseline">Было: {formatMoney(product.resultPrev)}</p>
        </article>

        <article className="simulation-kpi-card simulation-kpi-card--light">
          <p className="simulation-kpi-card__label">Заказы</p>
          <p className="simulation-kpi-card__value">{formatCount(product.ordersCurr)}</p>
          <p className={deltaClass(product.ordersDelta, true)}>{formatCountDelta(product.ordersDelta)}</p>
          <p className="simulation-kpi-card__baseline">Было: {formatCount(product.ordersPrev)}</p>
        </article>

        <article className="simulation-kpi-card simulation-kpi-card--light">
          <p className="simulation-kpi-card__label">Продано единиц</p>
          <p className="simulation-kpi-card__value">{formatCount(product.salesUnitsCurr)}</p>
          <p className={deltaClass(product.salesUnitsDelta, true)}>{formatCountDelta(product.salesUnitsDelta)}</p>
          <p className="simulation-kpi-card__baseline">Было: {formatCount(product.salesUnitsPrev)}</p>
        </article>

        <article className="simulation-kpi-card simulation-kpi-card--light">
          <p className="simulation-kpi-card__label">Состоялось заказов</p>
          <p className="simulation-kpi-card__value">{formatCount(product.fulfilledOrdersCurr)}</p>
          <p className={deltaClass(product.fulfilledOrdersCurr - product.fulfilledOrdersPrev, true)}>
            {formatCountDelta(product.fulfilledOrdersCurr - product.fulfilledOrdersPrev)}
          </p>
          <p className="simulation-kpi-card__baseline">Было: {formatCount(product.fulfilledOrdersPrev)}</p>
        </article>
      </section>

      <section className="panel section simulation-surface">
        <div className="section-heading">
          <div>
            <h2 className="section-title">Как заказы превратились в продажи</h2>
            <p className="section-note">Заказы, отмены, состоявшиеся заказы и проданные единицы за два месяца.</p>
          </div>
        </div>
        <div className="simulation-compare-table">
          <div className="simulation-compare-head">
            <span>Показатель</span>
            <span>{product.previousPeriodLabel}</span>
            <span>{product.currentPeriodLabel}</span>
            <span>Изменение</span>
          </div>
          {[
            ['Заказы', product.ordersPrev, product.ordersCurr, true],
            ['Отмены', product.cancelledOrdersPrev, product.cancelledOrdersCurr, false],
            ['Состоялось заказов', product.fulfilledOrdersPrev, product.fulfilledOrdersCurr, true],
            ['Продано единиц', product.salesUnitsPrev, product.salesUnitsCurr, true],
          ].map(([label, previous, current, positiveIsGood]) => {
            const before = Number(previous);
            const after = Number(current);
            return (
              <div key={String(label)} className="simulation-compare-row">
                <div className="simulation-compare-row__metric"><strong>{String(label)}</strong></div>
                <div className="simulation-compare-row__value">{formatCount(before)}</div>
                <div className="simulation-compare-row__value">{formatCount(after)}</div>
                <div className={deltaClass(after - before, Boolean(positiveIsGood))}>{formatCountDelta(after - before)}</div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="panel section simulation-surface">
        <div className="section-heading">
          <div>
            <h2 className="section-title">Расходы и доходы</h2>
            <p className="section-note page-subtle">Май и июнь 2026 года.</p>
          </div>
        </div>
        <div className="simulation-compare-table">
          <div className="simulation-compare-head">
            <span>Показатель</span>
            <span>Было</span>
            <span>Стало</span>
            <span>Изменение</span>
          </div>
          {product.expenseLines.map((line) => {
            const depth = depthByKey.get(line.key as 'ads' | 'logistics' | 'storage' | 'other');
            const positiveIsGood = line.key === 'revenue';
            return (
              <div key={line.key} className="simulation-compare-block">
                <div className="simulation-compare-row">
                  <div className="simulation-compare-row__metric">
                    <strong>{line.label}</strong>
                    {depth?.rows.length ? <span>Есть детализация</span> : null}
                  </div>
                  <div className="simulation-compare-row__value">{formatMoney(line.prev)}</div>
                  <div className="simulation-compare-row__value">{formatMoney(line.curr)}</div>
                  <div className={deltaClass(line.delta, positiveIsGood)}>{formatMoneyDelta(line.delta)}</div>
                </div>
                {depth?.rows.length ? (
                  <details className="simulation-compare-details">
                    <summary>Детализация</summary>
                    <div className="simulation-compare-nested">
                      {depth.rows.map((row) => (
                        <div key={`${line.key}-${row.label}`} className="simulation-compare-nested__row">
                          <span>{row.label}</span>
                          <span>{row.prev}</span>
                          <span>{row.curr}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                ) : null}
              </div>
            );
          })}
        </div>
      </section>

      <section className="panel section simulation-surface">
        <div className="section-heading">
          <div>
            <h2 className="section-title">Удельная экономика</h2>
            <p className="section-note page-subtle">Расходы и результат на одну проданную единицу.</p>
          </div>
        </div>
        <div className="simulation-compare-table">
          <div className="simulation-compare-head">
            <span>Показатель</span>
            <span>Было</span>
            <span>Стало</span>
            <span>Изменение</span>
          </div>
          {product.unitEconomicsLines.map((line) => {
            const positiveIsGood = line.key === 'result_per_unit';
            return (
              <div key={line.key} className="simulation-compare-row">
                <div className="simulation-compare-row__metric">
                  <strong>{line.label}</strong>
                </div>
                <div className="simulation-compare-row__value">{formatMoney(line.prev)}</div>
                <div className="simulation-compare-row__value">{formatMoney(line.curr)}</div>
                <div className={deltaClass(line.delta, positiveIsGood)}>{formatMoneyDelta(line.delta)}</div>
              </div>
            );
          })}
        </div>
      </section>

      <section className="simulation-store-grid">
        <article className="panel section simulation-surface">
          <div className="section-heading">
            <div>
              <h2 className="section-title">Ключевые проблемы</h2>
            </div>
          </div>
          <div className="simulation-action-list">
            {hasProblem && recommendation ? (
                <div className="simulation-action-item">
                  <span className="simulation-action-item__index">1</span>
                  <div className="simulation-action-item__copy">
                    <strong>{recommendation.short_problem}</strong>
                    <p className="simulation-action-item__detail">{recommendation.cause}</p>
                    <details className="demo-evidence">
                      <summary>Подтверждающие цифры</summary>
                      <p>{recommendation.full_problem}</p>
                    </details>
                  </div>
                </div>
            ) : (
              <div className="simulation-action-item simulation-action-item--empty">
                <div className="simulation-action-item__copy">
                  <strong>Подтвержденных проблем не найдено</strong>
                </div>
              </div>
            )}
          </div>
        </article>

        <article className="panel section simulation-surface">
          <div className="section-heading">
            <div>
              <h2 className="section-title">Рекомендации</h2>
            </div>
          </div>
          <div className="simulation-action-list">
            {recommendation && hasAction ? (
              <div className="simulation-action-item simulation-action-item--ok">
                <span className="simulation-action-item__index">1</span>
                <div className="simulation-action-item__copy">
                  {recommendation.action ? (
                    <p className="simulation-action-item__detail"><b>Что сделать:</b> {recommendation.action}</p>
                  ) : null}
                  {recommendation.check_period ? (
                    <p className="simulation-action-item__detail"><b>Когда проверить:</b> {recommendation.check_period}</p>
                  ) : null}
                  {recommendation.success_criterion ? (
                    <p className="simulation-action-item__detail"><b>Признак успеха:</b> {recommendation.success_criterion}</p>
                  ) : null}
                  {recommendation.next_step ? (
                    <p className="simulation-action-item__detail"><b>Если не помогло:</b> {recommendation.next_step}</p>
                  ) : null}
                </div>
              </div>
            ) : (
              <div className="simulation-action-item simulation-action-item--empty">
                <div className="simulation-action-item__copy">
                  <strong>Подтвержденных действий пока нет</strong>
                </div>
              </div>
            )}
          </div>
        </article>
      </section>

      <details id="product-graphs" className="panel section simulation-surface simulation-collapsible">
        <summary>Графики товара</summary>
        {graphProduct ? (
          <div className="simulation-graphs">
            <EconomicsWorkbench
              title="Графики товара"
              incomeRows={graphProduct.incomeRows}
              expenseRows={graphProduct.expenseRows}
              storeRegions={graphProduct.geoHotspots}
              storeWarehouses={graphProduct.warehouseHotspots}
              products={[]}
            />
          </div>
        ) : (
          <div className="simulation-empty-note">Для этого товара в текущем графическом pack нет отдельного слоя детализации.</div>
        )}
      </details>
    </div>
  );
}
