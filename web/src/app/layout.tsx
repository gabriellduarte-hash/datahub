import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const sans = Geist({ variable: "--fonte-sans", subsets: ["latin"] });
const mono = Geist_Mono({ variable: "--fonte-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "datahub",
  description: "Normalização, gráficos e insights de planilhas, sem IA.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="pt-BR" className={`${sans.variable} ${mono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
