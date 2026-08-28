# Deploy no Railway

## Passos

1. **Sobe o código para um repositório no GitHub** (pode ser privado — o
   Railway suporta repos privados sem problema).

2. No [Railway](https://railway.app):
   - **New Project** → **Deploy from GitHub repo** → escolhe o repositório
     do CS2 Pulse
   - O Railway deteta o `Dockerfile` automaticamente e usa-o para o build
     (não precisas de configurar build/start command à mão)

3. **Variáveis de ambiente** (separador Variables do serviço):
   ```
   CS2_PULSE_PASSWORD = <uma password tua, diferente de "pulse2026">
   DATA_DIR = /data
   ```

4. **Monta um Volume** — CRÍTICO, sem isto perdes o histórico e a watchlist
   a cada deploy (exatamente o mesmo problema que tinha o Render, e que foi
   a razão para não usarmos o Render):
   - Vai a **Settings** do serviço → **Volumes** → **New Volume**
   - Mount path: `/data` (tem de bater certo com o `DATA_DIR` acima)
   - Qualquer tamanho pequeno chega (a base de dados é só texto/números,
     não guarda imagens nem ficheiros grandes)

5. **Deploy**. O Railway atribui um domínio público
   (`algo.up.railway.app`) automaticamente — não precisas de configurar
   nada de rede.

6. No iPhone: abre esse URL no Safari → ícone de partilha → **"Adicionar
   ao ecrã principal"**.

## Nota sobre os tokens/chaves já embutidos no código

O `fun88_odds.py` e o `app.py` (chave da Polygonscan) já têm os valores
reais escritos diretamente no código, como pediste para facilitar. Como o
repositório fica privado e o Railway não expõe o código publicamente, isto
mantém-se ao mesmo nível de privacidade que já tinhas ao correr localmente
— só quem tiver acesso ao teu repositório GitHub ou à tua conta Railway é
que os vê.

## Nota

Não testei este deploy ao vivo (sem acesso à internet no ambiente onde
construí isto). Testei isoladamente: (1) a expansão da variável $PORT no
comando de arranque do Streamlit, com e sem a variável definida; (2) a
lógica do DATA_DIR no storage.py, com e sem a variável definida, incluindo
a criação automática da pasta. Ambos confirmados corretos. O que não
consigo garantir é o comportamento ao vivo do build/deploy em si — se
algo não bater certo, cola-me o erro exato dos logs do Railway.
