'use client';

import { motion } from 'framer-motion';

import { Card, CardContent } from '@/components/ui/card';

export function Trust() {
  return (
    <section className="py-16 sm:py-20">
      <div className="container-wrap">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.25 }}
          transition={{ duration: 0.5 }}
        >
          <Card className="border-cyan-300/25 bg-gradient-to-br from-cyan-300/10 via-blue-500/10 to-emerald-300/10">
            <CardContent className="p-6 sm:p-8">
              <h2 className="text-2xl font-semibold text-white sm:text-3xl">
                Подходит для магазинов с оборотом от 1 до 10 млн ₽ в месяц
              </h2>
              <p className="mt-3 max-w-3xl text-slate-200">
                Берём ограниченное число клиентов на старте, чтобы глубоко погружаться в цифры и реально доводить
                рекомендации до результата.
              </p>
            </CardContent>
          </Card>
        </motion.div>
      </div>
    </section>
  );
}
