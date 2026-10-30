'use client';

import { useMemo, useState } from 'react';

type Props = {
  nmId: string;
  alt: string;
  className: string;
  fallback: string;
};

function buildCandidateUrls(nmId: string) {
  const numeric = Number(nmId);
  if (!Number.isFinite(numeric)) return [];
  const vol = Math.floor(numeric / 100000);
  const part = Math.floor(numeric / 1000);

  return Array.from({ length: 40 }, (_, index) => {
    const basket = String(index + 1).padStart(2, '0');
    return `https://basket-${basket}.wbbasket.ru/vol${vol}/part${part}/${numeric}/images/c516x688/1.webp`;
  });
}

export function WbAutoImage({ nmId, alt, className, fallback }: Props) {
  const candidates = useMemo(() => buildCandidateUrls(nmId), [nmId]);
  const [index, setIndex] = useState(0);
  const [failed, setFailed] = useState(false);
  const src = candidates[index];

  if (!src || failed) {
    return <div className={`${className} product-hero__image--fallback`}>{fallback}</div>;
  }

  return (
    <img
      className={className}
      src={src}
      alt={alt}
      onError={() => {
        if (index < candidates.length - 1) {
          setIndex(index + 1);
        } else {
          setFailed(true);
        }
      }}
    />
  );
}
