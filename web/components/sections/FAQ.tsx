'use client';

import { motion } from 'framer-motion';

import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion';
import { faq } from '@/lib/copy';

export function FAQ() {
  return (
    <section className="py-16 sm:py-20">
      <div className="container-wrap">
        <h2 className="section-title">FAQ</h2>

        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.2 }}
          transition={{ duration: 0.5 }}
          className="mt-8"
        >
          <Accordion type="single" collapsible className="space-y-3">
            {faq.map((entry, idx) => (
              <AccordionItem key={entry.q} value={`item-${idx}`}>
                <AccordionTrigger>{entry.q}</AccordionTrigger>
                <AccordionContent>{entry.a}</AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        </motion.div>
      </div>
    </section>
  );
}
