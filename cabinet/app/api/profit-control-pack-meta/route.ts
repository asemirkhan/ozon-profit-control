import { NextResponse } from 'next/server';

import { getSimulationPackMeta } from '@/lib/profit-control-simulation';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET() {
  const meta = await getSimulationPackMeta();
  return NextResponse.json(meta, {
    headers: {
      'cache-control': 'no-store, no-cache, max-age=0, must-revalidate',
    },
  });
}
