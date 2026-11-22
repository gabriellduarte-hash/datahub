"use client";

import { useEffect, useState, type DragEvent } from "react";
import { Janela } from "@/components/Janela";
import { TabelaDados } from "@/components/TabelaDados";
import * as api from "@/lib/api";
import { fmtBytes, fmtInteiro } from "@/lib/formato";
import type { Saude, Tabela } from "@/lib/tipos";

/* No site público o arquivo sai da máquina de quem envia: a tela diz o limite e por quanto tempo ele fica. */
function Aviso({ saude }: { saude: Saude | null }) {
  if (saude?.modo !== "publico") {
    return <p className="texto-suave">O arquivo original nunca é alterado e a sessão é apagada depois de {saude?.horas_de_vida ?? 24} horas.</p>;
  }
  const maximo = saude.limites.max_bytes_arquivo;
  return (
    <p className="texto-suave">
      {maximo ? `Arquivos de até ${Math.round(maximo / 1024 ** 2)} MB. ` : ""}
      O arquivo fica no servidor só enquanto a sessão existe e é apagado em {saude.horas_de_vida} horas.
      Não envie dados pessoais ou sigilosos.
    </p>
  );
}

type Props = {
  sessao: string | null;
  tabela: Tabela | null;
  ocupado: boolean;
  aoEnviar: (arquivo: File) => void;
  aoExemplo: () => void;
  aoTrocarAba: (aba: string) => void;
  aoCarregarAbas: () => void;
};

export function BlocoArquivo({ sessao, tabela, ocupado, aoEnviar, aoExemplo, aoTrocarAba, aoCarregarAbas }: Props) {
  const [arrastando, setArrastando] = useState(false);
  const [saude, setSaude] = useState<Saude | null>(null);
  const vazio = !sessao || !tabela;

  useEffect(() => {
    if (vazio) api.saude().then(setSaude).catch(() => setSaude(null));
  }, [vazio]);

  const soltar = (evento: DragEvent) => {
    evento.preventDefault();
    setArrastando(false);
    const arquivo = evento.dataTransfer.files[0];
    if (arquivo) aoEnviar(arquivo);
  };

  if (vazio) {
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
          <span className="texto-suave">5 mil pedidos com sujeira de verdade, para ver o hub inteiro funcionando.</span>
        </div>
        <Aviso saude={saude} />
      </Janela>
    );
  }

  const { arquivo } = tabela;
  return (
    <>
      <Janela titulo={tabela.titulo} extra={<span className="marca">{tabela.nome}</span>}>
        <dl className="numeros">
          <div className="numero"><dt>Linhas</dt><dd>{fmtInteiro(arquivo.linhas)}</dd></div>
          <div className="numero"><dt>Colunas</dt><dd>{fmtInteiro(arquivo.colunas)}</dd></div>
          <div className="numero"><dt>Tamanho</dt><dd>{fmtBytes(arquivo.bytes)}</dd></div>
          <div className="numero"><dt>Formato</dt><dd>{arquivo.formato.toUpperCase()}</dd></div>
          {arquivo.separador ? <div className="numero"><dt>Separador</dt><dd>{arquivo.separador === "\t" ? "tab" : arquivo.separador}</dd></div> : null}
          {arquivo.codificacao ? <div className="numero"><dt>Codificação</dt><dd>{arquivo.codificacao}</dd></div> : null}
        </dl>
        {arquivo.abas.length > 1 ? (
          <div className="acoes">
            <label className="rotulo" htmlFor="aba">
              Aba da planilha
              <select id="aba" className="campo" value={arquivo.aba ?? ""} disabled={ocupado} onChange={(evento) => aoTrocarAba(evento.target.value)}>
                {arquivo.abas.map((aba) => <option key={aba} value={aba}>{aba}</option>)}
              </select>
            </label>
            <button type="button" className="botao" disabled={ocupado} onClick={aoCarregarAbas}>
              Carregar as {arquivo.abas.length} abas como tabelas
            </button>
          </div>
        ) : null}
        {arquivo.avisos.length ? (
          <ul className="avisos">{arquivo.avisos.map((aviso) => <li key={aviso}>{aviso}</li>)}</ul>
        ) : null}
      </Janela>
      <Janela titulo="Como o arquivo chegou" semFolga>
        <TabelaDados sessao={sessao} tabela={tabela.id} bruta versao={tabela.versao} />
      </Janela>
    </>
  );
}
