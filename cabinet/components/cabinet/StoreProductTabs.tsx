'use client';

import { useState } from 'react';

import { formatMoney } from '@/lib/format';

type TabRow = {
  key: string;
  title: string;
  valueLabel: string;
  value: number;
  href: string;
};

type TabConfig = {
  key: string;
  label: string;
  href: string;
  rows: TabRow[];
};

type Props = {
  title?: string;
  tabs: TabConfig[];
};

export function StoreProductTabs({ title = 'Товары', tabs }: Props) {
  const [activeKey, setActiveKey] = useState<string>(tabs[0]?.key || '');
  const activeTab = tabs.find((tab) => tab.key === activeKey) || tabs[0];

  if (!activeTab) return null;

  return (
    <section className="panel section">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="m-0 text-lg font-semibold">{title}</h3>
        <a href={activeTab.href} className="text-link text-sm">
          Открыть товары
        </a>
      </div>
      <div className="tab-nav mt-4">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            type="button"
            className={`tab-link ${activeTab.key === tab.key ? 'tab-link--active' : ''}`}
            onClick={() => setActiveKey(tab.key)}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="mt-4 grid gap-3">
        {activeTab.rows.map((row) => (
          <a
            key={row.key}
            href={row.href}
            className="flex items-center justify-between gap-4 rounded-2xl border border-white/10 bg-slate-950/25 p-4 text-inherit no-underline transition hover:border-cyan-300/30 hover:bg-slate-950/40"
          >
            <p className="m-0 text-sm font-medium text-slate-100">{row.title}</p>
            <div className="text-right">
              <p className="m-0 text-xs text-slate-400">{row.valueLabel}</p>
              <p className="mt-1 text-sm font-semibold text-slate-100">{formatMoney(row.value)}</p>
            </div>
          </a>
        ))}
      </div>
    </section>
  );
}
