/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  images: {
    remotePatterns: [
      {
        protocol: 'http',
        hostname: 'localhost',
        port: '8080',
      },
      {
        protocol: 'http',
        hostname: 'host.docker.internal',
        port: '8000',
      },
    ],
  },
  transpilePackages: [
    'react-markdown',
    'remark-gfm',
    'vfile',
    'vfile-message',
    'unist-util-is',
    'unist-util-visit-parents',
    'unist-util-visit',
    'mdast-util-to-hast',
    'mdast-util-from-markdown',
    'mdast-util-to-string',
    'micromark',
    'hast-util-to-jsx-runtime',
    'decode-named-character-reference'
  ],
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL || 'http://localhost:8081';
    return [
      {
        source: '/api/:path*',
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
