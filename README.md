# Antifake 3.0 — consulta pública e gratuita de checagem de fatos

Cole um **texto**, um **link**, uma **foto** ou um **vídeo**. O Antifake lê o conteúdo, pesquisa em agências de checagem e na imprensa, e devolve um veredito (**FALSO · ENGANOSO · VERDADEIRO · SEM EVIDÊNCIA**) com os motivos e as fontes.

**Custo: R$ 0.** Não precisa de nenhuma chave de API paga.

## Como rodar

### Opção 1: Docker (recomendado, já inclui OCR e vídeo)
```bash
cp backend/.env.example backend/.env
docker compose up -d --build
# abra http://localhost:8000
```

### Opção 2: sem Docker
- **Linux/macOS:** `./iniciar.sh`
- **Windows:** clique duas vezes em `iniciar.bat`

Pré-requisitos: Python 3.10+, [Tesseract](https://github.com/tesseract-ocr/tesseract) (com o idioma português) e [ffmpeg](https://ffmpeg.org).

### Opção 3: publicar na internet grátis (Render)
1. Crie um repositório no GitHub e envie o conteúdo desta pasta (o `Dockerfile` e o `render.yaml` precisam ficar na raiz).
2. Entre em https://dashboard.render.com → **New → Blueprint** → escolha o repositório → **Apply**.
3. Em ~5–8 min o Render mostra o link fixo, no formato `https://antifake-xxxx.onrender.com`.

Ou, com o repositório já no GitHub, use o botão (troque `SEU-USUARIO/SEU-REPO`):
`https://render.com/deploy?repo=https://github.com/SEU-USUARIO/SEU-REPO`

- **Hugging Face Spaces:** crie um Space do tipo *Docker* e envie os arquivos (porta 8000 → defina `app_port: 8000` no README do Space).
- **Oracle Cloud Always Free / qualquer VPS:** `docker compose up -d`.

> No plano gratuito do Render o servidor dorme após 15 min sem acesso (a próxima visita leva ~1 min para acordar) e o histórico é apagado quando ele reinicia, porque o disco não é persistente.

## Banco de dados (para guardar as consultas)
- **Com `DATABASE_URL`** (recomendado): usa PostgreSQL. O [Neon](https://neon.tech) tem plano grátis que não expira (0,5 GB, cabe dezenas de milhares de verificações). Tudo fica no banco: verificações, fotos reduzidas, quadros dos vídeos e avaliações.
- **Sem `DATABASE_URL`**: usa SQLite local (bom para testar no computador; no Render grátis é apagado a cada reinício).

## Recursos de consulta pública
- **Acervo pesquisável** (`/api/search?q=pix&veredito=FALSO&tipo=video`) com filtros e paginação.
- **Página pública por verificação** (`/v/{id}`) com imagem de prévia para WhatsApp/redes (`/og/{id}.png`) e marcação ClaimReview.
- **Avaliação "Útil / Resultado errado"** com comentário; moderação em `/api/admin/feedback?token=ADMIN_TOKEN` e `POST /api/admin/hide/{id}?token=...`.
- **Privacidade**: caixa "mostrar na consulta pública" (desmarque para conteúdos com dados pessoais); IP não é guardado (só um hash para evitar voto duplicado).
- **Anti-abuso**: limite de verificações por aparelho (`RATE_CHECKS`, padrão 15 a cada 10 min).
- **Servidor acordado**: com `PUBLIC_URL` definido, o próprio servidor se "cutuca" a cada 10 min para não dormir no plano grátis.

## O que acontece em cada verificação

| Etapa | Ferramenta (gratuita) |
|---|---|
| Texto de um link | trafilatura + análise do domínio (data de registro via RDAP/Registro.br, veículo reconhecido, site de sátira) |
| Texto de uma foto | Tesseract OCR (português + inglês) + metadados EXIF |
| Fala de um vídeo | ffmpeg + faster-whisper (roda no próprio servidor) + OCR de 4 quadros |
| Busca de checagens | Google Notícias (RSS) filtrado em Lupa, Aos Fatos, Fato ou Fake, Estadão Verifica, AFP Checamos, Comprova, Boatos.org, E-farsas, UOL Confere |
| Busca geral | DuckDuckGo + Bing (RSS) como reserva |
| Veredito | Motor de regras transparente: classificação das checagens × relevância, cobertura em veículos reconhecidos, linguagem alarmista, idade do domínio |
| Busca reversa de imagem | Links prontos para Google Lens, Yandex, TinEye e Bing |

## Melhorias opcionais (também gratuitas)
Em `backend/.env`:
- `GOOGLE_FACTCHECK_KEY`: banco oficial ClaimReview do Google ([ativar a API](https://console.cloud.google.com/apis/library/factchecktools.googleapis.com)).
- `GEMINI_API_KEY` (camada gratuita do [AI Studio](https://aistudio.google.com/apikey)), **ou** `GROQ_API_KEY` ([Groq](https://console.groq.com/keys)), **ou** `OLLAMA_URL` (LLM local): a IA escreve a explicação usando **somente** as evidências encontradas e cita cada uma por número.

## API
| Método | Rota | Descrição |
|---|---|---|
| POST | `/api/check` | form-data: `text` e/ou `file` → `{job}` |
| GET | `/api/check/{job}` | progresso ao vivo + resultado |
| GET | `/api/analysis/{id}` | análise salva |
| GET | `/api/history` | últimas verificações |
| GET | `/api/stats` | contagem por veredito |
| GET | `/api/health` | status e recursos ativos |
| GET | `/v/{id}` | página pública para compartilhar (com Open Graph para WhatsApp) |

Exemplo:
```bash
curl -F "text=Vacina contra covid tem chip 5G" http://localhost:8000/api/check
curl -F "file=@print.jpg" http://localhost:8000/api/check
```

## Limites honestos
- O veredito é automático: ele depende de alguém (agência ou imprensa) já ter publicado sobre o assunto. Boatos muito novos tendem a sair como **SEM EVIDÊNCIA**.
- Os buscadores gratuitos podem limitar muitas consultas seguidas vindas do mesmo servidor. Por isso as análises repetidas ficam em cache por 12 h.
- Sites que bloqueiam robôs (paywall, Cloudflare) podem não abrir. Nesses casos, cole o texto da notícia.
- Vídeos são analisados até `VIDEO_MAX_SECONDS` (padrão 180 s). O arquivo original é apagado depois da análise; só os quadros ficam salvos.
