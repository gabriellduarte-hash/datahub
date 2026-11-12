import type { Metadata } from "next";
import { JetBrains_Mono, VT323 } from "next/font/google";
import "./globals.css";

const mono = JetBrains_Mono({ variable: "--fonte-mono", subsets: ["latin"] });
const pixel = VT323({ variable: "--fonte-pixel", subsets: ["latin"], weight: "400" });

export const metadata: Metadata = {
  title: "datahub",
  description: "Normalização, gráficos e insights de planilhas, sem IA.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="pt-BR" className={`${mono.variable} ${pixel.variable}`}>
      <body>{children}</body>
    </html>
  );
}
