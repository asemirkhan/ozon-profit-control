'use client';

import { CheckCircle2 } from 'lucide-react';
import { motion } from 'framer-motion';

import { Card, CardContent } from '@/components/ui/card';
import { outcomes } from '@/lib/copy';

export function WhatYouGet() {
  return (
    <section className="py-16 sm:py-20">
      <div className="container-wrap">
        <h2 className="section-title">Что вы получите</h2>
        <p className="section-subtitle">Не “сырые таблицы”, а конкретную картину: где прибыль, где просадки и что делать уже в ближайшие 14 дней.</p>

        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.2 }}
          transition={{ duration: 0.55 }}
          className="mt-10"
        >
          <Card>
            <CardContent className="grid gap-4 py-7 sm:grid-cols-2">
              {outcomes.map((item) => (
                <div key={item} className="flex items-start gap-3 rounded-xl border border-white/10 bg-white/[0.02] p-4">
                  <CheckCircle2 className="mt-0.5 h-5 w-5 text-cyan-300" />
                  <p className="text-sm text-slate-100 sm:text-base">{item}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </section>
  );
}
