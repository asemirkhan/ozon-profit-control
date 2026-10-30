'use client';

import { useEffect, useRef, useState } from 'react';

export type ProblemFlipCard = {
  title: string;
  backText: string;
  tone: 'ok' | 'warning' | 'critical';
};

type Props = {
  cards: ProblemFlipCard[];
};

export function ProductProblemFlips({ cards }: Props) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    function handlePointerDown(event: MouseEvent) {
      if (!rootRef.current) return;
      if (rootRef.current.contains(event.target as Node)) return;
      setOpenIndex(null);
    }

    document.addEventListener('mousedown', handlePointerDown);
    return () => document.removeEventListener('mousedown', handlePointerDown);
  }, []);

  if (cards.length === 0) {
    return <div className="table-copy-muted">Выраженных проблемных зон сейчас не видно.</div>;
  }

  return (
    <div className="problem-chip-row" ref={rootRef}>
      {cards.map((card, index) => {
        const opened = openIndex === index;

        return (
          <div key={`${card.title}-${index}`} className="problem-chip-wrap">
            <button
              type="button"
              className={`problem-chip problem-chip--${card.tone} ${opened ? 'problem-chip--active' : ''}`}
              onClick={() => setOpenIndex(opened ? null : index)}
            >
              <span className="problem-chip__text">{card.title}</span>
            </button>

            {opened ? (
              <div className="problem-chip__popup" role="dialog">
                <p>{card.backText}</p>
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
