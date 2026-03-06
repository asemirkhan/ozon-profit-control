'use client';

import Image from 'next/image';
import { motion } from 'framer-motion';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { actionPlan, costShare, productReportRows, profitByDay, topProducts } from '@/lib/demoData';

const PIE_COLORS = ['#38bdf8', '#22d3ee', '#2dd4bf', '#93c5fd'];

function money(value: number) {
  const sign = value < 0 ? '−' : '';
  return `${sign}${new Intl.NumberFormat('ru-RU').format(Math.abs(value))} ₽`;
}

function renderStrongText(text: string) {
  return text.split(/(\*\*.*?\*\*)/g).map((part, idx) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <strong key={`${part}-${idx}`} className="font-semibold text-slate-100">
          {part.slice(2, -2)}
        </strong>
      );
    }
    return <span key={`${part}-${idx}`}>{part}</span>;
  });
}

export function Demo() {
  return (
    <section id="demo" className="py-16 sm:py-20">
      <div className="container-wrap">
        <h2 className="section-title">Демо-отчёт</h2>
        <p className="section-subtitle">
          Пример того, что будет видно в вашем магазине: прибыль по товарам, основные потери и 5 приоритетных действий
          на ближайшие 14 дней.
        </p>

        <div className="mt-10 grid gap-5 lg:grid-cols-2">
          <motion.div initial={{ opacity: 0, y: 20 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true }}>
            <Card className="overflow-hidden border-white/20 bg-slate-950/80">
              <div className="relative h-32 overflow-hidden border-b border-white/15">
                <Image src="/images/dashboard-1.webp" alt="Превью отчёта по прибыли" fill className="object-cover opacity-70" />
                <div className="absolute inset-0 bg-gradient-to-r from-slate-950/90 via-slate-950/60 to-transparent" />
                <div className="relative z-10 p-4">
                  <p className="text-xs uppercase tracking-wide text-cyan-100">Коротко по магазину за 30 дней</p>
                </div>
              </div>

              <CardContent className="space-y-4 p-4 sm:p-5">
                <div className="grid gap-2 sm:grid-cols-2">
                  <SummaryCard title="Выручка за 30 дней" value="2,48 млн ₽" />
                  <SummaryCard title="Чистая прибыль" value="418 тыс ₽" />
                  <SummaryCard title="Маржа после рекламы" value="16,9%" />
                  <SummaryCard
                    title="Денег теряется из-за расходов"
                    value="264 тыс ₽"
                    note="Доставка, услуги, корректировки и прочие списания, которые съедают маржу."
                  />
                </div>

                <div className="rounded-xl border border-white/15 bg-slate-900/80 p-3 sm:p-4">
                  <p className="text-sm font-semibold text-white">
                    Товары, которые делают прибыль — и которые тянут вниз
                  </p>

                  <div className="mt-3 overflow-x-auto">
                    <table className="min-w-full text-left text-xs sm:text-sm">
                      <thead>
                        <tr className="text-slate-300">
                          <th className="pb-2 pr-3 font-medium">Товар</th>
                          <th className="pb-2 pr-3 font-medium">Продано, шт</th>
                          <th className="pb-2 pr-3 font-medium">Прибыль за 30 дней</th>
                          <th className="pb-2 font-medium">Маржа</th>
                        </tr>
                      </thead>
                      <tbody className="text-slate-100">
                        {productReportRows.map((row) => {
                          const isPositive = row.profit >= 0;
                          return (
                            <tr key={row.name} className="border-t border-white/10">
                              <td className="py-2 pr-3">{row.name}</td>
                              <td className="py-2 pr-3">{new Intl.NumberFormat('ru-RU').format(row.sold)} шт</td>
                              <td className={`py-2 pr-3 font-medium ${isPositive ? 'text-emerald-300' : 'text-rose-300'}`}>
                                {money(row.profit)}
                              </td>
                              <td className={`py-2 font-medium ${isPositive ? 'text-emerald-300' : 'text-rose-300'}`}>
                                {row.margin > 0 ? '+' : ''}
                                {row.margin.toFixed(1)}%
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>

                  <p className="mt-3 text-xs text-slate-300">
                    Если товар в минус — система показывает, что именно съедает прибыль: реклама, доставка, услуги или
                    низкая цена.
                  </p>
                </div>
              </CardContent>
            </Card>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.06 }}
            viewport={{ once: true }}
          >
            <Card className="overflow-hidden border-white/20 bg-slate-950/80">
              <div className="relative h-32 overflow-hidden border-b border-white/15">
                <Image src="/images/dashboard-2.webp" alt="Превью отчёта по потерям" fill className="object-cover opacity-70" />
                <div className="absolute inset-0 bg-gradient-to-r from-slate-950/90 via-slate-950/60 to-transparent" />
                <div className="relative z-10 p-4">
                  <p className="text-xs uppercase tracking-wide text-cyan-100">Куда уходят деньги (потери за 30 дней)</p>
                </div>
              </div>

              <CardContent className="space-y-4 p-4 sm:p-5">
                <div className="grid gap-2 sm:grid-cols-2">
                  <LeakCard title="Доставка (логистика)" value="−128 400 ₽" />
                  <LeakCard title="Услуги маркетплейса" value="−96 700 ₽" />
                  <LeakCard title="Реклама" value="−73 500 ₽" />
                  <LeakCard
                    title="Возвраты и корректировки"
                    value="−38 900 ₽"
                    note="Компенсации, удержания, перерасчёты."
                  />
                </div>

                <div className="rounded-xl border border-white/15 bg-slate-900/80 p-3 sm:p-4">
                  <p className="text-sm font-semibold text-white">ТОП-5 действий на ближайшие 14 дней</p>
                  <ol className="mt-3 space-y-3 text-sm text-slate-100">
                    {actionPlan.map((item, idx) => (
                      <li key={item.title} className="rounded-lg border border-white/10 bg-white/[0.03] p-3">
                        <p className="font-medium text-white">
                          {idx + 1}) {renderStrongText(item.title)}
                        </p>
                        <p className="mt-1 text-xs leading-relaxed text-slate-300">
                          <span className="font-medium text-slate-200">Почему:</span> {renderStrongText(item.reason)}
                        </p>
                        <p className="mt-1 text-xs leading-relaxed text-slate-300">
                          <span className="font-medium text-slate-200">Что сделать:</span> {renderStrongText(item.action)}
                        </p>
                        <p className="mt-1 text-xs leading-relaxed text-cyan-100">{renderStrongText(item.effect)}</p>
                      </li>
                    ))}
                  </ol>
                </div>
              </CardContent>
            </Card>
          </motion.div>
        </div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.1 }}
          viewport={{ once: true }}
          className="mt-5"
        >
          <Card className="overflow-hidden border-white/20 bg-slate-950/80">
            <div className="relative h-28 overflow-hidden border-b border-white/15">
              <Image
                src="/images/telegram-alerts.webp"
                alt="Telegram-алерты по прибыли и расходам"
                fill
                className="object-cover opacity-70"
              />
              <div className="absolute inset-0 bg-gradient-to-r from-slate-950/90 via-slate-950/55 to-transparent" />
              <div className="relative z-10 p-4">
                <p className="text-xs uppercase tracking-wide text-cyan-100">Уведомления и контроль через Telegram-бота</p>
              </div>
            </div>
            <CardContent className="grid gap-3 p-4 sm:grid-cols-3">
              <InfoCard
                title="Реклама в минус"
                text="7 товаров уходят в минус после рекламы. Потери: −142 000 ₽ за 30 дней, бот присылает алерт в тот же день."
              />
              <InfoCard
                title="Рост расходов"
                text="Логистика выросла на +23% за неделю: +86 400 ₽ лишних расходов. Бот предупреждает и показывает, где рост начался."
              />
              <InfoCard
                title="Неразнесённые списания"
                text="Есть удержания на −57 300 ₽, часть приходит общей строкой. Бот присылает список операций, которые нужно проверить первыми."
              />
            </CardContent>
          </Card>
        </motion.div>

        <div className="mt-8 grid gap-4 lg:grid-cols-3">
          <Card className="border-white/20 bg-slate-950/80">
            <CardHeader>
              <CardTitle className="text-base text-slate-50">Прибыль по дням</CardTitle>
            </CardHeader>
            <CardContent className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={profitByDay}>
                  <defs>
                    <linearGradient id="profitGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#38bdf8" stopOpacity={0.6} />
                      <stop offset="95%" stopColor="#38bdf8" stopOpacity={0.08} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="day" stroke="#cbd5e1" tickLine={false} axisLine={false} fontSize={11} />
                  <YAxis
                    stroke="#cbd5e1"
                    tickLine={false}
                    axisLine={false}
                    width={58}
                    tickFormatter={(value) => `${Math.round(value / 1000)}k ₽`}
                    fontSize={11}
                  />
                  <Tooltip
                    contentStyle={{ background: '#0f172a', border: '1px solid rgba(148,163,184,0.4)' }}
                    formatter={(value: number) => money(value)}
                  />
                  <Area type="monotone" dataKey="profit" stroke="#38bdf8" strokeWidth={2} fill="url(#profitGradient)" />
                </AreaChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card className="border-white/20 bg-slate-950/80">
            <CardHeader>
              <CardTitle className="text-base text-slate-50">Доли расходов</CardTitle>
            </CardHeader>
            <CardContent className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Tooltip
                    contentStyle={{ background: '#0f172a', border: '1px solid rgba(148,163,184,0.4)' }}
                    formatter={(value: number) => `${value}%`}
                  />
                  <Pie data={costShare} dataKey="value" nameKey="name" innerRadius={52} outerRadius={78} paddingAngle={3}>
                    {costShare.map((entry, index) => (
                      <Cell key={entry.name} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                    ))}
                  </Pie>
                </PieChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>

          <Card className="border-white/20 bg-slate-950/80">
            <CardHeader>
              <CardTitle className="text-base text-slate-50">ТОП товаров по прибыли</CardTitle>
            </CardHeader>
            <CardContent className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={topProducts} layout="vertical" margin={{ top: 4, right: 0, left: 0, bottom: 4 }}>
                  <XAxis type="number" hide />
                  <YAxis
                    type="category"
                    dataKey="name"
                    stroke="#e2e8f0"
                    tickLine={false}
                    axisLine={false}
                    width={130}
                    fontSize={10}
                  />
                  <Tooltip
                    contentStyle={{ background: '#0f172a', border: '1px solid rgba(148,163,184,0.4)' }}
                    formatter={(value: number) => money(value)}
                  />
                  <Bar dataKey="profit" fill="#22d3ee" radius={[0, 8, 8, 0]} barSize={14} />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </div>

        <p className="mt-5 text-sm text-slate-300">Демо-пример. В реальном проекте подключается ваш магазин и ваши товары.</p>
      </div>
    </section>
  );
}

function SummaryCard({ title, value, note }: { title: string; value: string; note?: string }) {
  return (
    <div className="rounded-xl border border-white/15 bg-slate-900/80 p-3">
      <p className="text-[11px] text-slate-300">{title}</p>
      <p className="mt-1 text-sm font-semibold text-white">{value}</p>
      {note ? <p className="mt-1 text-[11px] leading-relaxed text-slate-400">{note}</p> : null}
    </div>
  );
}

function LeakCard({ title, value, note }: { title: string; value: string; note?: string }) {
  return (
    <div className="rounded-xl border border-white/15 bg-slate-900/80 p-3">
      <p className="text-[11px] text-slate-300">{title}</p>
      <p className="mt-1 text-sm font-semibold text-rose-200">{value}</p>
      {note ? <p className="mt-1 text-[11px] text-slate-400">{note}</p> : null}
    </div>
  );
}

function InfoCard({ title, text }: { title: string; text: string }) {
  return (
    <div className="rounded-xl border border-white/15 bg-slate-900/80 p-3">
      <p className="text-sm font-semibold text-white">{title}</p>
      <p className="mt-1 text-xs leading-relaxed text-slate-300">{text}</p>
    </div>
  );
}
