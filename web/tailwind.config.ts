import type { Config } from 'tailwindcss';

const config: Config = {
  darkMode: ['class'],
  content: ['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}', './lib/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        background: 'hsl(230 30% 7%)',
        foreground: 'hsl(210 25% 95%)',
        panel: 'hsl(228 29% 12%)',
        border: 'hsl(227 26% 24%)',
        accent: 'hsl(193 89% 61%)',
        accent2: 'hsl(154 80% 48%)'
      },
      boxShadow: {
        glow: '0 0 0 1px rgba(90, 140, 255, 0.25), 0 20px 60px rgba(26, 63, 130, 0.35)',
        card: '0 20px 80px rgba(5, 8, 22, 0.45)'
      },
      backgroundImage: {
        'grid-soft':
          'linear-gradient(to right, rgba(127,151,255,0.08) 1px, transparent 1px), linear-gradient(to bottom, rgba(127,151,255,0.08) 1px, transparent 1px)'
      }
    }
  },
  plugins: []
};

export default config;
