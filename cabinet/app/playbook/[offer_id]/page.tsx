import { redirect } from 'next/navigation';

type Props = {
  params: {
    offer_id: string;
  };
  searchParams: {
    marketplace?: string | string[];
  };
};

export default function LegacyPlaybookRoute({ params, searchParams }: Props) {
  const marketplace = Array.isArray(searchParams.marketplace)
    ? searchParams.marketplace[0]
    : searchParams.marketplace;
  const query = marketplace
    ? `?legacy_offer_id=${encodeURIComponent(params.offer_id)}&marketplace=${encodeURIComponent(marketplace)}`
    : `?legacy_offer_id=${encodeURIComponent(params.offer_id)}`;
  redirect(`/products${query}`);
}
