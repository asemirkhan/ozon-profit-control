'use client';

import { motion } from 'framer-motion';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';

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
              За 7 дней соберём понятный отчёт по вашему магазину на Ozon: покажем прибыль по каждому товару, объясним,
              почему “к выплате” приходит меньше, чем вы ожидали, и дадим список действий — что исправить в первую
              очередь.
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
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.1, ease: 'easeOut' }}
            className="relative"
          >
            <div className="absolute -inset-6 rounded-[30px] bg-gradient-to-tr from-blue-500/30 via-cyan-300/15 to-emerald-400/20 blur-3xl" />
            <div className="relative overflow-hidden rounded-[28px] border border-white/15 bg-[#0b1227]/90 p-4 sm:p-5 backdrop-blur-2xl">
              <div className="relative h-[520px] overflow-hidden rounded-2xl border border-white/10 bg-gradient-to-br from-slate-900/95 via-blue-950/85 to-cyan-950/70 p-4 sm:h-[560px] sm:p-6 lg:h-[620px]">
                <p className="text-sm font-medium text-cyan-100">Сводка по магазину за 30 дней</p>

                <div className="mt-4 grid gap-3 sm:grid-cols-2">
                  <KpiCard title="Выручка за 30 дней" value="2,48 млн ₽" />
                  <KpiCard title="Чистая прибыль" value="418 тыс ₽" />
                  <KpiCard title="Потери из-за расходов" value="264 тыс ₽" tone="negative" />
                  <KpiCard title="Потенциал возврата" value="+280–360 тыс ₽/мес" tone="positive" />
                </div>

                <div className="mt-5 rounded-xl border border-white/15 bg-slate-950/78 p-4">
                  <p className="text-sm font-semibold text-white">Что видно сразу</p>
                  <ul className="mt-2 space-y-1.5 text-sm text-slate-200">
                    <li>• Какие товары в плюс, а какие тянут вниз</li>
                    <li>• Куда уходят деньги: доставка, услуги, реклама</li>
                  </ul>
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
  tone
}: {
  title: string;
  value: string;
  tone?: 'positive' | 'negative';
}) {
  return (
    <div className="rounded-xl border border-white/15 bg-slate-950/82 p-3.5">
      <p className="text-sm text-slate-300">{title}</p>
      <p
        className={`mt-1 text-xl font-semibold ${
          tone === 'negative' ? 'text-rose-300' : tone === 'positive' ? 'text-emerald-300' : 'text-white'
        }`}
      >
        {value}
      </p>
    </div>
  );
}
