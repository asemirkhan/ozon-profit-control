import type {
  ActionRow,
  DataQualityRow,
  InternalDataHealth,
  ProductExtremes,
  PlaybookRow,
  ProductRow,
  RuleMapRow,
  StoreOverview,
  StoreSummary
} from '@/lib/types';

const API_BASE_URL =
  process.env.API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL || 'http://127.0.0.1:8000';

const DEFAULT_MARKETPLACE = (
  process.env.CABINET_MARKETPLACE || process.env.NEXT_PUBLIC_MARKETPLACE || ''
)
  .trim()
  .toLowerCase();

function buildPath(path: string, params?: URLSearchParams): string {
  const query = params?.toString() || '';
  return query ? `${path}?${query}` : path;
}

function withDefaultParams(params?: URLSearchParams): URLSearchParams {
  const result = params ? new URLSearchParams(params) : new URLSearchParams();
  if (DEFAULT_MARKETPLACE) {
    result.set('marketplace', DEFAULT_MARKETPLACE);
  }
  return result;
}

async function fetchJson<T>(path: string): Promise<T | null> {
  const url = `${API_BASE_URL}${path}`;
  try {
    const response = await fetch(url, { cache: 'no-store' });
    if (!response.ok) {
      return null;
    }
    return (await response.json()) as T;
  } catch {
    return null;
  }
}

export async function getStoreSummary(): Promise<StoreSummary | null> {
  return fetchJson<StoreSummary>(buildPath('/api/store-summary', withDefaultParams()));
}

export async function getTopActions(): Promise<ActionRow[]> {
  const params = withDefaultParams();
  params.set('top_only', 'true');
  return (await fetchJson<ActionRow[]>(buildPath('/api/actions', params))) || [];
}

export async function getActions(): Promise<ActionRow[]> {
  return (await fetchJson<ActionRow[]>(buildPath('/api/actions', withDefaultParams()))) || [];
}

export async function getProducts(filter?: string, q?: string): Promise<ProductRow[]> {
  const params = withDefaultParams();
  if (filter) params.set('filter', filter);
  if (q) params.set('q', q);
  return (await fetchJson<ProductRow[]>(buildPath('/api/products', params))) || [];
}

export async function getPlaybook(offerId: string, marketplace?: string): Promise<PlaybookRow | null> {
  const params = withDefaultParams();
  if (marketplace) {
    params.set('marketplace', marketplace);
  }
  return fetchJson<PlaybookRow>(buildPath(`/api/playbook/${encodeURIComponent(offerId)}`, params));
}

export async function getDataQuality(): Promise<DataQualityRow | null> {
  return fetchJson<DataQualityRow>(buildPath('/api/data-quality', withDefaultParams()));
}

export async function getRuleMap(): Promise<RuleMapRow[]> {
  return (await fetchJson<RuleMapRow[]>(buildPath('/api/rule-map', withDefaultParams()))) || [];
}

export async function getStoreOverview(): Promise<StoreOverview | null> {
  return fetchJson<StoreOverview>(buildPath('/api/store-overview', withDefaultParams()));
}

export async function getProductExtremes(limit = 5): Promise<ProductExtremes> {
  const params = withDefaultParams();
  params.set('limit', String(limit));
  return (await fetchJson<ProductExtremes>(buildPath('/api/product-extremes', params))) || {
    top_profitable: [],
    worst_by_profit: []
  };
}

export async function getInternalDataHealth(): Promise<InternalDataHealth | null> {
  return fetchJson<InternalDataHealth>(buildPath('/api/internal/data-health', withDefaultParams()));
}
