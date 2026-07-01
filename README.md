# Férias Mil Grau — Sistema de Inscrição (Python/Flask + PostgreSQL)

Sistema completo de inscrições com backend PostgreSQL persistente, pronto para deploy em Railway, Render ou Heroku.

---

## ⚡ Deploy rápido

### Railway (recomendado — plano gratuito generoso)
1. Crie conta em [railway.app](https://railway.app)
2. Novo projeto → **Deploy from GitHub** (suba o código em um repositório)
3. Adicione um serviço **PostgreSQL** ao projeto
4. Na aba **Variables** do serviço web, adicione:
   ```
   DATABASE_URL  → (Railway preenche automaticamente ao linkar o Postgres)
   SECRET_KEY    → gere com: python -c "import secrets; print(secrets.token_hex(32))"
   ADMIN_USER    → admin
   ADMIN_PASS    → sua_senha_segura
   ```
5. Railway detecta o `railway.toml` e faz o deploy automático.

### Render
1. Crie conta em [render.com](https://render.com)
2. Novo → **Blueprint** → aponte para o repositório
3. O `render.yaml` cria o serviço web + banco PostgreSQL automaticamente.
4. Defina `ADMIN_PASS` no painel do Render.

### Heroku
```bash
heroku create nome-do-app
heroku addons:create heroku-postgresql:essential-0
heroku config:set SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
heroku config:set ADMIN_USER=admin ADMIN_PASS=sua_senha
git push heroku main
```

---

## 🖥️ Rodar localmente

### 1. Instalar dependências
```bash
pip install -r requirements.txt
```

### 2. Configurar o banco PostgreSQL local
```bash
# Criar banco (com PostgreSQL instalado)
createdb ferias_db

# Ou via psql
psql -c "CREATE DATABASE ferias_db;"
```

### 3. Definir variáveis de ambiente
```bash
cp .env.example .env
# Edite .env com sua DATABASE_URL, SECRET_KEY e ADMIN_PASS
```

Carregue o `.env` antes de rodar (ou use `python-dotenv`):
```bash
export $(cat .env | grep -v '#' | xargs)
python app.py
```

### 4. Acessar
- **Inscrições:** http://localhost:5000
- **Admin:**     http://localhost:5000/admin  (`admin` / senha do `.env`)

---

## 🗄️ Variáveis de ambiente

| Variável | Obrigatória | Descrição |
|---|---|---|
| `DATABASE_URL` | ✅ Sim | URL de conexão PostgreSQL |
| `SECRET_KEY` | ✅ Sim | Chave para sessões Flask |
| `ADMIN_USER` | Não | Usuário admin (padrão: `admin`) |
| `ADMIN_PASS` | Não | Senha admin (padrão: `admin123`) |
| `PORT` | Não | Porta HTTP (padrão: `5000`) |
| `FLASK_ENV` | Não | `development` ativa o debug |

**Formato DATABASE_URL:**
```
postgresql://usuario:senha@host:porta/nome_banco
```

---

## 📁 Estrutura do projeto

```
ferias-mil-grau-python/
├── app.py               ← Servidor Flask (PostgreSQL)
├── requirements.txt     ← Dependências Python
├── Procfile             ← Comando de start (Railway/Heroku)
├── railway.toml         ← Configuração Railway
├── render.yaml          ← Configuração Render
├── .env.example         ← Modelo de variáveis de ambiente
├── .gitignore
├── static/
│   ├── uploads/         ← Documentos enviados pelos participantes
│   └── pdf/             ← Comprovantes PDF gerados
└── templates/
    ├── base.html
    ├── inscricao.html   ← Inscrição pública (5 passos)
    ├── admin_login.html
    └── admin.html       ← Painel administrativo
```

---

## 🔒 Sobre persistência dos dados

Diferente do SQLite (arquivo local que se perde em containers efêmeros), o **PostgreSQL** fica em um serviço separado com dados persistentes independentemente de reinicios, redeploys ou crashes do servidor web.

Os arquivos de documentos (uploads) ficam em `static/uploads/`. Em produção na nuvem, considere usar um serviço de armazenamento como **AWS S3**, **Cloudflare R2** ou **Supabase Storage** para que os arquivos também persistam — containers em Railway/Render reiniciam o sistema de arquivos a cada deploy.

---

## 🚀 API Endpoints

### Público
| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/quartos` | Lista quartos com vagas |
| POST | `/api/inscricao` | Cria inscrição |
| POST | `/api/anexo/upload` | Upload de documento |
| GET | `/api/pdf/<id>` | Baixa comprovante PDF |
| GET | `/health` | Health check (banco + app) |

### Admin (requer login)
| Método | Rota | Descrição |
|---|---|---|
| GET | `/api/admin/stats` | Estatísticas do dashboard |
| GET | `/api/admin/participantes` | Lista com filtros |
| POST | `/api/admin/participante` | Adicionar inscrito |
| PUT | `/api/admin/participante/<id>` | Editar inscrito |
| POST | `/api/admin/participante/<id>/cancelar` | Cancelar inscrição |
| DELETE | `/api/admin/participante/<id>` | Excluir inscrito |
| POST | `/api/admin/checkin` | Registrar check-in |
| GET | `/api/admin/exportar-csv` | Exportar CSV completo |
