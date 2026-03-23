import type { ProductRow } from '@/lib/profit-dashboard';

type CogsSourceRow = Pick<ProductRow, 'viruchka' | 'operacionnaya_pribyl' | 'pribyl_posle_sebestoimosti'>;

type ResolveEstimatedCogsInput = {
  revenue: number;
  operatingProfit: number | null | undefined;
  profitAfterCogs: number | null | undefined;
  fallbackRatio?: number | null;
};

export type EstimatedCogsResult = {
  amount: number;
  isEstimate: boolean;
  source: 'direct' | 'benchmark' | 'base_25';
};

function finiteNumber(value: number | null | undefined) {
  return value !== null && value !== undefined && Number.isFinite(value) ? value : null;
}

function directCogsAmount(input: ResolveEstimatedCogsInput) {
  const operatingProfit = finiteNumber(input.operatingProfit);
  const profitAfterCogs = finiteNumber(input.profitAfterCogs);
  if (operatingProfit === null || profitAfterCogs === null) return null;

  const diff = operatingProfit - profitAfterCogs;
  if (!Number.isFinite(diff) || diff <= 0) return null;
  return diff;
}

export function buildBenchmarkCogsRatio(rows: CogsSourceRow[]) {
  const ratios = rows
    .map((row) => {
      if (!Number.isFinite(row.viruchka) || row.viruchka <= 0) return null;
      const amount = directCogsAmount({
        revenue: row.viruchka,
        operatingProfit: row.operacionnaya_pribyl,
        profitAfterCogs: row.pribyl_posle_sebestoimosti,
      });
      if (amount === null || amount <= 0) return null;
      return amount / row.viruchka;
    })
    .filter((value): value is number => value !== null)
    .sort((a, b) => a - b);

  if (!ratios.length) return null;
  return ratios[Math.floor(ratios.length / 2)] ?? null;
}

export function resolveEstimatedCogs(input: ResolveEstimatedCogsInput): EstimatedCogsResult {
  const direct = directCogsAmount(input);
  if (direct !== null) {
    return {
      amount: direct,
      isEstimate: true,
      source: 'direct',
    };
  }

  const revenue = finiteNumber(input.revenue) ?? 0;
  if (revenue <= 0) {
    return {
      amount: 0,
      isEstimate: true,
      source: 'base_25',
    };
  }

  const benchmarkRatio =
    input.fallbackRatio !== null && input.fallbackRatio !== undefined && input.fallbackRatio > 0 ? input.fallbackRatio : null;
  if (benchmarkRatio !== null) {
    return {
      amount: revenue * benchmarkRatio,
      isEstimate: true,
      source: 'benchmark',
    };
  }

  return {
    amount: revenue * 0.25,
    isEstimate: true,
    source: 'base_25',
  };
}
