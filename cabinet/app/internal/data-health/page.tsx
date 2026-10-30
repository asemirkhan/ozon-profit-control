import Link from 'next/link';

import { PageHeader } from '@/components/cabinet/PageHeader';
import { getInternalDataHealth } from '@/lib/api';
import { formatCount, formatMoney, normalizeUiText } from '@/lib/format';

type TabKey = 'ingestion' | 'completeness' | 'attribution' | 'snapshots';

const tabs: Array<{ key: TabKey; label: string }> = [
  { key: 'ingestion', label: 'Ingestion' },
  { key: 'completeness', label: 'Completeness' },
  { key: 'attribution', label: 'Attribution' },
  { key: 'snapshots', label: 'Published snapshots' },
];

function resolveTab(raw?: string): TabKey {
  if (raw === 'completeness' || raw === 'attribution' || raw === 'snapshots') {
    return raw;
  }
  return 'ingestion';
}

function TabLink({ activeTab, tabKey, label }: { activeTab: TabKey; tabKey: TabKey; label: string }) {
  const isActive = activeTab === tabKey;
  return (
    <Link href={`/internal/data-health?tab=${tabKey}`} className={`tab-link ${isActive ? 'tab-link--active' : ''}`}>
      {label}
    </Link>
  );
}

type Props = {
  searchParams?: {
    tab?: string;
  };
};

export default async function InternalDataHealthPage({ searchParams }: Props) {
  const tab = resolveTab(searchParams?.tab);
  const data = await getInternalDataHealth();

  return (
    <div>
      <PageHeader
        title="Internal Data Health"
        subtitle="Внутренний контроль загрузки, полноты, распределения денег и опубликованных снимков."
      />

      <section className="panel section">
        <div className="tab-nav">
          {tabs.map((item) => (
            <TabLink key={item.key} activeTab={tab} tabKey={item.key} label={item.label} />
          ))}
        </div>
      </section>

      {tab === 'ingestion' ? (
        <section className="panel section overflow-x-auto">
          <table className="w-full min-w-[900px] text-sm">
            <thead className="text-left text-slate-400">
              <tr>
                <th>source</th>
                <th>last run</th>
                <th>status</th>
                <th>raw</th>
                <th>normalized</th>
                <th>loaded</th>
                <th>retries</th>
                <th>error_type</th>
              </tr>
            </thead>
            <tbody>
              {(data?.ingestion || []).map((row) => (
                <tr key={`${row.source}:${row.run_id}`} className="border-t border-white/10">
                  <td>{row.source}</td>
                  <td>{row.run_id}</td>
                  <td>{row.status}</td>
                  <td>{formatCount(row.raw_rows)}</td>
                  <td>{formatCount(row.normalized_rows)}</td>
                  <td>{formatCount(row.loaded_rows)}</td>
                  <td>{formatCount(row.retries)}</td>
                  <td>{row.error_type || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {tab === 'completeness' ? (
        <section className="panel section overflow-x-auto">
          <table className="w-full min-w-[900px] text-sm">
            <thead className="text-left text-slate-400">
              <tr>
                <th>source</th>
                <th>expected_days</th>
                <th>actual_days</th>
                <th>missing_days</th>
                <th>coverage_ratio</th>
                <th>usable_for_publish</th>
                <th>criticality</th>
                <th>status</th>
                <th>error_reason</th>
              </tr>
            </thead>
            <tbody>
              {(data?.completeness || []).map((row) => (
                <tr key={row.source} className="border-t border-white/10">
                  <td>{row.source}</td>
                  <td>{formatCount(row.expected_days)}</td>
                  <td>{formatCount(row.actual_days)}</td>
                  <td>{formatCount(row.missing_days)}</td>
                  <td>{`${(row.coverage_ratio * 100).toFixed(1)}%`}</td>
                  <td>{String(row.usable_for_publish)}</td>
                  <td>{row.source_criticality}</td>
                  <td>{row.status}</td>
                  <td>{row.error_reason || row.partial_reason || row.error_type || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {tab === 'attribution' ? (
        <section className="panel section overflow-x-auto">
          <table className="w-full min-w-[900px] text-sm">
            <thead className="text-left text-slate-400">
              <tr>
                <th>bucket</th>
                <th>money total</th>
                <th>money attributed</th>
                <th>money unattributed</th>
                <th>attribution by method</th>
              </tr>
            </thead>
            <tbody>
              {(data?.attribution || []).map((row) => (
                <tr key={row.money_bucket} className="border-t border-white/10">
                  <td>{row.money_bucket}</td>
                  <td>{formatMoney(row.money_total)}</td>
                  <td>{formatMoney(row.money_attributed)}</td>
                  <td>{formatMoney(row.money_unattributed)}</td>
                  <td>{normalizeUiText(Object.entries(row.attribution_by_method).map(([k, v]) => `${k}:${v.toFixed(2)}`).join(', ') || '-')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}

      {tab === 'snapshots' ? (
        <section className="panel section overflow-x-auto">
          <table className="w-full min-w-[900px] text-sm">
            <thead className="text-left text-slate-400">
              <tr>
                <th>snapshot_id</th>
                <th>period</th>
                <th>publish_status</th>
                <th>published_at</th>
                <th>is_last_good</th>
                <th>blocking sources</th>
              </tr>
            </thead>
            <tbody>
              {(data?.snapshots || []).map((row) => (
                <tr key={row.snapshot_id} className="border-t border-white/10">
                  <td>{row.snapshot_id}</td>
                  <td>
                    {row.period_from} - {row.period_to}
                  </td>
                  <td>{row.publish_status}</td>
                  <td>{row.published_at || '-'}</td>
                  <td>{String(row.is_last_good)}</td>
                  <td>{row.blocking_sources.join(', ') || '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      ) : null}
    </div>
  );
}
