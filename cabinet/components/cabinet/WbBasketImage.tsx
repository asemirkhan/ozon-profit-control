'use client';

import { useMemo, useState } from 'react';

type Props = {
  nmId: string;
  alt: string;
  className: string;
  fallbackText: string;
  initialUrl?: string | null;
};

function buildCandidateUrls(nmId: string, initialUrl?: string | null) {
  const numeric = Number(nmId);
  if (!Number.isFinite(numeric)) return initialUrl ? [initialUrl] : [];

  const vol = Math.floor(numeric / 100000);
  const part = Math.floor(numeric / 1000);
  const generated = Array.from({ length: 40 }, (_, index) => {
    const host = `basket-${String(index + 1).padStart(2, '0')}.wbbasket.ru`;
    return `https://${host}/vol${vol}/part${part}/${nmId}/images/c516x688/1.webp`;
  });

  return [initialUrl, ...generated].filter((value, index, array): value is string => Boolean(value) && array.indexOf(value as string) === index);
}

export function WbBasketImage({ nmId, alt, className, fallbackText, initialUrl }: Props) {
  const candidates = useMemo(() => buildCandidateUrls(nmId, initialUrl), [initialUrl, nmId]);
  const [index, setIndex] = useState(0);
  const currentSrc = candidates[index] ?? null;

  if (!currentSrc) {
    return <div className={`${className} product-hero__image--fallback`}>{fallbackText}</div>;
  }

  return (
    <img
      className={className}
      src={currentSrc}
      alt={alt}
      loading="eager"
      onError={() => {
        setIndex((prev) => (prev + 1 < candidates.length ? prev + 1 : prev));
      }}
    />
  );
}
