import { formatMoney } from '@/lib/format';
import type { ExpenseBreakdownRow } from '@/lib/profit-control-cabinet';

type Props = {
  title: string;
  subtitle?: string;
  rows: ExpenseBreakdownRow[];
  emptyText?: string;
};

const GROUP_LABELS: Record<string, string> = {
  'product-level': 'Товарные статьи',
  'store-level': 'Магазинные статьи',
  'mixed / heuristic': 'Распределяемые статьи',
};

const GROUP_ORDER = ['product-level', 'mixed / heuristic', 'store-level'];

function groupTone(group: string) {
  if (group === 'store-level') return 'badge-warning';
  if (group === 'mixed / heuristic') return 'badge-danger';
  return 'badge-ok';
}

export function ExpenseBreakdownTable({
  title,
  subtitle,
  rows,
  emptyText = 'В текущем периоде здесь нет ненулевых статей.',
}: Props) {
  const visibleRows = rows.filter((row) => Math.abs(row.amount) > 0.0001);

  return (
    <section className="panel section">
      <div className="section-heading">
        <div>
          <h2 className="page-title" style={{ fontSize: '1.2rem' }}>
            {title}
          </h2>
          {subtitle ? <p className="page-subtle">{subtitle}</p> : null}
        </div>
      </div>

      {!visibleRows.length ? (
        <p className="page-subtle">{emptyText}</p>
      ) : (
        GROUP_ORDER.map((group) => {
          const groupRows = visibleRows.filter((row) => row.bucketGroup === group);
          if (!groupRows.length) return null;

          return (
            <div key={group} className="table-wrap" style={{ marginTop: '14px' }}>
              <div className="chip-row" style={{ marginBottom: '10px' }}>
                <span className={`badge ${groupTone(group)}`}>{GROUP_LABELS[group] ?? group}</span>
              </div>
              <table className="table-lite">
                <thead>
                  <tr>
                    <th>Подстатья</th>
                    <th>Сумма</th>
                    <th>Где сидит</th>
                    <th>Как привязано</th>
                    <th>Источник</th>
                  </tr>
                </thead>
                <tbody>
                  {groupRows.map((row) => (
                    <tr key={row.bucketSubtype}>
                      <td>
                        <div style={{ display: 'grid', gap: '4px' }}>
                          <strong>{row.label}</strong>
                          <span style={{ color: 'rgb(148 163 184)', fontSize: '0.8rem' }}>{row.reason}</span>
                        </div>
                      </td>
                      <td>{formatMoney(row.amount)}</td>
                      <td>
                        <div style={{ display: 'grid', gap: '4px' }}>
                          {row.productLevelAmount > 0 ? <span>Товар: {formatMoney(row.productLevelAmount)}</span> : null}
                          {row.storeLevelAmount > 0 ? <span>Магазин: {formatMoney(row.storeLevelAmount)}</span> : null}
                          {row.mixedAmount > 0 ? <span>Через распределение: {formatMoney(row.mixedAmount)}</span> : null}
                        </div>
                      </td>
                      <td>
                        <div style={{ display: 'grid', gap: '4px' }}>
                          <span>{row.attributionMethod}</span>
                          <span style={{ color: 'rgb(148 163 184)', fontSize: '0.8rem' }}>Уверенность: {row.confidence}</span>
                        </div>
                      </td>
                      <td>{row.sourceLayer}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })
      )}
    </section>
  );
}
