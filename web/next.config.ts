import type { NextConfig } from "next";

/* DATAHUB_EXPORTAR=1 (na imagem de produção) faz o build sair como arquivos
   estáticos em out/, que a própria API serve. A exportação não aceita
   pré-renderização parcial, então os dois recursos de cache ficam só no
   desenvolvimento; a página é toda do lado do cliente e não perde nada. */
const exportar = Boolean(process.env.DATAHUB_EXPORTAR);

const nextConfig: NextConfig = exportar
  ? { output: "export", devIndicators: false }
  : { cacheComponents: true, partialPrefetching: true, devIndicators: false };

export default nextConfig;
