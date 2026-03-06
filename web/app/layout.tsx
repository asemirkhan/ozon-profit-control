import type { Metadata } from 'next';
import { Plus_Jakarta_Sans, Sora } from 'next/font/google';

import './globals.css';

const jakarta = Plus_Jakarta_Sans({
  subsets: ['latin', 'latin-ext', 'cyrillic-ext'],
  variable: '--font-jakarta',
  display: 'swap'
});

const sora = Sora({
  subsets: ['latin', 'latin-ext'],
  variable: '--font-sora',
  display: 'swap'
});

const brandName = process.env.NEXT_PUBLIC_BRAND_NAME || 'Profit Control';
const title = `${brandName} — аналитика прибыли Ozon и Wildberries`;
const description =
  'Показываем, какие товары реально приносят прибыль, где теряются деньги и что исправить в первую очередь.';

export const metadata: Metadata = {
  title,
  description,
  openGraph: {
    title,
    description,
    images: ['/images/og.webp'],
    type: 'website',
    locale: 'ru_RU'
  }
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ru" className={`${jakarta.variable} ${sora.variable}`}>
      <body className="font-[var(--font-jakarta)]">{children}</body>
    </html>
  );
}
