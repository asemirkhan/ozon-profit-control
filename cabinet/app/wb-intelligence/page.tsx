import { PageHeader } from '@/components/cabinet/PageHeader';
import { KpiCard } from '@/components/cabinet/KpiCard';
import { formatMoney, formatPercent } from '@/lib/format';
import { getWbIntelligenceSnapshot } from '@/lib/wb-intelligence';

function priorityBadge(value: number) {
  if (value === 1) return 'badge badge-danger';
  if (value === 2) return 'badge badge-warning';
  if (value === 3) return 'badge badge-ok';
  return 'badge';
}

function statusBadge(value: string) {
  if (value === 'исключить из товарных рекомендаций') return 'badge badge-danger';
  if (value === 'разобрать вручную') return 'badge badge-danger';
  if (value === 'проверить в первую очередь') return 'badge badge-warning';
  if (value === 'планово проверить') return 'badge badge-ok';
  return 'badge';
}

export default async function WbIntelligencePage() {
  const snapshot = await getWbIntelligenceSnapshot();

  if (!snapshot) {
    return (
      <div>
        <PageHeader
          title="Кабинет прибыли магазина"
          subtitle="Snapshot пока не найден. Сначала соберите fact-safe product layer и cabinet snapshot."
        />
        <section className="panel section">
          <p className="m-0 text-sm text-slate-300">
            Ожидаемый файл: `cabinet/data/wb_profit_intelligence_ru_v1.json`.
          </p>
        </section>
      </div>
    );
  }

  const summary = snapshot.store_summary;
  const attentionRows = snapshot.sku_decisions.filter(
    (row) => row.klass_sku_ru !== 'data hygiene' && row.klass_sku_ru !== 'data_hygiene'
  );

  return (
    <div>
      <PageHeader
        title="Кабинет прибыли магазина"
        subtitle="Сначала fact-safe экономика, потом решения: здесь остаются только подтверждённые выводы и явно помеченные ограничения."
      />

      <section className="panel section">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 className="m-0 text-lg font-semibold">Сводка магазина</h2>
            <p className="mt-1 text-sm text-slate-400">
              Seller ID: {snapshot.seller_id} · Период: {snapshot.period_start} - {snapshot.period_end}
            </p>
          </div>
          <div className="badge badge-ok">GO with limitations</div>
        </div>
        <div className="mt-3 rounded-2xl border border-amber-300/20 bg-amber-500/10 p-4 text-sm text-amber-50">
          Операционная прибыль подтверждена reconciliation gate. Прибыль после себестоимости остаётся оценкой, а рекламные рекомендации по товарам ограничены, потому что часть рекламы всё ещё сидит в `~`.
        </div>
        <div className="kpi-grid mt-3">
          <KpiCard label="Выручка" value={formatMoney(summary.revenue_total)} />
          <KpiCard label="Операционная прибыль" value={formatMoney(summary.operacionnaya_pribyl_total)} />
          <KpiCard label="Операционная маржа" value={formatPercent(summary.marzha_operacionnaya_total)} />
          <KpiCard label="Прибыль после себестоимости" value={`${formatMoney(summary.pribyl_posle_sebestoimosti_total)} (оценка)`} />
          <KpiCard
            label="Маржа после себестоимости"
            value={
              summary.marzha_posle_sebestoimosti_total == null
                ? '-'
                : `${formatPercent(summary.marzha_posle_sebestoimosti_total)} (оценка)`
            }
          />
          <KpiCard label="Реклама" value={formatMoney(summary.ad_spend_total)} />
          <KpiCard label="Критичные товары" value={String(summary.critical_problem_sku_count)} />
          <KpiCard label="Высокий приоритет" value={String(summary.high_problem_sku_count)} />
          <KpiCard label="Риск по фактовым проблемам" value={formatMoney(summary.fact_problem_risk_total)} />
          <KpiCard label="Риск по data hygiene" value={formatMoney(summary.data_hygiene_risk_total)} />
        </div>
      </section>

      <section className="panel section">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="m-0 text-lg font-semibold">Что важно сейчас</h2>
          <p className="m-0 text-sm text-slate-400">Верхнеуровневые управленческие задачи на период без дублей по товарам.</p>
        </div>
        <div className="actions-grid mt-4">
          {snapshot.store_actions.map((row) => (
            <article key={row.nomer_prioriteta} className="action-card">
              <p className="m-0 text-xs uppercase tracking-wide text-cyan-200">Приоритет {row.nomer_prioriteta}</p>
              <h3 className="mt-2 text-base font-semibold text-slate-100">{row.blok_zadachi_ru}</h3>
              <p className="mt-2 text-sm text-slate-300">{row.opisanie_ru}</p>
              <p className="mt-2 text-sm text-slate-100">
                Почему это важно: {row.pochemu_eto_vazhno_ru}
              </p>
              <p className="mt-2 text-sm text-emerald-200">Первый шаг: {row.pervyy_shag_ru}</p>
              <p className="mt-2 text-xs text-slate-400">
                Затронуто товаров: {row.skolko_sku_zatronuto} · риск {formatMoney(row.summa_riska)} · уверенность {row.uroven_uverennosti}
              </p>
            </article>
          ))}
        </div>
      </section>

      <section className="panel section">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="m-0 text-lg font-semibold">Товары, которые требуют внимания</h2>
          <p className="m-0 text-sm text-slate-400">Один товар = одна итоговая карточка. Data hygiene вынесен в отдельный раздел ниже.</p>
        </div>
        <div className="table-wrap mt-4">
          <table className="table-base">
            <thead>
              <tr>
                <th>Артикул</th>
                <th>Товар</th>
                <th>Класс</th>
                <th>Статус</th>
                <th>Что происходит</th>
                <th>Что делать первым</th>
                <th>Чего не делать</th>
                <th>Риск</th>
              </tr>
            </thead>
            <tbody>
              {attentionRows.map((row) => (
                <tr key={row.sku}>
                  <td>{row.sku}</td>
                  <td>
                    <div className="flex flex-col gap-2">
                      <span>{row.nazvanie}</span>
                      <span className="text-xs text-slate-400">{row.klass_sku_ru}</span>
                    </div>
                  </td>
                  <td>{row.klass_sku_ru}</td>
                  <td>
                    <div className="flex flex-col gap-2">
                      <span className={statusBadge(row.itogovy_status_ru)}>{row.itogovy_status_ru}</span>
                      <span className={priorityBadge(row.prioritet_chislo)}>{row.prioritet_ru}</span>
                    </div>
                  </td>
                  <td>
                    <div className="flex flex-col gap-2">
                      <span className="text-sm text-slate-100">{row.korotkaya_kartochka_ru}</span>
                      <span className="text-sm text-slate-300">{row.srednyaya_kartochka_ru}</span>
                      <span className="text-xs text-slate-400">{row.osnovanie_rekomendacii_ru} · {row.osnovano_na_fakte_ili_ocenke}</span>
                    </div>
                  </td>
                  <td className="text-sm text-slate-200">{row.pervoe_deystvie_ru}</td>
                  <td className="text-sm text-slate-300">{row.chto_ne_delat_ru}</td>
                  <td>{formatMoney(row.summa_riska)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel section">
        <div className="grid gap-4 lg:grid-cols-[1.35fr_0.65fr]">
          <div>
            <h2 className="m-0 text-lg font-semibold">Товары, которые лучше не трогать</h2>
            <div className="table-wrap mt-4">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>Артикул</th>
                    <th>Товар</th>
                    <th>Класс</th>
                    <th>Почему сильный</th>
                    <th>Чего не делать</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshot.top_do_not_touch.map((row) => (
                    <tr key={row.sku}>
                      <td>{row.sku}</td>
                      <td>{row.nazvanie}</td>
                      <td>{row.klass_sku_ru}</td>
                      <td className="text-sm text-slate-300">{row.pochemu_silnyi_ru}</td>
                      <td className="text-sm text-slate-300">{row.chto_ne_delat_ru}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="space-y-4">
            <div className="rounded-2xl border border-white/10 bg-slate-950/35 p-4">
              <h3 className="m-0 text-base font-semibold">Root Cause Summary</h3>
              <div className="mt-3 space-y-2">
                {snapshot.root_cause_summary.map((row) => (
                  <div key={row.blok_prichiny_ru} className="flex items-center justify-between gap-3 text-sm">
                    <span className="text-slate-300">{row.blok_prichiny_ru}</span>
                    <span className="text-slate-100">{formatMoney(row.summa_riska)}</span>
                  </div>
                ))}
              </div>
            </div>

            <div className="rounded-2xl border border-white/10 bg-slate-950/35 p-4">
              <h3 className="m-0 text-base font-semibold">Сегментация товаров</h3>
              <div className="mt-3 space-y-2">
                {Array.from(
                  snapshot.segmentation.reduce((acc, row) => {
                    acc.set(row.segment_ru, (acc.get(row.segment_ru) || 0) + 1);
                    return acc;
                  }, new Map<string, number>())
                ).map(([segment, count]) => (
                  <div key={segment} className="flex items-center justify-between gap-3 text-sm">
                    <span className="text-slate-300">{segment}</span>
                    <span className="text-slate-100">{count}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="panel section">
        <div className="grid gap-4 lg:grid-cols-[0.9fr_1.1fr]">
          <div>
            <h2 className="m-0 text-lg font-semibold">Проблемы качества данных</h2>
            <div className="mt-4 space-y-3">
              {snapshot.data_hygiene.map((row) => (
                <article key={row.tip_problemy} className="rounded-2xl border border-amber-300/25 bg-amber-500/10 p-4">
                  <p className="m-0 text-xs uppercase tracking-wide text-amber-100">{row.tip_problemy}</p>
                  <p className="mt-2 text-lg font-semibold text-amber-50">{formatMoney(row.summa)}</p>
                  <p className="mt-2 text-sm text-amber-100">{row.opisanie}</p>
                  <p className="mt-2 text-sm text-amber-50">Что делать: {row.chto_s_etim_delat}</p>
                </article>
              ))}
            </div>
          </div>

          <div>
            <h2 className="m-0 text-lg font-semibold">Динамика по дням</h2>
            <div className="table-wrap mt-4">
              <table className="table-base">
                <thead>
                  <tr>
                    <th>День</th>
                    <th>Выручка</th>
                    <th>Реклама</th>
                    <th>Опер. прибыль</th>
                    <th>Маржа</th>
                    <th>Комментарий</th>
                  </tr>
                </thead>
                <tbody>
                  {snapshot.daily_dynamics.map((row) => (
                    <tr key={row.day}>
                      <td>{row.day}</td>
                      <td>{formatMoney(row.viruchka)}</td>
                      <td>{formatMoney(row.reklama)}</td>
                      <td>{formatMoney(row.operacionnaya_pribyl)}</td>
                      <td>{formatPercent(row.marzha_operacionnaya)}</td>
                      <td className="text-sm text-slate-300">{row.anomaliya_ru || 'без явного отклонения'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      <section className="panel section">
        <div className="grid gap-4 lg:grid-cols-2">
          <div className="rounded-2xl border border-emerald-300/20 bg-emerald-500/10 p-4">
            <h2 className="m-0 text-lg font-semibold">Что можно показывать клиенту уже сейчас</h2>
            <div className="mt-3 space-y-3">
              {snapshot.ready_phrases.map((row) => (
                <p key={row} className="m-0 text-sm text-emerald-50">
                  {row}
                </p>
              ))}
            </div>
          </div>
          <div className="rounded-2xl border border-white/10 bg-slate-950/35 p-4">
            <h2 className="m-0 text-lg font-semibold">Что пока не выводим клиенту</h2>
            <div className="mt-3 space-y-3">
              {snapshot.hidden_phrases.map((row) => (
                <p key={row} className="m-0 text-sm text-slate-300">
                  {row}
                </p>
              ))}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
