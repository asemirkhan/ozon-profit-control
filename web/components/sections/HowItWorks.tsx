'use client';

import { motion } from 'framer-motion';

import { Card, CardContent, CardTitle } from '@/components/ui/card';
import { steps } from '@/lib/copy';

export function HowItWorks() {
  return (
    <section className="py-16 sm:py-20">
      <div className="container-wrap">
        <h2 className="section-title">Как это работает</h2>

        <div className="mt-10 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
          {steps.map((step, idx) => (
            <motion.div
              key={step}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true, amount: 0.25 }}
              transition={{ duration: 0.45, delay: idx * 0.07 }}
            >
              <Card className="h-full">
                <CardContent className="space-y-4">
                  <span className="inline-flex h-8 w-8 items-center justify-center rounded-full border border-cyan-300/50 bg-cyan-300/10 text-sm font-semibold text-cyan-200">
                    {idx + 1}
                  </span>
                  <CardTitle className="text-lg leading-snug">{step}</CardTitle>
                </CardContent>
              </Card>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
