import { ProductDetailExperience } from '@/components/cabinet/ProductDetailExperience';
import { PageHeader } from '@/components/cabinet/PageHeader';
import { getAdsProfitDrivenProduct, getAdsProfitDrivenSnapshot } from '@/lib/ads-profit-driven';
import { getSignalsForSku } from '@/lib/attention-engine';
import { buildBenchmarkCogsRatio } from '@/lib/cogs';
import { getProductDetailBySku, getProfitDashboardSnapshot } from '@/lib/profit-dashboard';

type Props = {
  params: {
    sku: string;
  };
  searchParams?: {
    focus?: string;
    reason?: string;
  };
};

function resolveFocus(value: string | undefined) {
  if (value === 'ads') return 'ads' as const;
  if (value === 'logistics') return 'logistics' as const;
  if (value === 'commission') return 'commission' as const;
  if (value === 'other') return 'other' as const;
  return 'overview' as const;
}

export default async function ProductDetailPage({ params, searchParams }: Props) {
  const [snapshot, product, adsSignal, adsSnapshot] = await Promise.all([
    getProfitDashboardSnapshot(),
    getProductDetailBySku(params.sku),
    getAdsProfitDrivenProduct(params.sku),
    getAdsProfitDrivenSnapshot(),
  ]);

  if (!snapshot || !product) {
    return (
      <div>
        <PageHeader title="Карточка товара" subtitle="По этому товару данные пока не найдены." />
      </div>
    );
  }

  const attentionSignals = getSignalsForSku(snapshot, adsSnapshot, params.sku);

  return (
    <ProductDetailExperience
      product={product}
      benchmarks={snapshot.economics.benchmarks || []}
      adsSignal={adsSignal}
      attentionSignals={attentionSignals}
      storeAdSpendTotal={snapshot.summary.ad_spend_total}
      cogsBenchmarkRatio={buildBenchmarkCogsRatio(snapshot.products.rows)}
      initialFocus={resolveFocus(searchParams?.focus)}
      initialReason={searchParams?.reason}
    />
  );
}
