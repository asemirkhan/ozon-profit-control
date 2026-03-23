const currencyFmt = new Intl.NumberFormat('ru-RU', {
  style: 'currency',
  currency: 'RUB',
  maximumFractionDigits: 0
});

const numberFmt = new Intl.NumberFormat('ru-RU');

const percentFmt = new Intl.NumberFormat('ru-RU', {
  style: 'percent',
  maximumFractionDigits: 1
});

const decimalFmt = new Intl.NumberFormat('ru-RU', {
  maximumFractionDigits: 2
});

export function formatMoney(value: number | null | undefined): string {
  if (value === null || value === undefined) return currencyFmt.format(0);
  return currencyFmt.format(Number.isFinite(value) ? value : 0);
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined) return numberFmt.format(0);
  return numberFmt.format(Number.isFinite(value) ? value : 0);
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return percentFmt.format(0);
  return percentFmt.format(Number.isFinite(value) ? value : 0);
}

export function formatRatio(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'н/д';
  return formatPercent(value);
}

export function ruleLabel(code: string | null | undefined): string {
  if (!code) return 'нет критичных отклонений';
  const map: Record<string, string> = {
    negative_profit_after_ads: 'Убыток после рекламы',
    ads_eating_profit: 'Реклама съедает прибыль',
    ads_without_sales: 'Реклама без продаж',
    high_logistics_share: 'Слишком дорогая логистика',
    low_margin_after_ads: 'Слишком низкая маржа',
    stockout_risk: 'Риск дефицита'
  };
  return map[code] || 'Другая проблема';
}

export function attributionLabel(value: string | null | undefined): string {
  const map: Record<string, string> = {
    exact_match: 'Расходы связаны с товарами точно',
    fallback_match: 'Часть расходов распределена приблизительно',
    unattributed: 'Часть расходов не удалось точно связать с товарами'
  };
  if (!value) return 'Нет данных';
  return map[value] || 'Данных недостаточно для оценки';
}

export function confidenceLabel(value: string | null | undefined): string {
  const map: Record<string, string> = {
    high: 'высокая',
    medium: 'средняя',
    low: 'низкая',
    reliable: 'надежная',
    estimated: 'ориентировочная',
    preliminary: 'предварительная'
  };
  if (!value) return 'Нет данных';
  return map[value] || 'нет данных';
}

export function confidenceHint(value: string | null | undefined): string {
  const map: Record<string, string> = {
    high: 'Большинство расходов связано с товарами корректно.',
    medium: 'Часть расходов распределена приблизительно, эффект нужно трактовать осторожно.',
    low: 'Существенная часть расходов пока не разнесена, оценка предварительная.',
    reliable: 'Прибыль можно использовать как рабочую управленческую метрику.',
    estimated: 'Часть цифр рассчитана приблизительно, это ориентир для действий.',
    preliminary: 'Пока воспринимайте эффект как предварительную оценку.'
  };
  if (!value) return 'Нет данных о точности расчёта.';
  return map[value] || 'Нет данных о точности расчёта.';
}

export function actionTypeLabel(value: string | null | undefined): string {
  const map: Record<string, string> = {
    ads: 'Реклама',
    inventory: 'Остатки',
    logistics: 'Логистика',
    margin: 'Маржа'
  };
  if (!value) return 'Нет данных';
  return map[value] || 'Другое';
}

export function effectTypeLabel(value: string | null | undefined): string {
  const map: Record<string, string> = {
    profit_uplift: 'Потенциал роста прибыли',
    revenue_at_risk: 'Возможная потеря выручки',
    loss_reduction: 'Снижение потерь',
    working_capital_release: 'Ускорение оборачиваемости'
  };
  if (!value) return 'Оценка эффекта';
  return map[value] || 'Оценка эффекта';
}

export function effectTypeHint(value: string | null | undefined): string {
  const map: Record<string, string> = {
    profit_uplift: 'Эффект относится к прибыли.',
    revenue_at_risk: 'Это возможная потеря выручки, а не гарантированный рост прибыли.',
    loss_reduction: 'Эффект — снижение текущих потерь.',
    working_capital_release: 'Эффект — высвобождение денег из медленного остатка.'
  };
  if (!value) return 'Эффект рассчитан ориентировочно.';
  return map[value] || 'Эффект рассчитан ориентировочно.';
}

export function effectActionLabel(value: string | null | undefined): string {
  const map: Record<string, string> = {
    profit_uplift: 'увеличить прибыль',
    revenue_at_risk: 'сократить потерю выручки',
    loss_reduction: 'сократить потери',
    working_capital_release: 'ускорить оборачиваемость'
  };
  if (!value) return 'улучшить экономику';
  return map[value] || 'улучшить экономику';
}

export function storeHealthHuman(status: string | null | undefined): string {
  if (status === 'losing_money') return 'Магазин теряет деньги на части ассортимента';
  if (status === 'at_risk') return 'Прибыль магазина теряется сильнее обычного';
  return 'Состояние магазина стабильное';
}

export function topProblemFamilyLabel(value: string | null | undefined): string {
  const map: Record<string, string> = {
    ads: 'реклама',
    inventory: 'остатки и пополнение',
    logistics: 'логистика и комиссии',
    margin: 'маржинальность',
    general: 'экономика товара'
  };
  if (!value) return 'экономика товара';
  return map[value] || 'экономика товара';
}

export function stockRiskUrgency(daysOfStock: number | null | undefined): string {
  if (daysOfStock === null || daysOfStock === undefined) return 'Срок остатка не определен';
  if (daysOfStock <= 3) return 'Критично';
  if (daysOfStock <= 7) return 'Срочно';
  if (daysOfStock <= 14) return 'Нужно контролировать';
  return 'Риск низкий';
}

export function formatDays(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'н/д';
  return `${decimalFmt.format(value)} дн.`;
}

const productNameMap: Record<string, string> = {
  'Core Bottle 500ml': 'Базовая бутылка 500 мл',
  'Core Lunchbox': 'Базовый ланч-бокс',
  'Core Cotton Tee': 'Базовая хлопковая футболка',
  'Ads Drain Mat': 'Коврик с убыточной рекламой',
  'Return Chaos Lamp': 'Лампа с возвратами',
  'Aggro Campaign Mug': 'Кружка с перегретой кампанией',
  'Aggro Campaign Pen': 'Ручка с перегретой кампанией',
  'Bulky Box Set': 'Габаритный набор коробок',
  'Heavy Stand': 'Тяжёлая подставка',
  'Thin Margin Socks': 'Носки с тонкой маржой',
  'Thin Margin Cover': 'Чехол с тонкой маржой',
  'Fast Seller Alpha': 'Быстрый хит Альфа',
  'Fast Seller Beta': 'Быстрый хит Бета',
  'Spend No Revenue': 'Расход без выручки',
  'Silent Ad Item': 'Товар с рекламой без продаж',
  'WB Return Chaos Lamp': 'WB Лампа с возвратами',
  'WB Fast Seller Alpha': 'WB Быстрый хит Альфа'
};

export function normalizeUiText(value: string | null | undefined): string {
  const cleaned = (value || '').trim();
  return cleaned || 'Нет данных';
}

export function displayProductName(productName: string | null | undefined, fallback?: string): string {
  const cleaned = (productName || '').trim();
  if (!cleaned) {
    return fallback || 'Товар без названия';
  }
  const translated = productNameMap[cleaned] || cleaned;
  return translated;
}
