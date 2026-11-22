Baixe o arquivo do seu sistema, descompacte e abra. O hub sobe no seu computador e abre no navegador; **os seus arquivos não saem da sua máquina**. Para encerrar, feche a janela do terminal que abriu junto.

| Sistema | Arquivo | Na primeira vez |
|---|---|---|
| Windows 10 ou 11 | `datahub-windows-x64.zip` | O Windows avisa que o app não é reconhecido: clique em **Mais informações → Executar assim mesmo**. |
| macOS (Apple Silicon) | `datahub-macos-arm64.zip` | Clique com o botão direito no `datahub` → **Abrir** → **Abrir**. Se não aparecer, vá em Ajustes do Sistema → Privacidade e Segurança → **Abrir Mesmo Assim**. |
| Linux | `datahub-linux-x64.zip` | No terminal: `chmod +x datahub && ./datahub`. |

O aviso aparece porque o executável não tem assinatura paga de desenvolvedor. O código que o gera está neste repositório e o executável é montado pelo GitHub Actions, à vista no histórico de execuções.
