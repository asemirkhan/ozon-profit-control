import { redirect } from 'next/navigation';

export default function AdsPage() {
  redirect('/products?filter=ads_spend_up');
}
