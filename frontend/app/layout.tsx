import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '员工月奖自动核算系统',
  description: '员工月奖申报、识别校对、规则试算、审核与 Excel 导出工作台',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
