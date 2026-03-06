'use client';

import { motion } from 'framer-motion';

import { Button } from '@/components/ui/button';

type FinalCTAProps = {
  leadUrl: string;
};

export function FinalCTA({ leadUrl }: FinalCTAProps) {
  return (
    <section className="pb-16 pt-8 sm:pb-24">
      <div className="container-wrap">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.25 }}
          transition={{ duration: 0.55 }}
          className="relative overflow-hidden rounded-[28px] border border-cyan-300/25 bg-gradient-to-br from-cyan-300/15 via-blue-500/10 to-emerald-300/15 p-8 sm:p-12"
        >
          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_20%_20%,rgba(255,255,255,0.24),transparent_45%)]" />
          <h2 className="relative z-10 max-w-3xl font-[var(--font-sora)] text-3xl font-semibold text-white sm:text-4xl">
            Покажем, где теряется прибыль — и что делать, чтобы её вернуть
          </h2>
          <div className="relative z-10 mt-6">
            <a href={leadUrl} target="_blank" rel="noreferrer">
              <Button size="lg">Оставить заявку</Button>
            </a>
          </div>
          <p className="relative z-10 mt-4 text-sm text-slate-200">Можно начать с одного магазина. Ответ в течение дня.</p>
        </motion.div>
      </div>
    </section>
  );
}
