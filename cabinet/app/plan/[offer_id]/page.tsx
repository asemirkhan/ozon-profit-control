import { redirect } from 'next/navigation';

type Props = {
  params: {
    offer_id: string;
  };
};

export default function PlanPage({ params }: Props) {
  redirect(`/products?legacy_offer_id=${encodeURIComponent(params.offer_id)}`);
}
