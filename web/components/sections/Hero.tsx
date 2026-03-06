'use client';

import Image from 'next/image';
import { motion } from 'framer-motion';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { heroProofs } from '@/lib/copy';

type HeroProps = {
  leadUrl: string;
};

export function Hero({ leadUrl }: HeroProps) {
  return (
    <section className="relative overflow-hidden pb-16 pt-28 sm:pb-24 sm:pt-32">
      <div className="container-wrap">
        <div className="grid items-center gap-10 lg:grid-cols-[0.98fr_1.02fr]">
          <motion.div
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.7, ease: 'easeOut' }}
          >
            <Badge>Аналитика прибыли Ozon / WB</Badge>
            <h1 className="mt-6 max-w-3xl font-[var(--font-sora)] text-4xl font-semibold leading-tight text-white sm:text-5xl lg:text-6xl">
              Покажем, какие товары приносят прибыль — а какие сжирают деньги
            </h1>
            <p className="mt-6 max-w-2xl text-lg text-slate-300">
              За 7 дней соберём понятный отчёт по вашему магазину на Ozon/WB: прибыль по каждому товару, расходы на
              логистику и услуги, “дыры” в выплатах и план действий — что исправить в первую очередь.
            </p>

            <div className="mt-8 flex flex-col gap-3 sm:flex-row">
              <a href={leadUrl} target="_blank" rel="noreferrer">
                <Button size="lg">Получить диагностику за 7 дней</Button>
              </a>
              <a href="#demo">
                <Button variant="secondary" size="lg">
                  Посмотреть демо-отчёт
                </Button>
              </a>
            </div>

            <p className="mt-5 text-sm text-slate-300">
              Подключение через API или выгрузки. Доступ можно отозвать в любой момент.
            </p>

            <div className="mt-8 flex flex-wrap gap-3">
              {heroProofs.map((proof) => (
                <span key={proof} className="rounded-full border border-white/15 bg-white/[0.03] px-4 py-2 text-sm text-slate-200">
                  {proof}
                </span>
              ))}
            </div>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.1, ease: 'easeOut' }}
            className="relative"
          >
            <div className="absolute -inset-6 rounded-[30px] bg-gradient-to-tr from-blue-500/30 via-cyan-300/15 to-emerald-400/20 blur-3xl" />
            <div className="relative overflow-hidden rounded-[28px] border border-white/15 bg-[#0b1227]/90 p-4 sm:p-5 backdrop-blur-2xl">
              <div className="relative h-[520px] overflow-hidden rounded-2xl border border-white/10 sm:h-[560px] lg:h-[640px]">
                <div className="absolute inset-0 bg-gradient-to-br from-slate-900 via-blue-950 to-cyan-950" />
                <Image
                  src="/images/dashboard-1.webp"
                  alt="Панель прибыли магазина с ключевыми метриками и приоритетными действиями"
                  fill
                  priority
                  className="object-cover opacity-5"
                />
                <div className="absolute inset-0 bg-gradient-to-t from-slate-950/95 via-slate-950/65 to-slate-900/25" />

                <div className="relative z-10 flex h-full flex-col p-4 sm:p-6">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="text-sm font-medium text-cyan-100">Демо KPI по магазину за 30 дней</p>
                    <span className="rounded-full border border-cyan-200/40 bg-cyan-200/10 px-3 py-1 text-xs text-cyan-100">
                      Обновление: ежедневно
                    </span>
                  </div>

                  <div className="mt-4 grid gap-3 sm:grid-cols-2">
                    <KpiCard title="Выручка за 30 дней" value="2,48 млн ₽" note="+12% к прошлому месяцу" />
                    <KpiCard title="Чистая прибыль" value="418 тыс ₽" note="маржа после рекламы 16,9%" tone="positive" />
                    <KpiCard title="Потери на расходах" value="−264 тыс ₽" note="логистика, услуги, корректировки" tone="negative" />
                    <KpiCard title="Потенциал возврата" value="+55–110 тыс ₽/мес" note="после внедрения ТОП-5 действий" tone="positive" />
                  </div>

                  <div className="mt-4 rounded-xl border border-white/15 bg-slate-950/82 p-3.5">
                    <p className="text-xs uppercase tracking-wide text-slate-300">Ключевые утечки</p>
                    <div className="mt-2 grid gap-2 text-sm text-slate-100 sm:grid-cols-3">
                      <p className="rounded-lg border border-white/10 bg-white/[0.02] px-2.5 py-2">
                        Логистика: <span className="font-semibold text-rose-300">−128 400 ₽</span>
                      </p>
                      <p className="rounded-lg border border-white/10 bg-white/[0.02] px-2.5 py-2">
                        Услуги: <span className="font-semibold text-rose-300">−96 700 ₽</span>
                      </p>
                      <p className="rounded-lg border border-white/10 bg-white/[0.02] px-2.5 py-2">
                        Реклама: <span className="font-semibold text-rose-300">−73 500 ₽</span>
                      </p>
                    </div>
                  </div>

                  <div className="mt-3 rounded-xl border border-white/15 bg-slate-950/82 p-3.5">
                    <p className="text-xs uppercase tracking-wide text-slate-300">Что делаем в первую очередь</p>
                    <ul className="mt-2 space-y-1.5 text-sm text-slate-100">
                      <li>• Перенастраиваем убыточную рекламу и оставляем прибыльные кампании</li>
                      <li>• Проверяем габариты и упаковку на товарах с дорогой логистикой</li>
                      <li>• Ставим Telegram-алерты на резкий рост расходов и просадку маржи</li>
                    </ul>
                  </div>

                  <div className="mt-auto pt-4 text-base text-slate-200">
                    Демо-эффект по итогам внедрения: <span className="font-semibold text-cyan-100">+55–110 тыс ₽/мес</span>
                  </div>
                </div>
              </div>
            </div>
          </motion.div>
        </div>
      </div>
    </section>
  );
}

function KpiCard({
  title,
  value,
  note,
  tone
}: {
  title: string;
  value: string;
  note: string;
  tone?: 'positive' | 'negative';
}) {
  return (
    <div className="rounded-xl border border-white/15 bg-slate-950/82 p-3">
      <p className="text-xs text-slate-300">{title}</p>
      <p
        className={`mt-1 text-lg font-semibold ${
          tone === 'negative' ? 'text-rose-300' : tone === 'positive' ? 'text-emerald-300' : 'text-white'
        }`}
      >
        {value}
      </p>
      <p className="mt-1 text-xs leading-relaxed text-slate-300">{note}</p>
    </div>
  );
}
