'use client';

import { motion } from 'framer-motion';

import { Card, CardContent } from '@/components/ui/card';
import { firstWeekInsights } from '@/lib/copy';

export function WhatYouGet() {
  return (
    <section className="py-16 sm:py-20">
      <div className="container-wrap">
        <h2 className="section-title">Что вы поймёте уже в первую неделю</h2>
        <p className="section-subtitle">
          Без долгого внедрения и сложных терминов. Уже в первую неделю становится видно, где магазин теряет прибыль и
          что делать дальше.
        </p>

        <div className="mt-10 grid gap-4 md:grid-cols-3">
          {firstWeekInsights.map((item, idx) => (
            <motion.div
              key={item.title}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.2 }}
              transition={{ duration: 0.5, delay: idx * 0.06 }}
            >
              <Card className="h-full">
                <CardContent className="space-y-3">
                  <p className="text-lg font-semibold text-white">{item.title}</p>
                  <p className="text-sm leading-relaxed text-slate-300">{item.text}</p>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
