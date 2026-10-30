import { unstable_noStore as noStore } from 'next/cache';

import { EconomicsWorkbench } from '@/components/cabinet/EconomicsWorkbench';
import { PageHeader } from '@/components/cabinet/PageHeader';
import { formatMoney } from '@/lib/format';
import { getCabinetProducts, getProfitControlCabinetPack, getProfitControlCabinetPackMeta } from '@/lib/profit-control-cabinet';
import { getSimulationStorePageData, type SimulationHorizon } from '@/lib/profit-control-simulation';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

type Props = {
  searchParams?: Record<string, string | string[] | undefined>;
};

function resolveHorizon(value: string | string[] | undefined): SimulationHorizon {
  const scalar = Array.isArray(value) ? value[0] : value;
  return scalar === '7d' || scalar === '1d' ? scalar : '28d';
}

function formatDelta(value: number) {
  const abs = formatMoney(Math.abs(value));
  if (value > 0) return `+${abs}`;
  if (value < 0) return `−${abs}`;
  return abs;
}

function formatDeltaPercent(current: number, previous: number | null) {
  if (previous === null || Math.abs(previous) < 0.0001) return null;
  const ratio = (current - previous) / previous;
  const sign = ratio > 0 ? '+' : ratio < 0 ? '−' : '';
  return `${sign}${Math.abs(ratio * 100).toFixed(1).replace('.', ',')}%`;
}

function sentenceCase(text: string) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function monthLabel(range: string) {
  const match = range.match(/^(\d{2})\.(\d{2})\.(\d{4})/);
  if (!match) return range;
  const monthNames = [
    'январь', 'февраль', 'март', 'апрель', 'май', 'июнь',
    'июль', 'август', 'сентябрь', 'октябрь', 'ноябрь', 'декабрь',
  ];
  return `${monthNames[Number(match[2]) - 1] ?? match[2]} ${match[3]}`;
}

function horizonLabel(horizon: SimulationHorizon, data: Awaited<ReturnType<typeof getSimulationStorePageData>>) {
  if (horizon === '28d') return `${monthLabel(data.current28dWindow)} / ${monthLabel(data.previous28dWindow)}`;
  if (horizon === '7d') return `${data.current7dWindow} против ${data.previous7dWindow}`;
  return `${data.current1dDate} против обычного уровня такого дня недели`;
}

function horizonSupportLabel(horizon: SimulationHorizon) {
  if (horizon === '28d') return 'Завершенный календарный месяц против предыдущего завершенного месяца';
  if (horizon === '7d') return '7 подтвержденных дней против предыдущих 7 дней';
  return 'Оперативный сигнал по последнему дню против устойчивой базы';
}

function formatUpdatedAt(value: string | null) {
  if (!value) return 'время обновления уточняется';
  return new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'Europe/Moscow',
  }).format(new Date(value));
}

function formatDataDay(value: string | undefined) {
  if (!value) return 'дата уточняется';
  const [year, month, day] = value.split('-');
  return year && month && day ? `${day}.${month}.${year}` : value;
}

export default async function EconomicsPage({ searchParams }: Props) {
  noStore();
  const horizon = resolveHorizon(searchParams?.period);
  const [data, cabinetPack, graphProducts, packMeta] = await Promise.all([
    getSimulationStorePageData(horizon),
    getProfitControlCabinetPack(),
    getCabinetProducts(),
    getProfitControlCabinetPackMeta(),
  ]);
  const storePack = cabinetPack.store;
  const latestDataDay = cabinetPack.financeBoundary.confirmedThrough;

  return (
    <div className="page-stack simulation-page simulation-page--store">
      <section className="panel section simulation-surface simulation-period-card">
        <div className="simulation-page-heading">
          <PageHeader title="Экономика магазина" />
          <div className="simulation-freshness" aria-label="Свежесть данных">
            <span className="simulation-freshness__dot" />
            <span>Данные по {formatDataDay(latestDataDay ?? undefined)}</span>
            <span className="simulation-freshness__divider">·</span>
            <span>Обновлено {formatUpdatedAt(packMeta.generatedAt || packMeta.lastModifiedAt)}</span>
          </div>
        </div>
        <div className="simulation-period-toolbar">
          <div className="simulation-period-toolbar__copy">
            <span>Сравнение</span>
            <strong>{horizonLabel(horizon, data)}</strong>
            <small>{horizon === '1d' ? data.baselineMethod1d : horizonSupportLabel(horizon)}</small>
          </div>
          <div className="chip-row simulation-period-toolbar__chips">
            {(['28d', '7d', '1d'] as SimulationHorizon[]).map((item) => (
              <a
                key={item}
                href={`/economics?period=${item}`}
                className={`workbench-tab ${horizon === item ? 'workbench-tab--active' : ''}`}
              >
                {item === '28d' ? 'Месяц' : item === '7d' ? '7 дней' : '1 день'}
              </a>
            ))}
          </div>
        </div>
      </section>

      <section className="simulation-kpi-grid">
        {data.metricCards.map((card) => (
          <article key={card.key} className="simulation-kpi-card simulation-kpi-card--light">
            <p className="simulation-kpi-card__label">{card.label}</p>
            <p className="simulation-kpi-card__value">{formatMoney(card.currentValue)}</p>
            <p className={`simulation-delta ${
              card.deltaValue === 0
                ? 'simulation-delta--neutral'
                : card.key === 'expenses'
                  ? card.deltaValue > 0 ? 'simulation-delta--bad' : 'simulation-delta--good'
                  : card.deltaValue > 0 ? 'simulation-delta--good' : 'simulation-delta--bad'
            }`}>
              {formatDelta(card.deltaValue)}
            </p>
            <p className="simulation-kpi-card__baseline">
              {card.previousValue !== null ? `Было: ${formatMoney(card.previousValue)}` : 'Нет второго подтвержденного периода'}
            </p>
            <p className="simulation-kpi-card__signal">
              {card.previousValue !== null
                ? `${formatDeltaPercent(card.currentValue, card.previousValue) ?? '0%'} к прошлому периоду`
                : 'Сигнал без второго окна'}
            </p>
          </article>
        ))}
      </section>

      <section className="simulation-store-grid">
        <article className="panel section simulation-surface">
          <div className="section-heading">
            <div>
              <h2 className="section-title simulation-title-positive">Топ по приросту результата</h2>
            </div>
          </div>
          <div className="simulation-ranking-list">
            <div className="simulation-ranking-head">
              <span>Товар</span>
              <span>Текущий результат</span>
              <span>Δ результата</span>
            </div>
            {data.topGrowth.map((item, index) => (
              <a key={item.normalizedProductKey} href={item.href} className="simulation-ranking-row">
                <div className="simulation-ranking-row__product">
                  <span className="simulation-ranking-row__index">{index + 1}</span>
                  {item.imageUrl ? <img className="simulation-ranking-row__image" src={item.imageUrl} alt="" /> : null}
                  <div className="simulation-ranking-row__copy">
                    <strong>{item.productName}</strong>
                    <span>{item.sku !== '~' ? `SKU: ${item.sku}` : item.groupName}</span>
                  </div>
                </div>
                <span className="simulation-ranking-row__value">{formatMoney(item.resultCurrent ?? 0)}</span>
                <span className="simulation-ranking-row__metric">
                  <strong className="simulation-delta simulation-delta--good">{formatDelta(item.resultDelta)}</strong>
                  <span>
                    {formatDeltaPercent(item.resultCurrent ?? 0, item.resultPrevious ?? null)
                      ? `${formatDeltaPercent(item.resultCurrent ?? 0, item.resultPrevious ?? null)} к прошлому периоду`
                      : 'Без базы для процента'}
                  </span>
                </span>
              </a>
            ))}
          </div>
        </article>

        <article className="panel section simulation-surface">
          <div className="section-heading">
            <div>
              <h2 className="section-title simulation-title-negative">Топ по снижению результата</h2>
            </div>
          </div>
          <div className="simulation-ranking-list">
            <div className="simulation-ranking-head">
              <span>Товар</span>
              <span>Текущий результат</span>
              <span>Δ результата</span>
            </div>
            {data.topDecline.map((item, index) => (
              <a key={item.normalizedProductKey} href={item.href} className="simulation-ranking-row">
                <div className="simulation-ranking-row__product">
                  <span className="simulation-ranking-row__index">{index + 1}</span>
                  {item.imageUrl ? <img className="simulation-ranking-row__image" src={item.imageUrl} alt="" /> : null}
                  <div className="simulation-ranking-row__copy">
                    <strong>{item.productName}</strong>
                    <span>{item.sku !== '~' ? `SKU: ${item.sku}` : item.groupName}</span>
                  </div>
                </div>
                <span className="simulation-ranking-row__value">{formatMoney(item.resultCurrent ?? 0)}</span>
                <span className="simulation-ranking-row__metric">
                  <strong className="simulation-delta simulation-delta--bad">{formatDelta(item.resultDelta)}</strong>
                  <span>
                    {formatDeltaPercent(item.resultCurrent ?? 0, item.resultPrevious ?? null)
                      ? `${formatDeltaPercent(item.resultCurrent ?? 0, item.resultPrevious ?? null)} к прошлому периоду`
                      : 'Без базы для процента'}
                  </span>
                </span>
              </a>
            ))}
          </div>
        </article>
      </section>

      <section className="panel section simulation-surface">
        <div className="section-heading">
          <div>
            <h2 className="section-title">Ключевые проблемы</h2>
          </div>
        </div>
        <div className="simulation-insights-grid">
          {data.problemBlocks28d.map((problem, index) => (
            <a key={problem.key} href={problem.href} className="simulation-insight-card simulation-insight-card--problem">
              <span className="simulation-action-item__index">{index + 1}</span>
              <div className="simulation-insight-card__copy">
                <strong>{sentenceCase(problem.title)}</strong>
                <p>{problem.valueText}</p>
                <span>{problem.recommendation}</span>
                <span className="simulation-insight-card__link">Показать {problem.products.length} {problem.products.length === 1 ? 'товар' : problem.products.length < 5 ? 'товара' : 'товаров'} →</span>
              </div>
            </a>
          ))}
        </div>
      </section>

      <section className="panel section simulation-surface">
        <div className="section-heading">
          <div>
            <h2 className="section-title">Ключевые рекомендации</h2>
          </div>
        </div>
        <div className="simulation-insights-grid">
          {data.recommendationBlocks.map((problem, index) => (
            <a key={problem.key} href={problem.href} className="simulation-insight-card simulation-insight-card--ok">
              <span className="simulation-action-item__index">{index + 1}</span>
              <div className="simulation-insight-card__copy">
                <strong>{sentenceCase(problem.title)}</strong>
                <p>{problem.recommendation}</p>
                <span>{problem.valueText}</span>
                <span className="simulation-insight-card__link">Открыть товар →</span>
              </div>
            </a>
          ))}
        </div>
      </section>

      <details id="store-graphs" className="panel section simulation-surface simulation-collapsible">
        <summary>Графики магазина</summary>
        <div className="simulation-graphs">
          <EconomicsWorkbench
            incomeRows={storePack.incomeRows}
            expenseRows={storePack.expenseRows}
            storeRegions={storePack.regions}
            storeWarehouses={storePack.warehouses}
            products={graphProducts.map((item) => ({
              productId: item.productId,
              productName: item.productName,
              regions: item.geoHotspots,
              warehouses: item.warehouseHotspots,
            }))}
          />
        </div>
      </details>
    </div>
  );
}
