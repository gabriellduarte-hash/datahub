"use client";

import { useState, type DragEvent } from "react";
import { Janela } from "@/components/Janela";
import { TabelaDados } from "@/components/TabelaDados";
import { fmtBytes, fmtInteiro } from "@/lib/formato";
import type { Estado } from "@/lib/tipos";

type Props = {
  estado: Estado | null;
  ocupado: boolean;
  aoEnviar: (arquivo: File) => void;
  aoExemplo: () => void;
  aoTrocarAba: (aba: string) => void;
};

export function BlocoArquivo({ estado, ocupado, aoEnviar, aoExemplo, aoTrocarAba }: Props) {
  const [arrastando, setArrastando] = useState(false);

  const soltar = (evento: DragEvent) => {
    evento.preventDefault();
    setArrastando(false);
    const arquivo = evento.dataTransfer.files[0];
    if (arquivo) aoEnviar(arquivo);
  };

  if (!estado) {
    return (
      <Janela titulo="Novo arquivo">
        <label
          className={`soltar${arrastando ? " ativo" : ""}`}
          onDragOver={(evento) => { evento.preventDefault(); setArrastando(true); }}
          onDragLeave={() => setArrastando(false)}
          onDrop={soltar}
        >
          <strong>Solte um arquivo aqui</strong>
          <span>ou clique para escolher</span>
          <span className="texto-suave">CSV, TSV, TXT, XLSX ou Parquet</span>
          <input
            type="file"
            id="arquivo"
            accept=".csv,.tsv,.txt,.xlsx,.parquet"
            disabled={ocupado}
            onChange={(evento) => { const arquivo = evento.target.files?.[0]; if (arquivo) aoEnviar(arquivo); }}
          />
        </label>
        <div className="abertura">
          <button type="button" className="botao principal" disabled={ocupado} onClick={aoExemplo}>
            Usar vendas de exemplo
          </button>
          <span className="texto-suave">5 mil pedidos com sujeira de verdade, para ver os cinco blocos funcionando.</span>
        </div>
        <p className="texto-suave">O arquivo original nunca é alterado e a sessão é apagada depois de 24 horas.</p>
      </Janela>
    );
  }

  const { arquivo } = estado;
  return (
    <>
      <Janela titulo={arquivo.arquivo}>
        <dl className="numeros">
          <div className="numero"><dt>Linhas</dt><dd>{fmtInteiro(arquivo.linhas)}</dd></div>
          <div className="numero"><dt>Colunas</dt><dd>{fmtInteiro(arquivo.colunas)}</dd></div>
          <div className="numero"><dt>Tamanho</dt><dd>{fmtBytes(arquivo.bytes)}</dd></div>
          <div className="numero"><dt>Formato</dt><dd>{arquivo.formato.toUpperCase()}</dd></div>
          {arquivo.separador ? <div className="numero"><dt>Separador</dt><dd>{arquivo.separador === "\t" ? "tab" : arquivo.separador}</dd></div> : null}
          {arquivo.codificacao ? <div className="numero"><dt>Codificação</dt><dd>{arquivo.codificacao}</dd></div> : null}
        </dl>
        {arquivo.abas.length > 1 ? (
          <label className="rotulo" htmlFor="aba">
            Aba da planilha
            <select id="aba" className="campo" value={arquivo.aba ?? ""} disabled={ocupado} onChange={(evento) => aoTrocarAba(evento.target.value)}>
              {arquivo.abas.map((aba) => <option key={aba} value={aba}>{aba}</option>)}
            </select>
          </label>
        ) : null}
        {arquivo.avisos.length ? (
          <ul className="avisos">{arquivo.avisos.map((aviso) => <li key={aviso}>{aviso}</li>)}</ul>
        ) : null}
      </Janela>
      <Janela titulo="Como o arquivo chegou" semFolga>
        <TabelaDados sessao={estado.sessao} tabela="bruta" versao={arquivo.aba ?? ""} />
      </Janela>
    </>
  );
}
