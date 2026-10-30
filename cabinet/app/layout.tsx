import type { Metadata } from 'next';

import { CabinetLiveRefresh } from '@/components/cabinet/CabinetLiveRefresh';
import { Sidebar } from '@/components/cabinet/Sidebar';

import './globals.css';

const brandName = process.env.NEXT_PUBLIC_BRAND_NAME || 'Контроль прибыли';
const title = `${brandName} | кабинет действий по прибыли`;
const description =
  'Кабинет принятия решений: где магазин теряет деньги, что исправлять в первую очередь и какой эффект это даст.';

export const metadata: Metadata = {
  title,
  description,
  metadataBase: new URL('http://127.0.0.1:3001'),
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
    <html lang="ru">
      <body className="font-[var(--font-jakarta)]">
        <CabinetLiveRefresh />
        <div className="cabinet-shell">
          <Sidebar />
          <main className="cabinet-main">
            <div className="cabinet-main__inner">{children}</div>
          </main>
        </div>
      </body>
    </html>
  );
}
