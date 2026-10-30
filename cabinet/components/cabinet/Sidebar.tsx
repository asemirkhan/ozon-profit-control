'use client';

import { usePathname } from 'next/navigation';
import { BarChart3, ShoppingBag } from 'lucide-react';

export function Sidebar() {
  const pathname = usePathname();
  const demoMode = pathname?.startsWith('/demo');
  const navItems = demoMode
    ? [
        { href: '/demo/economics', label: 'Магазин', icon: BarChart3 },
        { href: '/demo/products', label: 'Товары', icon: ShoppingBag },
      ]
    : [
        { href: '/economics', label: 'Магазин', icon: BarChart3 },
        { href: '/products', label: 'Товары', icon: ShoppingBag },
      ];

  return (
    <header className="cabinet-topbar">
      <div className="cabinet-topbar__inner">
        <a href={demoMode ? '/demo/economics' : '/economics'} className="cabinet-brand cabinet-brand--top">
          <div>
            <p className="cabinet-brand__title">Контроль прибыли</p>
            <p className="cabinet-brand__subtitle">Кабинет владельца магазина</p>
          </div>
        </a>

        <div className="cabinet-store-switch" aria-label="Выбор магазина">
          <a href="/economics" className={!demoMode ? 'cabinet-store-switch__item cabinet-store-switch__item--active' : 'cabinet-store-switch__item'}>
            Реальный магазин
          </a>
          <a href="/demo/economics" className={demoMode ? 'cabinet-store-switch__item cabinet-store-switch__item--active' : 'cabinet-store-switch__item'}>
            LUNARO HOME · демо
          </a>
        </div>

        <nav className="cabinet-nav cabinet-nav--top">
          {navItems.map((item) => {
            const isActive =
              pathname === item.href ||
              (item.href.includes('/products') && (pathname === item.href || pathname?.startsWith(`${item.href}/`))) ||
              (item.href.endsWith('/economics') &&
                (pathname === '/' ||
                  pathname === item.href ||
                  pathname === '/profit-dashboard'));
            const Icon = item.icon;
            return (
              <a
                key={item.href}
                href={item.href}
                className={`cabinet-nav__item ${isActive ? 'cabinet-nav__item--active' : ''}`}
              >
                <Icon size={16} />
                <span>{item.label}</span>
              </a>
            );
          })}
        </nav>
      </div>
    </header>
  );
}
