/** @type {import('next').NextConfig} */
const distDir = (process.env.CABINET_DIST_DIR || '').trim();

const nextConfig = {
  distDir: distDir || '.next',
  images: {
    formats: ['image/avif', 'image/webp']
  },
  async redirects() {
    return [
      {
        source: '/cabinet',
        destination: '/economics',
        permanent: false,
      },
      {
        source: '/cabinet/:path*',
        destination: '/:path*',
        permanent: false,
      },
    ];
  },
};

export default nextConfig;
