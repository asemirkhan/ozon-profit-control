import type { ProfitDashboardSnapshot } from '@/lib/profit-dashboard';

export type ReconciliationStatus = 'OK' | 'WARNING' | 'ERROR';

export type ReconciliationRow = {
  code: string;
  label: string;
  status: ReconciliationStatus;
  detail: string;
};

export type ReconciliationSummary = {
  overallStatus: ReconciliationStatus;
  rows: ReconciliationRow[];
};

function resolveStatus(delta: number, warning = 1, error = 100): ReconciliationStatus {
  if (delta > error) return 'ERROR';
  if (delta > warning) return 'WARNING';
  return 'OK';
}

function formatDelta(value: number) {
  return `${value.toFixed(2)} ₽`;
}

export function buildReconciliationSummary(snapshot: ProfitDashboardSnapshot): ReconciliationSummary {
  const productRows = snapshot.product_details || [];
  const duplicateCount = productRows.length - new Set(productRows.map((row) => row.sku)).size;
  const missingCore = productRows.filter(
    (row) =>
      !row.sku ||
      !row.nazvanie ||
      row.viruchka === null ||
      row.viruchka === undefined ||
      row.operacionnaya_pribyl === null ||
      row.operacionnaya_pribyl === undefined ||
      row.wb_rashody === null ||
      row.wb_rashody === undefined ||
      row.reklama === null ||
      row.reklama === undefined ||
      row.units === null ||
      row.units === undefined
  ).length;

  const estimatedCogsIssues = productRows.filter((row) => {
    const estimated = row.viruchka - row.operacionnaya_pribyl - row.wb_rashody - row.reklama;
    return estimated < -1;
  }).length;

  const revenueSum = productRows.reduce((sum, row) => sum + row.viruchka, 0);
  const profitSum = productRows.reduce((sum, row) => sum + row.operacionnaya_pribyl, 0);
  const adsSum = productRows.reduce((sum, row) => sum + row.reklama, 0);
  const wbSum = productRows.reduce((sum, row) => sum + row.wb_rashody, 0);

  const commissionSum = productRows.reduce((sum, row) => sum + row.komissiya, 0);
  const logisticsSum = productRows.reduce((sum, row) => sum + row.logistika, 0);
  const servicesSum = productRows.reduce((sum, row) => sum + row.platnye_uslugi, 0);
  const penaltiesSum = productRows.reduce((sum, row) => sum + row.shtrafy_i_uderzhaniya, 0);

  const aggregateRevenueDelta = Math.abs(revenueSum - snapshot.summary.revenue_total);
  const aggregateProfitDelta = Math.abs(profitSum - snapshot.summary.operacionnaya_pribyl_total);
  const aggregateAdsDelta = Math.abs(adsSum - snapshot.summary.ad_spend_total);
  const aggregateWbDelta = Math.abs(wbSum - snapshot.summary.wb_cost_total);
  const aggregateMaxDelta = Math.max(aggregateRevenueDelta, aggregateProfitDelta, aggregateAdsDelta, aggregateWbDelta);

  const breakdownCommissionDelta = Math.abs(commissionSum - snapshot.summary.commission_total);
  const breakdownLogisticsDelta = Math.abs(logisticsSum - snapshot.summary.logistics_total);
  const breakdownServicesDelta = Math.abs(servicesSum - snapshot.summary.services_total);
  const breakdownPenaltiesDelta = Math.abs(penaltiesSum - snapshot.summary.penalties_total);
  const breakdownMaxDelta = Math.max(
    breakdownCommissionDelta,
    breakdownLogisticsDelta,
    breakdownServicesDelta,
    breakdownPenaltiesDelta
  );

  const rows: ReconciliationRow[] = [
    {
      code: 'completeness',
      label: 'Полнота данных',
      status: missingCore > 0 ? 'ERROR' : 'OK',
      detail: missingCore > 0 ? `Проблемных товаров: ${missingCore}` : 'Ключевые метрики заполнены.',
    },
    {
      code: 'duplicates',
      label: 'Дубликаты SKU',
      status: duplicateCount > 0 ? 'ERROR' : 'OK',
      detail: duplicateCount > 0 ? `Дубликатов: ${duplicateCount}` : 'Повторов SKU нет.',
    },
    {
      code: 'formula',
      label: 'Базовая формула',
      status: estimatedCogsIssues > 0 ? 'WARNING' : 'OK',
      detail: estimatedCogsIssues > 0 ? `Товаров с отрицательной оценкой себестоимости: ${estimatedCogsIssues}` : 'Выручка и расходы складываются корректно.',
    },
    {
      code: 'aggregates',
      label: 'Сходимость итогов',
      status: resolveStatus(aggregateMaxDelta),
      detail:
        aggregateMaxDelta > 0
          ? `Максимальное отклонение: ${formatDelta(aggregateMaxDelta)}`
          : 'Суммы по товарам сходятся с итогами магазина.',
    },
    {
      code: 'breakdown',
      label: 'Разложение расходов',
      status: resolveStatus(breakdownMaxDelta),
      detail:
        breakdownMaxDelta > 0
          ? `Максимальное отклонение: ${formatDelta(breakdownMaxDelta)}`
          : 'Категории расходов сходятся с итоговыми суммами.',
    },
  ];

  const overallStatus = rows.some((row) => row.status === 'ERROR')
    ? 'ERROR'
    : rows.some((row) => row.status === 'WARNING')
      ? 'WARNING'
      : 'OK';

  return { overallStatus, rows };
}
