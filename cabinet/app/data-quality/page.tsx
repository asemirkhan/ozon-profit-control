import { PageHeader } from '@/components/cabinet/PageHeader';
import { getDataQuality } from '@/lib/api';
import { formatCount, formatMoney, normalizeUiText } from '@/lib/format';

function DqRow({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <article className="rounded-xl border border-white/10 bg-slate-950/25 p-4">
      <p className="m-0 text-sm text-slate-400">{label}</p>
      <p className="mt-2 text-2xl font-semibold">{formatCount(value)}</p>
      <p className="mt-2 text-sm text-slate-300">{normalizeUiText(hint)}</p>
    </article>
  );
}

function DqMoneyRow({ label, value, hint }: { label: string; value: number; hint: string }) {
  return (
    <article className="rounded-xl border border-white/10 bg-slate-950/25 p-4">
      <p className="m-0 text-sm text-slate-400">{label}</p>
      <p className="mt-2 text-2xl font-semibold">{formatMoney(value)}</p>
      <p className="mt-2 text-sm text-slate-300">{normalizeUiText(hint)}</p>
    </article>
  );
}

export default async function DataQualityPage() {
  const quality = await getDataQuality();

  return (
    <div>
      <PageHeader
        title="Качество данных"
        subtitle={`Показываем, какие аномалии в ${(quality?.marketplace || 'магазина').toUpperCase()} могут искажать расчёты и влиять на точность рекомендаций.`}
      />

      <section className="panel section">
        <div className="grid gap-3 lg:grid-cols-2">
          <DqRow
            label="Отрицательная выручка"
            value={quality?.negative_revenue_sku_count || 0}
            hint={quality?.negative_revenue_note || 'Нет данных.'}
          />
          <DqRow
            label="Продажи при нулевой выручке"
            value={quality?.zero_revenue_with_sales_sku_count || 0}
            hint={quality?.zero_revenue_note || 'Нет данных.'}
          />
          <DqRow
            label="Отрицательная логистика"
            value={quality?.negative_logistics_sku_count || 0}
            hint={quality?.negative_logistics_note || 'Нет данных.'}
          />
          <DqRow
            label="Остаток без продаж"
            value={quality?.stock_without_sales_sku_count || 0}
            hint={quality?.stock_without_sales_note || 'Нет данных.'}
          />
          <DqRow
            label="Дубликаты номеров отправлений"
            value={quality?.duplicate_posting_count || 0}
            hint={quality?.duplicate_posting_note || 'Нет данных.'}
          />
          <DqRow
            label="Операции без точной привязки к товарам"
            value={quality?.unattributed_row_count || 0}
            hint={quality?.unattributed_note || 'Нет данных.'}
          />
          <DqMoneyRow
            label="Расходы, не связанные с товарами"
            value={quality?.unattributed_cost_total || 0}
            hint={quality?.unattributed_note || 'Нет данных.'}
          />
          <DqMoneyRow
            label="Расходы, распределённые приблизительно"
            value={quality?.fallback_attributed_cost_total || 0}
            hint={quality?.fallback_attributed_note || 'Нет данных.'}
          />
        </div>
        <div className="mt-4 rounded-xl border border-white/10 bg-slate-950/25 p-4">
          <h2 className="m-0 text-base font-semibold">Что это значит</h2>
          <p className="mt-2 text-sm text-slate-300">{normalizeUiText(quality?.notes || 'Нет данных.')}</p>
        </div>
      </section>
    </div>
  );
}
