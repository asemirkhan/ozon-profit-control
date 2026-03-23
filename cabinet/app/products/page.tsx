import { PageHeader } from '@/components/cabinet/PageHeader';
import { getAdsProfitDrivenSnapshot } from '@/lib/ads-profit-driven';
import { buildProductAttentionSignals } from '@/lib/attention-engine';
import { buildBenchmarkCogsRatio, resolveEstimatedCogs } from '@/lib/cogs';
import { formatMoney, formatPercent } from '@/lib/format';
import { getProfitDashboardSnapshot } from '@/lib/profit-dashboard';

type Props = {
  searchParams?: {
    mode?: string;
    type?: string;
    reason?: string;
    q?: string;
    view?: string;
  };
};

type ModeKey = 'all' | 'attention';
type SignalType = 'all' | 'ads' | 'logistics' | 'commission';

type AttentionItem = {
  sku: string;
  nazvanie: string;
  type: Exclude<SignalType, 'all'>;
  reasonKey: string;
  reasonLabel: string;
  evidence: string;
  impact: number;
  detailHref: string;
};

function resolveMode(value: string | undefined, legacyView: string | undefined): ModeKey {
  if (value === 'attention' || legacyView === 'attention') return 'attention';
  return 'all';
}

function resolveType(value: string | undefined): SignalType {
  if (value === 'ads') return 'ads';
  if (value === 'logistics') return 'logistics';
  if (value === 'commission') return 'commission';
  return 'all';
}

function reasonOptions(type: SignalType) {
  if (type === 'ads') {
    return [
      { key: 'all', label: 'Все причины' },
      { key: 'cpo_growth', label: 'Реклама стала дороже' },
      { key: 'budget_concentration', label: 'Бюджет сосредоточен' },
      { key: 'not_profitable', label: 'Реклама не окупается' },
    ];
  }

  if (type === 'logistics') {
    return [
      { key: 'all', label: 'Все причины' },
      { key: 'logistics_growth', label: 'Растёт логистика' },
    ];
  }

  if (type === 'commission') {
    return [
      { key: 'all', label: 'Все причины' },
      { key: 'commission_growth', label: 'Растут комиссии' },
    ];
  }

  return [{ key: 'all', label: 'Все причины' }];
}

export default async function ProductsPage({ searchParams }: Props) {
  const [snapshot, adsProfitSnapshot] = await Promise.all([getProfitDashboardSnapshot(), getAdsProfitDrivenSnapshot()]);
  const mode = resolveMode(searchParams?.mode, searchParams?.view);
  const type = resolveType(searchParams?.type);
  const reason = searchParams?.reason || 'all';
  const query = (searchParams?.q || '').trim().toLowerCase();
  const appHref = (path: string) => `/cabinet${path}`;

  if (!snapshot) {
    return (
      <div>
        <PageHeader title="Товары" />
      </div>
    );
  }

  const rowMap = new Map(snapshot.products.rows.map((row) => [row.sku, row]));
  const cogsBenchmarkRatio = buildBenchmarkCogsRatio(snapshot.products.rows);
  const attentionItems: AttentionItem[] = buildProductAttentionSignals(snapshot, adsProfitSnapshot).flatMap((signal) => {
    const row = rowMap.get(signal.sku);
    if (!row) return [];
    return [
      {
        sku: signal.sku,
        nazvanie: signal.nazvanie,
        type: signal.category,
        reasonKey: signal.reasonKey,
        reasonLabel: signal.reasonLabel,
        evidence: signal.body,
        impact: signal.impact,
        detailHref: signal.href,
      },
    ];
  });

  const filteredAttentionItems = attentionItems
    .filter((item) => (type === 'all' ? true : item.type === type))
    .filter((item) => (reason === 'all' ? true : item.reasonKey === reason))
    .filter((item) => (query ? `${item.sku} ${item.nazvanie}`.toLowerCase().includes(query) : true))
    .sort((a, b) => b.impact - a.impact);

  const groupedAttentionItems = filteredAttentionItems.reduce<Record<string, AttentionItem[]>>((acc, item) => {
    acc[item.reasonLabel] ||= [];
    acc[item.reasonLabel].push(item);
    return acc;
  }, {});

  const allRows = snapshot.products.rows
    .filter((row) => row.viruchka > 0)
    .filter((row) => (query ? `${row.sku} ${row.nazvanie}`.toLowerCase().includes(query) : true))
    .sort((a, b) => b.operacionnaya_pribyl - a.operacionnaya_pribyl);

  const level1 = [
    { key: 'all', label: 'Все товары' },
    { key: 'attention', label: 'Обратить внимание' },
  ];

  const level2 = [
    { key: 'all', label: 'Все' },
    { key: 'ads', label: 'Реклама' },
    { key: 'logistics', label: 'Логистика' },
    { key: 'commission', label: 'Комиссии' },
  ];

  return (
    <div>
      <PageHeader title="Товары" />

      <section className="panel section">
        <div className="flex flex-wrap gap-2">
          {level1.map((filter) => {
            const params = new URLSearchParams();
            params.set('mode', filter.key);
            if (query) params.set('q', query);
            return (
              <a key={filter.key} href={appHref(`/products?${params.toString()}`)} className={`badge ${mode === filter.key ? 'badge-ok' : ''}`}>
                {filter.label}
              </a>
            );
          })}
        </div>

        {mode === 'attention' ? (
          <>
            <div className="mt-4 flex flex-wrap gap-2">
              {level2.map((filter) => {
                const params = new URLSearchParams();
                params.set('mode', 'attention');
                params.set('type', filter.key);
                if (query) params.set('q', query);
                return (
                  <a key={filter.key} href={appHref(`/products?${params.toString()}`)} className={`badge ${type === filter.key ? 'badge-ok' : ''}`}>
                    {filter.label}
                  </a>
                );
              })}
            </div>

            <div className="mt-4 flex flex-wrap gap-2">
              {reasonOptions(type).map((filter) => {
                const params = new URLSearchParams();
                params.set('mode', 'attention');
                params.set('type', type);
                params.set('reason', filter.key);
                if (query) params.set('q', query);
                return (
                  <a key={filter.key} href={appHref(`/products?${params.toString()}`)} className={`badge ${reason === filter.key ? 'badge-ok' : ''}`}>
                    {filter.label}
                  </a>
                );
              })}
            </div>
          </>
        ) : null}

        <form action="" method="get" className="mt-4 grid gap-3 lg:grid-cols-[1fr_auto]">
          <input type="hidden" name="mode" value={mode} />
          {mode === 'attention' ? <input type="hidden" name="type" value={type} /> : null}
          {mode === 'attention' ? <input type="hidden" name="reason" value={reason} /> : null}
          <input
            type="search"
            name="q"
            defaultValue={query}
            placeholder="Поиск по товару или артикулу"
            className="w-full rounded-xl border border-white/15 bg-slate-950/35 px-3 py-2 text-sm text-white outline-none placeholder:text-slate-500 focus:border-teal-300/60"
          />
          <button className="rounded-xl border border-white/15 bg-white/[0.04] px-4 py-2 text-sm text-slate-100 hover:bg-white/[0.08]">
            Найти
          </button>
        </form>
      </section>

      {mode === 'attention' ? (
        <section className="panel section">
          {Object.keys(groupedAttentionItems).length ? (
            <div className="space-y-6">
              {Object.entries(groupedAttentionItems).map(([reasonLabel, items]) => (
                <div key={reasonLabel}>
                  <h2 className="m-0 text-lg font-semibold">{reasonLabel}</h2>
                  <div className="mt-4 space-y-3">
                    {items.map((item) => (
                      <a
                        key={`${item.reasonKey}-${item.sku}`}
                        href={appHref(item.detailHref)}
                        className="block rounded-2xl border border-white/12 bg-slate-950/45 p-4 transition hover:border-cyan-300/35"
                      >
                        <div className="flex flex-wrap items-start justify-between gap-3">
                          <div>
                            <div className="font-semibold text-slate-50">{item.nazvanie}</div>
                            <div className="mt-1 text-xs text-slate-400">{item.sku}</div>
                          </div>
                        </div>
                        <div className="mt-3 whitespace-pre-line text-sm text-slate-200">{item.evidence}</div>
                      </a>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-slate-400">По выбранным фильтрам сигналы не найдены.</div>
          )}
        </section>
      ) : (
        <section className="panel section">
          <div className="table-wrap">
            <table className="table-base">
              <thead>
                <tr>
                  <th>Товар</th>
                  <th>Выручка</th>
                  <th>Себестоимость</th>
                  <th>Прибыль</th>
                  <th>Маржа</th>
                  <th>Реклама</th>
                  <th>Логистика</th>
                  <th>Расходы WB</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {allRows.map((row) => (
                  <tr key={row.sku}>
                    <td>
                      {row.nazvanie}
                      <div className="mt-1 text-xs text-slate-400">{row.sku}</div>
                    </td>
                    <td>{formatMoney(row.viruchka)}</td>
                    <td>
                      {formatMoney(
                        resolveEstimatedCogs({
                          revenue: row.viruchka,
                          operatingProfit: row.operacionnaya_pribyl,
                          profitAfterCogs: row.pribyl_posle_sebestoimosti,
                          fallbackRatio: cogsBenchmarkRatio,
                        }).amount
                      )}
                      <div className="mt-1 text-xs text-slate-400">оценка</div>
                    </td>
                    <td>{formatMoney(row.operacionnaya_pribyl)}</td>
                    <td>{formatPercent(row.marzha_operacionnaya)}</td>
                    <td>{formatMoney(row.reklama)}</td>
                    <td>{formatMoney(row.logistika)}</td>
                    <td>{formatMoney(row.wb_rashody)}</td>
                    <td>
                      <a href={appHref(row.detail_href)} className="text-link">
                        Открыть
                      </a>
                    </td>
                  </tr>
                ))}
                {allRows.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="text-slate-400">
                      По выбранным фильтрам товары не найдены.
                    </td>
                  </tr>
                ) : null}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
