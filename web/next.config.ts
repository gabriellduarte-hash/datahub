import type { NextConfig } from "next";

/* DATAHUB_EXPORTAR=1 faz o build sair como arquivos estáticos em out/: servidos
   pela própria API (Dockerfile, executável) ou, com NEXT_PUBLIC_MOTOR=navegador,
   publicados no GitHub Pages com o motor rodando no navegador. A exportação não
   aceita pré-renderização parcial, então os dois recursos de cache ficam só no
   desenvolvimento; a página é toda do lado do cliente e não perde nada.
   NEXT_PUBLIC_BASE_PATH é a subpasta do site (no GitHub Pages, /datahub). */
const exportar = Boolean(process.env.DATAHUB_EXPORTAR);
const basePath = process.env.NEXT_PUBLIC_BASE_PATH || undefined;

const nextConfig: NextConfig = exportar
  ? { output: "export", basePath, devIndicators: false }
  : { cacheComponents: true, partialPrefetching: true, devIndicators: false };

export default nextConfig;
