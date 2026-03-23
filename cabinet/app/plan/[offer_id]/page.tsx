import { notFound } from 'next/navigation';

import { PageHeader } from '@/components/cabinet/PageHeader';
import { getPlaybook } from '@/lib/api';
import {
  attributionLabel,
  confidenceHint,
  confidenceLabel,
  displayProductName,
  effectActionLabel,
  effectTypeHint,
  effectTypeLabel,
  formatDays,
  formatMoney
} from '@/lib/format';

type Props = {
  params: {
    offer_id: string;
  };
  searchParams: {
    marketplace?: string | string[];
  };
};

function ActionBlock({ title, text }: { title: string; text: string }) {
  return (
    <article className="rounded-xl border border-white/10 bg-slate-950/25 p-4">
      <h3 className="m-0 text-base font-semibold text-slate-100">{title}</h3>
      <p className="mt-2 text-sm leading-relaxed text-slate-200">{text}</p>
    </article>
  );
}

export default async function PlanPage({ params, searchParams }: Props) {
  const marketplace = Array.isArray(searchParams.marketplace)
    ? searchParams.marketplace[0]
    : searchParams.marketplace;

  const playbook = await getPlaybook(params.offer_id, marketplace);

  if (!playbook) {
    notFound();
  }

  const checkSummary = `Проверка через ${playbook.action_window_days} дней. ${playbook.check_after_window}`;
  const whatToDo = `${playbook.what_to_do} ${playbook.where_to_do_it}`.trim();

  return (
    <div>
      <PageHeader
        title={`План действий по товару ${displayProductName(playbook.product_name, playbook.offer_id)}`}
        subtitle={`${effectTypeLabel(playbook.effect_type)}: ${formatMoney(playbook.effect_value || playbook.estimated_gain_if_fixed)}. Точность расчёта: ${confidenceLabel(playbook.confidence)}.`}
      />

      <section className="panel section">
        <div className="mb-3 rounded-xl border border-white/10 bg-slate-950/30 p-3 text-sm text-slate-300">
          <p className="m-0">Качество расчёта: {attributionLabel(playbook.attribution_quality)}.</p>
          <p className="m-0 mt-1">{confidenceHint(playbook.confidence)}</p>
          <p className="m-0 mt-1">
            Тип эффекта: {effectActionLabel(playbook.effect_type)}. {effectTypeHint(playbook.effect_type)}
          </p>
          {playbook.effect_type === 'revenue_at_risk' ? (
            <div className="mt-2 rounded-lg border border-white/10 bg-slate-900/40 p-2 text-xs">
              <p className="m-0">Остатка примерно на {formatDays(playbook.days_of_stock_30d)}.</p>
              <p className="m-0 mt-1">Под угрозой за 1 день: {formatMoney(playbook.revenue_at_risk_per_day)}.</p>
              <p className="m-0 mt-1">Под угрозой за 7 дней: {formatMoney(playbook.revenue_at_risk_7d)}.</p>
            </div>
          ) : null}
        </div>

        <div className="grid gap-3 lg:grid-cols-2">
          <ActionBlock title="1. Проблема" text={playbook.problem_summary} />
          <ActionBlock title="2. Что сделать" text={whatToDo} />
          <ActionBlock title="3. Когда и как проверить результат" text={checkSummary} />
          <ActionBlock title="4. Если не помогло" text={playbook.if_not_improved} />
        </div>

        <div className="mt-3 rounded-xl border border-emerald-300/25 bg-emerald-500/10 p-4">
          <h3 className="m-0 text-base font-semibold text-emerald-50">Когда остановиться</h3>
          <p className="mt-2 text-sm text-emerald-100/90">{playbook.success_signal}</p>
          <p className="mt-2 text-xs text-emerald-100/80">Не делайте: {playbook.do_not_do}</p>
        </div>

        <div className="mt-3 rounded-xl border border-white/10 bg-slate-950/30 p-4">
          <h3 className="m-0 text-base font-semibold text-slate-100">Статус выполнения</h3>
          <p className="mt-2 text-sm text-slate-300">На следующем этапе здесь появится трекинг результата по выполненному действию.</p>
          <button
            type="button"
            disabled
            className="mt-3 rounded-lg border border-white/25 bg-slate-800/70 px-3 py-2 text-sm text-slate-300 opacity-80"
          >
            Отметить как выполнено (скоро)
          </button>
        </div>
      </section>
    </div>
  );
}
