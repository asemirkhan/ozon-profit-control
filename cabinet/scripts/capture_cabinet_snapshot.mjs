import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from 'playwright';

const BASE_URL = process.env.CABINET_BASE_URL || 'http://127.0.0.1:3008/cabinet';
const ROOT = path.join(process.cwd(), '..', 'artifacts', 'cabinet_full_snapshot_2026-03-21');

const folders = [
  '01_overview',
  '02_store_economics',
  '03_products_list',
  '04_product_card',
  '05_ads',
  '06_logistics',
  '07_costs',
  '08_filters_states',
  '09_empty_states',
];

async function ensureDirs() {
  await fs.rm(ROOT, { recursive: true, force: true });
  for (const folder of folders) {
    await fs.mkdir(path.join(ROOT, folder), { recursive: true });
  }
}

function out(...parts) {
  return path.join(ROOT, ...parts);
}

async function goto(page, url) {
  await page.goto(url, { waitUntil: 'networkidle' });
  await page.waitForTimeout(500);
}

async function shot(page, relativePath, options = {}) {
  await page.screenshot({
    path: out(relativePath),
    fullPage: true,
    ...options,
  });
}

async function clickByRole(page, role, name) {
  const target = page.getByRole(role, { name }).first();
  await target.click();
  await page.waitForTimeout(500);
}

async function hoverEconomicsChart(page) {
  const chart = page.locator('.recharts-responsive-container').first();
  const box = await chart.boundingBox();
  if (!box) return;
  await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.35);
  await page.waitForTimeout(400);
}

async function capture() {
  await ensureDirs();

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1400 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();

  await goto(page, `${BASE_URL}/profit-dashboard`);
  await shot(page, '01_overview/overview_full.png');

  await goto(page, `${BASE_URL}/economics`);
  await shot(page, '02_store_economics/economics_full.png');
  await hoverEconomicsChart(page);
  await shot(page, '02_store_economics/economics_chart_tooltip.png');
  await clickByRole(page, 'button', 'Расходы');
  await shot(page, '02_store_economics/economics_chart_expenses.png');
  await clickByRole(page, 'button', 'Маржа и доли');
  await shot(page, '02_store_economics/economics_chart_ratios.png');
  await clickByRole(page, 'button', 'Главные товары по прибыли');
  await shot(page, '02_store_economics/economics_products_profit.png');
  await clickByRole(page, 'button', 'Где теряется прибыль');
  await shot(page, '02_store_economics/economics_products_loss.png');
  await clickByRole(page, 'button', 'Самая дорогая логистика');
  await shot(page, '02_store_economics/economics_products_logistics.png');

  await goto(page, `${BASE_URL}/risks`);
  await shot(page, '02_store_economics/risks_full.png');

  await goto(page, `${BASE_URL}/products`);
  await shot(page, '03_products_list/products_all.png');
  await goto(page, `${BASE_URL}/products?view=attention`);
  await shot(page, '03_products_list/products_attention.png');
  await goto(page, `${BASE_URL}/products?view=loss`);
  await shot(page, '03_products_list/products_loss.png');
  await goto(page, `${BASE_URL}/products?view=strong`);
  await shot(page, '03_products_list/products_strong.png');
  await goto(page, `${BASE_URL}/products?view=watch`);
  await shot(page, '03_products_list/products_watch.png');

  await goto(page, `${BASE_URL}/products?q=__nothing_found__`);
  await shot(page, '09_empty_states/products_empty_search.png');

  await goto(page, `${BASE_URL}/products?risk=logistics`);
  await shot(page, '06_logistics/products_risk_logistics.png');

  await goto(page, `${BASE_URL}/products?view=attention&risk=logistics`);
  await shot(page, '08_filters_states/products_attention_logistics_filter.png');

  await goto(page, `${BASE_URL}/products?view=strong`);
  await shot(page, '08_filters_states/products_strong_filter.png');

  await goto(page, `${BASE_URL}/products/mshcm120621`);
  await shot(page, '04_product_card/product_signal_overview.png');
  await clickByRole(page, 'button', 'Реклама');
  await shot(page, '04_product_card/product_signal_ads.png');
  await clickByRole(page, 'button', 'Логистика');
  await shot(page, '04_product_card/product_signal_logistics.png');
  await shot(page, '06_logistics/product_signal_logistics_tab.png');
  await clickByRole(page, 'button', 'Комиссия');
  await shot(page, '04_product_card/product_signal_commission.png');
  await clickByRole(page, 'button', 'Прочие расходы');
  await shot(page, '04_product_card/product_signal_other_costs.png');
  await shot(page, '07_costs/product_signal_other_costs_tab.png');

  await goto(page, `${BASE_URL}/products/2047590184015`);
  await shot(page, '04_product_card/product_strong_action_overview.png');

  await goto(page, `${BASE_URL}/products/2047591296052`);
  await shot(page, '04_product_card/product_normal_overview.png');

  await goto(page, `${BASE_URL}/ads`);
  await shot(page, '05_ads/ads_full.png');

  await goto(page, `${BASE_URL}/wb-costs`);
  await shot(page, '07_costs/wb_costs_full.png');

  const manifest = `# Cabinet Full Snapshot\n\nBase URL: ${BASE_URL}\n\nFolders:\n${folders
    .map((folder) => `- ${folder}`)
    .join('\n')}\n\nNotes:\n- Desktop only.\n- Full-page screenshots.\n- Included states: signal_only, strong_action, no recommendation / normal, empty search.\n- No hypothesis_action state found in current snapshot.\n`;
  await fs.writeFile(out('README.md'), manifest, 'utf8');
  await fs.writeFile(out('09_empty_states/notes.md'), 'No hypothesis_action state found in the current snapshot.\n', 'utf8');

  await browser.close();
}

capture().catch((error) => {
  console.error(error);
  process.exit(1);
});
