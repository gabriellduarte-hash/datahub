import type { ReactNode } from "react";

type Props = {
  titulo: string;
  extra?: ReactNode;
  children: ReactNode;
  semSombra?: boolean;
  semFolga?: boolean;
  nivel?: 2 | 3;
};

export function Janela({ titulo, extra, children, semSombra, semFolga, nivel = 2 }: Props) {
  const Titulo = `h${nivel}` as "h2" | "h3";
  return (
    <section className={`janela${semSombra ? " sem-sombra" : ""}`}>
      <div className="barra">
        <Titulo>{titulo}</Titulo>
        {extra ? <span className="barra-extra">{extra}</span> : null}
      </div>
      <div className={`corpo${semFolga ? " sem-folga" : ""}`}>{children}</div>
    </section>
  );
}

export function Triangulo() {
  return (
    <svg viewBox="0 0 32 28" aria-hidden="true">
      <path d="M16 2 L30 26 H2 Z" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <rect x="14.5" y="9" width="3" height="10" fill="currentColor" />
      <rect x="14.5" y="21" width="3" height="3" fill="currentColor" />
    </svg>
  );
}
