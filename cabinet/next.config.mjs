/** @type {import('next').NextConfig} */
const distDir = (process.env.CABINET_DIST_DIR || '').trim();

const nextConfig = {
  basePath: '/cabinet',
  distDir: distDir || '.next',
  images: {
    formats: ['image/avif', 'image/webp']
  }
};

export default nextConfig;
