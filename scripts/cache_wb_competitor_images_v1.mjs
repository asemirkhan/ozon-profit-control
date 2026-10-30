import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import path from 'node:path';

const ROOT = process.cwd();
const PACK_PATH = path.join(ROOT, 'cabinet', 'data', 'profit_control_cabinet_human_pack_v1.json');
const OUTPUT_DIR = path.join(ROOT, 'cabinet', 'public', 'wb-competitors');
const MANIFEST_PATH = path.join(ROOT, 'cabinet', 'data', 'wb_competitor_image_manifest.json');
const PRODUCT_OUTPUT_DIR = path.join(ROOT, 'cabinet', 'public', 'wb-products');
const PRODUCT_MANIFEST_PATH = path.join(ROOT, 'cabinet', 'data', 'wb_product_image_manifest.json');

function buildImageUrl(host, nmId) {
  const numeric = Number(nmId);
  const vol = Math.floor(numeric / 100000);
  const part = Math.floor(numeric / 1000);
  return `https://${host}/vol${vol}/part${part}/${nmId}/images/c516x688/1.webp`;
}

async function fileExists(filePath) {
  try {
    const fileStat = await stat(filePath);
    return fileStat.isFile() && fileStat.size > 0;
  } catch {
    return false;
  }
}

async function resolveAndDownload(nmId) {
  const hosts = Array.from({ length: 40 }, (_, offset) => `basket-${String(offset + 1).padStart(2, '0')}.wbbasket.ru`);

  for (let index = 0; index < hosts.length; index += 8) {
    const batch = hosts.slice(index, index + 8).map((host) => {
      const url = buildImageUrl(host, nmId);
      return (async () => {
        const response = await fetch(url, {
          headers: {
            'user-agent': 'Mozilla/5.0',
            accept: 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
          },
          signal: AbortSignal.timeout(2600),
        });

        if (!response.ok) {
          throw new Error(`bad_status_${response.status}`);
        }

        const arrayBuffer = await response.arrayBuffer();
        const buffer = Buffer.from(arrayBuffer);
        if (!buffer.length) {
          throw new Error('empty_body');
        }

        return { buffer, sourceUrl: url };
      })();
    });

    try {
      return await Promise.any(batch);
    } catch {
      continue;
    }
  }

  return null;
}

function normalizeNmId(value) {
  if (value === null || value === undefined) return null;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return null;
  return String(Math.trunc(numeric));
}

async function cacheImages({ ids, outputDir, publicPrefix, manifestPath, label }) {
  await mkdir(outputDir, { recursive: true });

  const manifest = {};
  const downloaded = [];
  const skipped = [];
  const missing = [];

  for (let index = 0; index < ids.length; index += 4) {
    const batch = ids.slice(index, index + 4);
    const batchResults = await Promise.all(
      batch.map(async (nmId) => {
        const outputPath = path.join(outputDir, `${nmId}.webp`);
        const publicPath = `${publicPrefix}/${nmId}.webp`;

        if (await fileExists(outputPath)) {
          manifest[nmId] = publicPath;
          skipped.push(nmId);
          return;
        }

        const result = await resolveAndDownload(nmId);

        if (!result) {
          missing.push(nmId);
          return;
        }

        await writeFile(outputPath, result.buffer);
        manifest[nmId] = publicPath;
        downloaded.push({ nmId, sourceUrl: result.sourceUrl });
      }),
    );

    void batchResults;
  }

  await writeFile(
    manifestPath,
    JSON.stringify(
      {
        generatedAt: new Date().toISOString(),
        total: ids.length,
        resolvedCount: Object.keys(manifest).length,
        missingIds: missing,
        images: manifest,
      },
      null,
      2,
    ),
  );

  return {
    label,
    total: ids.length,
    downloaded: downloaded.length,
    skipped: skipped.length,
    missing,
  };
}

async function main() {
  const pack = JSON.parse(await readFile(PACK_PATH, 'utf-8'));
  const competitorIds = [
    ...new Set(
      pack.products.flatMap((product) =>
        (product.competitors || [])
          .map((item) => normalizeNmId(item.nmId))
          .filter(Boolean),
      ),
    ),
  ];
  const productIds = [
    ...new Set(
      pack.products.map((product) => normalizeNmId(product.productId)).filter(Boolean),
    ),
  ];

  const competitorResult = await cacheImages({
    ids: competitorIds,
    outputDir: OUTPUT_DIR,
    publicPrefix: '/cabinet/wb-competitors',
    manifestPath: MANIFEST_PATH,
    label: 'competitors',
  });

  const productResult = await cacheImages({
    ids: productIds,
    outputDir: PRODUCT_OUTPUT_DIR,
    publicPrefix: '/cabinet/wb-products',
    manifestPath: PRODUCT_MANIFEST_PATH,
    label: 'products',
  });

  console.log(JSON.stringify({ competitorResult, productResult }, null, 2));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
