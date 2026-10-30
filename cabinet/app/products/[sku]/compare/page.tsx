import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { unstable_noStore as noStore } from 'next/cache';

import { PageHeader } from '@/components/cabinet/PageHeader';
import { WbBasketImage } from '@/components/cabinet/WbBasketImage';
import { formatCount, formatMoney } from '@/lib/format';
import { getCabinetProduct, getProfitControlCabinetPack, type CompetitorRow, type ProductPack } from '@/lib/profit-control-cabinet';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

type Props = {
  params: {
    sku: string;
  };
};

type CardMeta = {
  photoCount: number;
  hasVideo: boolean;
  characteristicsCount: number;
};

async function loadCompetitorImageManifest(): Promise<Record<string, string>> {
  return readFile(path.join(process.cwd(), 'data', 'wb_competitor_image_manifest.json'), 'utf-8')
    .then((raw) => JSON.parse(raw) as { images?: Record<string, string> })
    .then((parsed) => parsed.images || {})
    .catch(() => ({} as Record<string, string>));
}

function deltaText(value: number | null | undefined, base: number | null | undefined, suffix = '') {
  if (value === null || value === undefined || base === null || base === undefined) return null;
  const delta = value - base;
  const sign = delta > 0 ? '+' : delta < 0 ? '−' : '±';
  const abs = Math.abs(delta);
  return `${sign}${suffix === '₽' ? formatMoney(abs) : `${String(abs.toFixed(1)).replace('.', ',')}${suffix}`}`;
}

function priceDeltaTone(competitorPrice: number | null | undefined, ourPrice: number | null | undefined) {
  if (competitorPrice === null || competitorPrice === undefined || ourPrice === null || ourPrice === undefined) return 'neutral';
  if (competitorPrice < ourPrice) return 'negative';
  if (competitorPrice > ourPrice) return 'positive';
  return 'neutral';
}

function strongerIsNegative(value: number | null | undefined, base: number | null | undefined) {
  if (value === null || value === undefined || base === null || base === undefined) return 'neutral';
  if (value > base) return 'negative';
  if (value < base) return 'positive';
  return 'neutral';
}

function competitorInitials(name: string) {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() || '')
    .join('');
}

function CompareMetric({
  label,
  value,
  delta,
  tone = 'neutral',
}: {
  label: string;
  value: string;
  delta?: string | null;
  tone?: 'positive' | 'negative' | 'neutral';
}) {
  return (
    <div className="metric-strip__item">
      <span>{label}</span>
      <strong>
        {value}{' '}
        {delta ? <span className={`metric-delta metric-delta--${tone}`}>({delta})</span> : null}
      </strong>
    </div>
  );
}

function OurProductCard({ product, cardMeta }: { product: ProductPack; cardMeta?: CardMeta | null }) {
  return (
    <section className="product-hero product-hero--compare">
      <div className="product-hero__media">
        {product.imageUrl ? (
          <img className="product-hero__image" src={product.imageUrl} alt={product.productName} />
        ) : (
          <div className="product-hero__image product-hero__image--fallback">{product.imageFallback}</div>
        )}
      </div>
      <div className="product-hero__copy">
        <div className="product-compare-label">Наш товар</div>
        <h2 className="page-title product-hero__title">{product.productName}</h2>
        <div className="metric-strip">
          <CompareMetric label="Цена" value={formatMoney(product.price)} />
          <CompareMetric label="Рейтинг" value={product.rating ? product.rating.toFixed(1).replace('.', ',') : '—'} />
          <CompareMetric label="Отзывы" value={formatCount(product.feedbacks)} />
        </div>
        {cardMeta ? (
          <div className="story-grid mt-4">
            <article className="story-card">
              <h3>Карточка</h3>
              <p>
                Фото: {formatCount(cardMeta.photoCount)} · Видео: {cardMeta.hasVideo ? 'есть' : 'нет'} · Характеристики:{' '}
                {formatCount(cardMeta.characteristicsCount)}
              </p>
            </article>
          </div>
        ) : null}
      </div>
    </section>
  );
}

function CompetitorCard({
  item,
  product,
  imageUrl,
  cardMeta,
}: {
  item: CompetitorRow;
  product: ProductPack;
  imageUrl: string | null;
  cardMeta?: CardMeta | null;
}) {
  const priceDelta = deltaText(item.price, product.price, '₽');
  const ratingDelta = deltaText(item.rating, product.rating, '');
  const reviewsDelta = deltaText(item.reviews, product.feedbacks, '');

  return (
    <section className="product-hero product-hero--compare">
      <div className="product-hero__media">
        <WbBasketImage
          nmId={item.nmId}
          alt={item.name}
          className="product-hero__image"
          fallbackText={competitorInitials(item.name)}
          initialUrl={imageUrl ?? `/api/wb-image/${item.nmId}`}
        />
      </div>
      <div className="product-hero__copy">
        <div className="product-compare-label">Конкурент</div>
        <h2 className="page-title product-hero__title">{item.name}</h2>
        <div className="product-cell__meta">ID {item.nmId}</div>
        <div className="metric-strip">
          <CompareMetric label="Цена" value={formatMoney(item.price)} delta={priceDelta} tone={priceDeltaTone(item.price, product.price)} />
          <CompareMetric
            label="Рейтинг"
            value={item.rating ? item.rating.toFixed(1).replace('.', ',') : '—'}
            delta={ratingDelta}
            tone={strongerIsNegative(item.rating, product.rating)}
          />
          <CompareMetric label="Отзывы" value={formatCount(item.reviews)} delta={reviewsDelta} tone={strongerIsNegative(item.reviews, product.feedbacks)} />
        </div>
        <div className="story-grid mt-4">
          <article className="story-card">
            <h3>Карточка</h3>
            <p>
              Фото: {formatCount(cardMeta?.photoCount ?? item.photoCount)} · Видео: {(cardMeta?.hasVideo ?? item.hasVideo) ? 'есть' : 'нет'} · Характеристики:{' '}
              {formatCount(cardMeta?.characteristicsCount ?? item.characteristicsCount)}
            </p>
          </article>
          <article className="story-card">
            <h3>Положение в выдаче</h3>
            <p>
              {item.aboveUs ? 'Сейчас этот товар стоит выше нас в выдаче.' : 'Сейчас этот товар не стоит выше нас в выдаче.'}
              {item.queries.length ? ` Сравнение важно по запросам: ${item.queries.join(', ')}.` : ''}
            </p>
          </article>
        </div>
      </div>
    </section>
  );
}

export default async function ProductComparePage({ params }: Props) {
  noStore();
  const [pack, product] = await Promise.all([getProfitControlCabinetPack(), getCabinetProduct(params.sku)]);
  const financeNote = pack.financeBoundary?.note ?? '';

  if (!product) {
    return (
      <div className="page-stack">
        <PageHeader title="Сравнение с конкурентами" subtitle="По этому товару не удалось найти данные для сравнения." />
      </div>
    );
  }

  const localImageManifest = await loadCompetitorImageManifest();

  return (
    <div className="page-stack">
      <a href={`/products/${product.productId}`} className="inline-link">
        ← Назад к карточке товара
      </a>

      <PageHeader title="Сравнение с конкурентами" />
      {financeNote ? <div className="finance-boundary-note">{financeNote}</div> : null}

      <OurProductCard product={product} />

      {product.competitors.length ? (
        product.competitors.map((item) => (
          <CompetitorCard
            key={item.nmId}
            item={item}
            product={product}
            imageUrl={localImageManifest[item.nmId] ?? null}
          />
        ))
      ) : (
        <section className="panel section">
          <p>Текущий канонический pack пока не содержит отдельный live competitor слой для этого товара.</p>
        </section>
      )}
    </div>
  );
}
