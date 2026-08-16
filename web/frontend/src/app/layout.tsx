import type { Metadata } from 'next';
import './globals.css';
import ClientLayout from './client-layout';

export const metadata: Metadata = {
  title: 'Digital Twin - Giám sát Công trình Giao thông',
  description:
    'Hệ thống bản sao số phục vụ giám sát và đánh giá tự động sức khỏe công trình giao thông. Tích hợp AI phát hiện vết nứt và trợ lý kỹ thuật thông minh.',
  keywords: ['digital twin', 'giám sát công trình', 'phát hiện vết nứt', 'AI', 'drone'],
  authors: [{ name: 'Digital Twin Team' }],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="vi" className="light">
      <head>
        <link rel="icon" href="/favicon.ico" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="antialiased">
        <ClientLayout>{children}</ClientLayout>
      </body>
    </html>
  );
}
