"use client";

import { useState, type ReactNode } from "react";
import * as api from "@/lib/api";
import { NO_NAVEGADOR } from "@/lib/motor";

type Props = { caminho: string; className?: string; titulo?: string; children: ReactNode };

/* Com servidor, um link comum. No site estático, um botão: o arquivo sai do
   motor do navegador e vira um download local. */
export function Baixar({ caminho, className, titulo, children }: Props) {
  const [erro, setErro] = useState<string | null>(null);
  if (!NO_NAVEGADOR) {
    return <a className={className} href={api.enderecoDownload(caminho)} aria-label={titulo}>{children}</a>;
  }
  const baixar = () => {
    setErro(null);
    api.baixarDoMotor(caminho).catch((falha: Error) => setErro(falha.message));
  };
  return (
    <button type="button" className={className} onClick={baixar} aria-label={titulo} title={erro ?? undefined}>
      {erro ? "! " : null}{children}
    </button>
  );
}
