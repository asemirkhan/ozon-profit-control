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
  const query = marketplace ? `?marketplace=${encodeURIComponent(marketplace)}` : '';
  redirect(`/plan/${encodeURIComponent(params.offer_id)}${query}`);
}
