'use client';

import { painSubtitle } from '@/lib/copy';

export function Pain() {
  return (
    <section className="py-16 sm:py-20">
      <div className="container-wrap">
        <h2 className="section-title">Похоже на ваш магазин?</h2>
        <p className="section-subtitle">{painSubtitle}</p>
      </div>
    </section>
  );
}
