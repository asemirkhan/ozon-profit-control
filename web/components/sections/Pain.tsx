'use client';

import { motion } from 'framer-motion';

import { Card, CardContent } from '@/components/ui/card';
import { painPoints } from '@/lib/copy';

export function Pain() {
  return (
    <section className="py-16 sm:py-20">
      <div className="container-wrap">
        <h2 className="section-title">Знакомо?</h2>
        <p className="section-subtitle">Эти симптомы почти всегда означают, что деньги “утекают” в деталях, которые не видно в отчётах маркетплейса.</p>

        <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {painPoints.map((point, idx) => (
            <motion.div
              key={point}
              initial={{ opacity: 0, y: 22 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.25 }}
              transition={{ duration: 0.45, delay: idx * 0.05 }}
            >
              <Card className="h-full">
                <CardContent className="flex h-full items-center py-7 text-base text-slate-100">{point}</CardContent>
              </Card>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
