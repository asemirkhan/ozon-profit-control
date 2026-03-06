import { FinalCTA } from '@/components/sections/FinalCTA';
import { FAQ } from '@/components/sections/FAQ';
import { Footer } from '@/components/sections/Footer';
import { Hero } from '@/components/sections/Hero';
import { HowItWorks } from '@/components/sections/HowItWorks';
import { Pain } from '@/components/sections/Pain';
import { Pricing } from '@/components/sections/Pricing';
import { Demo } from '@/components/sections/Demo';
import { WhatYouGet } from '@/components/sections/WhatYouGet';
import { Button } from '@/components/ui/button';
import { brandName, leadUrl } from '@/lib/copy';

function Divider() {
  return (
    <div className="container-wrap">
      <div className="glow-divider" />
    </div>
  );
}

export default function HomePage() {
  return (
    <main>
      <div className="pointer-events-none absolute inset-0 -z-10 bg-grid-soft opacity-20" />

      <div className="fixed right-4 top-4 z-50 sm:right-8 sm:top-6">
        <a href={leadUrl} target="_blank" rel="noreferrer" className="inline-block">
          <Button size="sm">Получить диагностику</Button>
        </a>
      </div>

      <Hero leadUrl={leadUrl} />
      <Divider />
      <Pain />
      <Divider />
      <HowItWorks />
      <Divider />
      <WhatYouGet />
      <Divider />
      <Demo />
      <Divider />
      <Pricing leadUrl={leadUrl} />
      <Divider />
      <FAQ />
      <Divider />
      <FinalCTA leadUrl={leadUrl} />
      <Footer brandName={brandName} leadUrl={leadUrl} />
    </main>
  );
}
