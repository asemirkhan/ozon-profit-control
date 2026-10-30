import { unstable_noStore as noStore } from 'next/cache';

import { PageHeader } from '@/components/cabinet/PageHeader';
import { getFictionalStorePack } from '@/lib/fictional-store';
import { formatMoney } from '@/lib/format';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const statusLabels: Record<string, string> = {
  action: 'Нужно действие',
  blocked: 'Недостаточно данных',
  monitor: 'Наблюдение',
  signal: 'Оперативный сигнал',
  controlled_test: 'Контрольный опыт',
  diagnostic_check: 'Нужно уточнение',
  no_action: 'Без проблемы',
};

export default async function DemoProductsPage() {
  noStore();
  const pack = await getFictionalStorePack();
  const recommendationByProduct = new Map(pack.recommendations.map((row) => [row.product_id, row]));
  const rows = [...pack.products].sort((a, b) => {
    const aAction = a.recommendation_status === 'action' ? 1 : 0;
    const bAction = b.recommendation_status === 'action' ? 1 : 0;
    return bAction - aAction || b.financial_impact - a.financial_impact;
  });

  return (
    <div className="page-stack simulation-page demo-page">
      <section className="panel section simulation-surface">
        <div className="simulation-page-heading">
          <PageHeader
            title="Товары LUNARO HOME"
            subtitle="40 вымышленных товаров для проверки реальных деловых ситуаций"
          />
          <span className="demo-badge">Демонстрационные данные</span>
        </div>
      </section>

      <section className="panel section simulation-surface">
        <div className="demo-product-list">
          {rows.map((product) => {
            const recommendation = recommendationByProduct.get(product.product_id);
            return (
              <a key={product.product_id} href={`/demo/products/${product.product_id}`} className="demo-product-row">
                <div className="demo-product-row__name">
                  <strong>{product.name}</strong>
                  <span>{product.group} · {product.vendor_code}</span>
                </div>
                <div className="demo-product-row__metric">
                  <span>Продано</span>
                  <strong>{product.prev_units} → {product.curr_units} шт.</strong>
                </div>
                <div className="demo-product-row__metric">
                  <span>Результат</span>
                  <strong>{formatMoney(product.curr_result)}</strong>
                </div>
                <div className="demo-product-row__problem">
                  <span className={`demo-status demo-status--${product.recommendation_status}`}>
                    {statusLabels[product.recommendation_status] ?? product.recommendation_status}
                  </span>
                  <p>{recommendation?.short_problem ?? 'Существенных изменений нет'}</p>
                </div>
                <span className="inline-link">Открыть →</span>
              </a>
            );
          })}
        </div>
      </section>
    </div>
  );
}
