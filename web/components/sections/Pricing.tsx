'use client';

import { motion } from 'framer-motion';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { pricingSection } from '@/lib/copy';

type PricingProps = {
  leadUrl: string;
};

export function Pricing({ leadUrl }: PricingProps) {
  return (
    <section className="py-16 sm:py-20">
      <div className="container-wrap">
        <h2 className="section-title">{pricingSection.title}</h2>
        <p className="section-subtitle">{pricingSection.subtitle}</p>

        <div className="mt-10 grid gap-4 lg:grid-cols-3">
          {pricingSection.plans.map((plan, idx) => (
            <motion.div
              key={plan.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.25 }}
              transition={{ duration: 0.45, delay: idx * 0.06 }}
            >
              <Card className={`flex h-full flex-col ${plan.badge ? 'border-cyan-300/40 bg-slate-900/90' : ''}`}>
                <CardHeader>
                  {plan.badge ? (
                    <span className="mb-3 inline-flex w-fit rounded-full border border-cyan-300/40 bg-cyan-500/10 px-3 py-1 text-[11px] font-medium text-cyan-100">
                      {plan.badge}
                    </span>
                  ) : null}
                  <CardTitle>{plan.title}</CardTitle>
                  <p className="mt-2 text-2xl font-semibold text-cyan-200">{plan.price}</p>
                  {plan.note ? <p className="mt-2 text-xs leading-relaxed text-slate-400">{plan.note}</p> : null}
                </CardHeader>
                <CardContent className="flex flex-1 flex-col justify-between gap-6">
                  <ul className="space-y-3 text-sm text-slate-300">
                    {plan.points.map((point) => (
                      <li key={point} className="leading-relaxed">
                        • {point}
                      </li>
                    ))}
                  </ul>
                  <div className="space-y-3">
                    <a href={leadUrl} target="_blank" rel="noreferrer" className="block w-full">
                      <Button className="h-auto w-full whitespace-normal px-4 py-3 text-center leading-tight">{plan.cta}</Button>
                    </a>
                    <p className="text-xs leading-relaxed text-slate-400">{plan.micro}</p>
                  </div>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>

        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.2 }}
          transition={{ duration: 0.45, delay: 0.14 }}
          className="mt-6"
        >
          <Card>
            <CardContent className="p-5 sm:p-6">
              <p className="text-lg font-semibold text-white">{pricingSection.payoff.title}</p>
              <div className="mt-2 space-y-1 text-sm leading-relaxed text-slate-300">
                {pricingSection.payoff.lines.map((line) => (
                  <p key={line}>{line}</p>
                ))}
              </div>
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </section>
  );
}
