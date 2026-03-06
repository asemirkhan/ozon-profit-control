type FooterProps = {
  brandName: string;
  leadUrl: string;
};

export function Footer({ brandName, leadUrl }: FooterProps) {
  return (
    <footer className="border-t border-white/10 py-8">
      <div className="container-wrap flex flex-col items-start justify-between gap-3 text-sm text-slate-400 sm:flex-row sm:items-center">
        <p>© {new Date().getFullYear()} {brandName}. Аналитика прибыли маркетплейсов.</p>
        <a href={leadUrl} target="_blank" rel="noreferrer" className="text-cyan-300 hover:text-cyan-200">
          Получить диагностику
        </a>
      </div>
    </footer>
  );
}
