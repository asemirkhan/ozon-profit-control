'use client';

type AttentionStateTone = 'ok' | 'warning' | 'critical';
type AttentionSectionTone = 'ok' | 'warning';

export type AttentionStateItem = {
  key: string;
  title: string;
  detail?: string;
  countLabel?: string;
  href?: string;
  linkLabel?: string;
  linkMode?: 'aside' | 'inline';
};

export type AttentionStateSection = {
  key: string;
  label: string;
  tone?: AttentionSectionTone;
  items: AttentionStateItem[];
};

type Props = {
  title: string;
  tone: AttentionStateTone;
  sections: AttentionStateSection[];
  statusTextOverride?: string;
};

const statusText: Record<AttentionStateTone, string> = {
  ok: 'В норме',
  warning: 'Есть зоны внимания',
  critical: 'Требует внимания',
};

function InlineLink({ href, label }: { href: string; label: string }) {
  return (
    <a href={href} className="inline-link">
      {label}
    </a>
  );
}

export function AttentionStatePanel({ title, tone, sections, statusTextOverride }: Props) {
  return (
    <section className="panel section">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="m-0 text-lg font-semibold">{title}</h2>
        <div className={`status-pill status-pill--${tone}`}>{statusTextOverride ?? statusText[tone]}</div>
      </div>

      <div className="attention-state mt-4">
        {sections.map((section) => (
          <div key={section.key} className="attention-state__section">
            <div className={`attention-state__tag attention-state__tag--${section.tone ?? 'ok'}`}>{section.label}</div>
            <div className="attention-state__items">
              {section.items.map((item) => (
                <div key={item.key} className="attention-state__item">
                  <div className="attention-state__copy">
                    <p className="attention-state__title">
                      {item.linkMode === 'inline' && item.href && item.linkLabel ? (
                        <>
                          {item.title} {'—'} <InlineLink href={item.href} label={item.linkLabel} />
                        </>
                      ) : (
                        item.title
                      )}
                    </p>
                    {item.detail ? <p className="attention-state__detail whitespace-pre-line">{item.detail}</p> : null}
                  </div>

                  {item.linkMode !== 'inline' && (item.countLabel || (item.href && item.linkLabel)) ? (
                    <div className="attention-state__meta">
                      {item.countLabel ? <span className="attention-state__count">{item.countLabel}</span> : null}
                      {item.href && item.linkLabel ? <InlineLink href={item.href} label={item.linkLabel} /> : null}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
