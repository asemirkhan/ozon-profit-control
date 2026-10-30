'use client';

import { motion } from 'framer-motion';

import { Card, CardContent, CardTitle } from '@/components/ui/card';
import { firstWeekSteps } from '@/lib/copy';

export function HowItWorks() {
  return (
    <section className="py-16 sm:py-20">
      <div className="container-wrap">
        <h2 className="section-title">Как выглядит первая неделя работы</h2>
        <p className="section-subtitle">
          Без долгого внедрения и сложных терминов. За 7 дней становится понятно, где магазин зарабатывает меньше, чем
          должен.
        </p>

        <div className="mt-10 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {firstWeekSteps.map((step, idx) => (
            <motion.div
              key={step.day}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.25 }}
              transition={{ duration: 0.45, delay: idx * 0.07 }}
            >
              <Card className="h-full">
                <CardContent className="space-y-3">
                  <span className="inline-flex rounded-full border border-cyan-300/50 bg-cyan-300/10 px-3 py-1 text-xs font-semibold text-cyan-200">
                    {step.day}
                  </span>
                  <CardTitle className="text-lg leading-snug">{step.text}</CardTitle>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
