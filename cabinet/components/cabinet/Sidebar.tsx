'use client';

import { usePathname } from 'next/navigation';
import { BarChart3, Megaphone, ShoppingBag, WalletCards } from 'lucide-react';

const navItems = [
  { href: '/economics', label: 'Экономика магазина', icon: BarChart3 },
  { href: '/products', label: 'Товары', icon: ShoppingBag },
  { href: '/ads', label: 'Реклама', icon: Megaphone },
  { href: '/wb-costs', label: 'Расходы WB', icon: WalletCards },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="cabinet-sidebar">
      <div className="cabinet-brand">
        <p className="cabinet-brand__title">Контроль прибыли</p>
        <p className="cabinet-brand__subtitle">Кабинет владельца магазина</p>
      </div>
      <nav className="cabinet-nav">
        {navItems.map((item) => {
          const cabinetHref = `/cabinet${item.href}`;
          const isActive =
            pathname === item.href ||
            pathname === cabinetHref ||
            (item.href.startsWith('/products') && (pathname === '/products' || pathname?.startsWith('/products/'))) ||
            (item.href === '/economics' &&
              (pathname === '/' ||
                pathname === '/economics' ||
                pathname === '/cabinet/economics' ||
                pathname === '/profit-dashboard' ||
                pathname === '/cabinet/profit-dashboard'));
          const Icon = item.icon;
          return (
            <a
              key={item.href}
              href={cabinetHref}
              className={`cabinet-nav__item ${isActive ? 'cabinet-nav__item--active' : ''}`}
            >
              <Icon size={16} />
              <span>{item.label}</span>
            </a>
          );
        })}
      </nav>
    </aside>
  );
}
