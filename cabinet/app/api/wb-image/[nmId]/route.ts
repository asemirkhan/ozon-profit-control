import { readFile } from 'node:fs/promises';
import path from 'node:path';

import { NextResponse } from 'next/server';

export const runtime = 'nodejs';

const hostCache = new Map<string, string | null>();

function buildCandidateHosts(preferred?: string | null) {
  return [
    ...(preferred ? [preferred] : []),
    ...Array.from({ length: 40 }, (_, index) => `basket-${String(index + 1).padStart(2, '0')}.wbbasket.ru`).filter(
      (host) => host !== preferred,
    ),
  ];
}

async function fetchImage(nmId: string) {
  const numeric = Number(nmId);
  if (!Number.isFinite(numeric)) return null;

  const vol = Math.floor(numeric / 100000);
  const part = Math.floor(numeric / 1000);
  const preferred = hostCache.get(nmId) ?? null;

  for (const host of buildCandidateHosts(preferred)) {
    const imageUrl = `https://${host}/vol${vol}/part${part}/${nmId}/images/c516x688/1.webp`;
    try {
      const response = await fetch(imageUrl, {
        headers: {
          'user-agent': 'Mozilla/5.0',
          accept: 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        },
        cache: 'no-store',
        signal: AbortSignal.timeout(1200),
      });

      if (!response.ok || !response.body) continue;
      hostCache.set(nmId, host);
      return response;
    } catch {
      continue;
    }
  }

  hostCache.set(nmId, null);
  return null;
}

export async function GET(_request: Request, { params }: { params: { nmId: string } }) {
  const cachedPath = path.join(process.cwd(), 'public', 'wb-competitors', `${params.nmId}.webp`);

  try {
    const buffer = await readFile(cachedPath);
    return new NextResponse(new Uint8Array(buffer), {
      status: 200,
      headers: {
        'content-type': 'image/webp',
        'cache-control': 'public, max-age=31536000, immutable',
      },
    });
  } catch {
    // fall through to network fetch
  }

  const response = await fetchImage(params.nmId);

  if (!response || !response.body) {
    return new NextResponse('Image not found', { status: 404 });
  }

  return new NextResponse(response.body, {
    status: 200,
    headers: {
      'content-type': response.headers.get('content-type') || 'image/webp',
      'cache-control': 'public, max-age=86400, stale-while-revalidate=604800',
    },
  });
}
