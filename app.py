"""
Férias Mil Grau — Sistema de Inscrição v4 (Python/Flask + PostgreSQL)
Compatível com Railway, Render, Heroku e qualquer VPS com PostgreSQL.
"""

import os, random, string, csv, io
from datetime import datetime
from functools import wraps
from contextlib import contextmanager

from flask import (Flask, render_template, request, jsonify,
                   redirect, url_for, session, send_from_directory,
                   send_file, abort, Response)
from werkzeug.utils import secure_filename

# ── PDF (opcional) ────────────────────────────────────────────────────────────
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    REPORTLAB_OK = True
except ImportError:
    REPORTLAB_OK = False

# ══════════════════════════════════════════════════════════════════════════════
#  BANCO DE DADOS — PostgreSQL via psycopg2
# ══════════════════════════════════════════════════════════════════════════════
import psycopg2
import psycopg2.extras
from psycopg2 import pool as pg_pool

# DATABASE_URL vem da variável de ambiente definida na plataforma de deploy.
# Formato: postgresql://usuario:senha@host:porta/banco
DATABASE_URL = os.environ.get('DATABASE_URL', '')

# Render/Railway/Heroku às vezes entregam "postgres://" — psycopg2 precisa de "postgresql://"
if DATABASE_URL.startswith('postgres://'):
    DATABASE_URL = DATABASE_URL.replace('postgres://', 'postgresql://', 1)

# Garantir sslmode=require para conexões na nuvem (Render exige SSL)
if DATABASE_URL and 'sslmode' not in DATABASE_URL:
    sep = '&' if '?' in DATABASE_URL else '?'
    DATABASE_URL = DATABASE_URL + sep + 'sslmode=require'

# Pool de conexões: mínimo 1, máximo 10 simultâneas
_pool = None

def get_pool():
    global _pool
    if _pool is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL nao definida. No Render: "
                "copie a Internal Database URL do banco e cole em Environment > DATABASE_URL"
            )
        safe_url = DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else '???'
        print(f"Conectando ao PostgreSQL: ...@{safe_url}")
        _pool = pg_pool.ThreadedConnectionPool(1, 10, DATABASE_URL)
        print("Pool de conexoes criado com sucesso.")
    return _pool

@contextmanager
def get_db():
    """Context manager que entrega uma conexão do pool e faz commit/rollback automático."""
    conn = get_pool().getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        get_pool().putconn(conn)

def query(sql, params=(), *, fetch='all', conn=None):
    """Helper: executa SQL e retorna lista de dicts (fetch='all') ou dict (fetch='one') ou None."""
    # Converte ? → %s (SQLite → PostgreSQL)
    pg_sql = sql.replace('?', '%s')

    def _run(c):
        with c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(pg_sql, params)
            if fetch == 'all':
                return [dict(r) for r in cur.fetchall()]
            elif fetch == 'one':
                row = cur.fetchone()
                return dict(row) if row else None
            elif fetch == 'scalar':
                row = cur.fetchone()
                return list(row.values())[0] if row else None
            else:
                return None   # INSERT/UPDATE/DELETE sem retorno

    if conn:
        return _run(conn)
    with get_db() as c:
        return _run(c)

def execute(sql, params=(), *, conn=None):
    """Helper: executa INSERT/UPDATE/DELETE (sem retorno de linhas)."""
    pg_sql = sql.replace('?', '%s')
    if conn:
        with conn.cursor() as cur:
            cur.execute(pg_sql, params)
        return
    with get_db() as c:
        with c.cursor() as cur:
            cur.execute(pg_sql, params)

def init_db():
    """Cria as tabelas se ainda não existirem."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS participantes (
                id          TEXT PRIMARY KEY,
                nome        TEXT NOT NULL,
                email       TEXT NOT NULL,
                telefone    TEXT NOT NULL,
                idade       TEXT DEFAULT '',
                cidade      TEXT DEFAULT '',
                quarto_id   TEXT DEFAULT '',
                quarto_nome TEXT DEFAULT '',
                dias        TEXT DEFAULT '',
                checkin     TEXT DEFAULT 'Não',
                status      TEXT DEFAULT 'Confirmado',
                data        TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS documentos (
                id                  SERIAL PRIMARY KEY,
                ticket_id           TEXT NOT NULL UNIQUE REFERENCES participantes(id) ON DELETE CASCADE,
                doc_participante    TEXT DEFAULT '',
                doc_responsavel     TEXT DEFAULT '',
                autorizacao         TEXT DEFAULT '',
                atestado            TEXT DEFAULT '',
                comunicacao         TEXT DEFAULT ''
            );

            -- Adiciona constraint UNIQUE caso a tabela já exista sem ela
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'documentos_ticket_id_key'
                ) THEN
                    ALTER TABLE documentos ADD CONSTRAINT documentos_ticket_id_key UNIQUE (ticket_id);
                END IF;
            END $$;
            """)
    print("✅  Banco PostgreSQL inicializado com sucesso.")

# ══════════════════════════════════════════════════════════════════════════════
#  QUARTOS
# ══════════════════════════════════════════════════════════════════════════════
QUARTOS_CAPACIDADE = 8

def build_quartos():
    quartos = []
    for n in range(1, 25):
        if n <= 4:    genero, grupo, cor = 'Meninas', 'Laranja', 'laranja'
        elif n <= 8:  genero, grupo, cor = 'Meninas', 'Roxo',    'roxo'
        elif n <= 12: genero, grupo, cor = 'Meninas', 'Verde',   'verde'
        elif n <= 16: genero, grupo, cor = 'Meninos', 'Laranja', 'laranja'
        elif n <= 20: genero, grupo, cor = 'Meninos', 'Roxo',    'roxo'
        else:         genero, grupo, cor = 'Meninos', 'Verde',   'verde'
        quartos.append(dict(
            id=f"Q{n:02d}", num=n, genero=genero,
            grupo=grupo, cor=cor, capacidade=QUARTOS_CAPACIDADE
        ))
    return quartos

def get_quartos_com_vagas():
    quartos = build_quartos()
    rows = query(
        "SELECT quarto_id, COUNT(*) as total FROM participantes "
        "WHERE status != 'Cancelado' AND quarto_id IS NOT NULL AND quarto_id != '' "
        "GROUP BY quarto_id",
        fetch='all'
    )
    ocupados = {r['quarto_id']: int(r['total']) for r in rows}
    for q in quartos:
        q['ocupados'] = ocupados.get(q['id'], 0)
        q['vagas']    = q['capacidade'] - q['ocupados']
    return quartos

# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════
ALLOWED_EXT = {'pdf', 'jpg', 'jpeg', 'png', 'doc', 'docx'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

def gen_id():
    """Gera ID único garantido pelo banco (tenta até encontrar um livre)."""
    chars = string.ascii_uppercase + string.digits
    for _ in range(20):
        tid = 'FMG-' + ''.join(random.choices(chars, k=6))
        exists = query("SELECT id FROM participantes WHERE id=?", (tid,), fetch='one')
        if not exists:
            return tid
    raise RuntimeError("Não foi possível gerar ID único.")

def format_br(dt: datetime):
    return dt.strftime('%d/%m/%Y %H:%M')

# ══════════════════════════════════════════════════════════════════════════════
#  FLASK APP
# ══════════════════════════════════════════════════════════════════════════════
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'fmg-secret-mude-em-producao-2026')

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads')
PDF_FOLDER    = os.path.join(os.path.dirname(__file__), 'static', 'pdf')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PDF_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER']        = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH']   = 10 * 1024 * 1024  # 10 MB

# ── Auth ──────────────────────────────────────────────────────────────────────
ADMIN_USER = os.environ.get('ADMIN_USER', 'admin')
ADMIN_PASS = os.environ.get('ADMIN_PASS', 'admin123')

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_logged'):
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated

# ══════════════════════════════════════════════════════════════════════════════
#  ROTAS — INSCRIÇÃO PÚBLICA
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('inscricao.html')

@app.route('/api/quartos')
def api_quartos():
    return jsonify(get_quartos_com_vagas())

@app.route('/api/inscricao', methods=['POST'])
def api_inscricao():
    data     = request.get_json(force=True)
    nome     = (data.get('nome') or '').strip()
    email    = (data.get('email') or '').strip()
    telefone = (data.get('telefone') or '').strip()
    if not nome or not email or not telefone:
        return jsonify(error='Campos obrigatórios ausentes'), 400

    quarto_id = data.get('quarto_id', '').strip()
    quartos   = get_quartos_com_vagas()
    q_info    = next((q for q in quartos if q['id'] == quarto_id), None)
    if not q_info:
        return jsonify(error='Quarto inválido'), 400
    if q_info['vagas'] <= 0:
        return jsonify(error='Quarto sem vagas disponíveis'), 409

    tid   = gen_id()
    qNome = f"Quarto {q_info['num']} — {q_info['genero']} {q_info['grupo']}"
    dias  = data.get('dias', 'Sexta 01/08, Sábado 02/08, Domingo 03/08')
    now   = format_br(datetime.now())

    with get_db() as conn:
        execute(
            "INSERT INTO participantes (id,nome,email,telefone,idade,cidade,"
            "quarto_id,quarto_nome,dias,checkin,status,data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, nome, email, telefone,
             data.get('idade',''), data.get('cidade',''),
             quarto_id, qNome, dias, 'Não', 'Confirmado', now),
            conn=conn
        )
        execute("INSERT INTO documentos (ticket_id) VALUES (?)", (tid,), conn=conn)

    return jsonify(id=tid, quarto_nome=qNome), 201

@app.route('/api/anexo/upload', methods=['POST'])
def api_upload():
    ticket_id = request.form.get('ticket_id', '').strip()
    tipo      = request.form.get('tipo', '').strip()
    if not ticket_id or not tipo:
        return jsonify(error='Parâmetros ausentes'), 400

    TIPOS_VALIDOS = ('doc_participante','doc_responsavel','autorizacao','atestado','comunicacao')
    if tipo not in TIPOS_VALIDOS:
        return jsonify(error='Tipo de documento inválido'), 400

    f = request.files.get('arquivo')
    if not f or not allowed_file(f.filename):
        return jsonify(error='Arquivo inválido ou formato não permitido'), 400

    ext      = f.filename.rsplit('.', 1)[1].lower()
    filename = secure_filename(f"{ticket_id}_{tipo}.{ext}")
    f.save(os.path.join(UPLOAD_FOLDER, filename))

    # Usa INSERT ... ON CONFLICT (upsert) do PostgreSQL
    execute(
        f"""
        INSERT INTO documentos (ticket_id, {tipo})
        VALUES (?, ?)
        ON CONFLICT (ticket_id) DO UPDATE SET {tipo} = EXCLUDED.{tipo}
        """,
        (ticket_id, filename)
    )
    return jsonify(ok=True, filename=filename)

@app.route('/api/pdf/<ticket_id>')
def api_pdf(ticket_id):
    if not REPORTLAB_OK:
        return jsonify(error='ReportLab não instalado. Execute: pip install reportlab'), 501

    p = query("SELECT * FROM participantes WHERE id=?", (ticket_id,), fetch='one')
    if not p:
        abort(404)

    pdf_path = os.path.join(PDF_FOLDER, f"{ticket_id}.pdf")
    _gerar_pdf(p, pdf_path)
    return send_file(pdf_path, as_attachment=True,
                     download_name=f"inscricao_{ticket_id}.pdf",
                     mimetype='application/pdf')

def _gerar_pdf(p, path):
    doc  = SimpleDocTemplate(path, pagesize=A4,
                             leftMargin=2*cm, rightMargin=2*cm,
                             topMargin=2*cm, bottomMargin=2*cm)
    title_style = ParagraphStyle('title', fontSize=28, fontName='Helvetica-Bold',
                                 alignment=TA_CENTER, textColor=colors.HexColor('#0066CC'))
    sub_style   = ParagraphStyle('sub', fontSize=12, fontName='Helvetica',
                                 alignment=TA_CENTER, textColor=colors.HexColor('#7AADCC'),
                                 spaceAfter=20)
    elems = [
        Paragraph("FÉRIAS MIL GRAU", title_style),
        Paragraph("Comprovante de Inscrição", sub_style),
        Spacer(1, 0.5*cm),
    ]
    data = [
        ['Campo', 'Informação'],
        ['Código', p['id']],
        ['Nome', p['nome']],
        ['E-mail', p['email']],
        ['Telefone', p['telefone']],
        ['Idade', p.get('idade') or '—'],
        ['Cidade', p.get('cidade') or '—'],
        ['Quarto', p.get('quarto_nome') or '—'],
        ['Dias', p.get('dias') or '—'],
        ['Status', p.get('status') or 'Confirmado'],
        ['Data de inscrição', p.get('data') or '—'],
    ]
    t = Table(data, colWidths=[5*cm, 11*cm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#0066CC')),
        ('TEXTCOLOR',  (0,0), (-1,0), colors.white),
        ('FONTNAME',   (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,0), (-1,0), 11),
        ('ROWBACKGROUNDS', (0,1), (-1,-1),
         [colors.HexColor('#0D1B2A'), colors.HexColor('#0A0F1E')]),
        ('TEXTCOLOR',  (0,1), (-1,-1), colors.HexColor('#F0F8FF')),
        ('FONTNAME',   (0,1), (0,-1), 'Helvetica-Bold'),
        ('FONTSIZE',   (0,1), (-1,-1), 10),
        ('GRID',       (0,0), (-1,-1), 0.5, colors.HexColor('#1A3A5C')),
        ('VALIGN',     (0,0), (-1,-1), 'MIDDLE'),
        ('ROWHEIGHT',  (0,0), (-1,-1), 0.8*cm),
    ]))
    elems.append(t)
    elems.append(Spacer(1, 1*cm))
    elems.append(Paragraph(
        "<i>Apresente este comprovante (ou o QR Code) no check-in do evento.</i>",
        ParagraphStyle('note', fontSize=9, textColor=colors.grey, alignment=TA_CENTER)
    ))
    doc.build(elems)

# ══════════════════════════════════════════════════════════════════════════════
#  ROTAS — ADMIN
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    error = None
    if request.method == 'POST':
        if (request.form.get('usuario') == ADMIN_USER and
                request.form.get('senha') == ADMIN_PASS):
            session['admin_logged'] = True
            return redirect(url_for('admin_dashboard'))
        error = 'Usuário ou senha incorretos.'
    return render_template('admin_login.html', error=error)

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    return render_template('admin.html')

# ── API Admin ─────────────────────────────────────────────────────────────────

@app.route('/api/admin/stats')
@login_required
def api_admin_stats():
    total   = query("SELECT COUNT(*) FROM participantes WHERE status!='Cancelado'", fetch='scalar')
    checkin = query("SELECT COUNT(*) FROM participantes WHERE checkin='Sim'", fetch='scalar')
    com_docs = query(
        "SELECT COUNT(DISTINCT p.id) FROM participantes p "
        "JOIN documentos d ON p.id = d.ticket_id "
        "WHERE p.status != 'Cancelado'",
        fetch='scalar'
    )
    quartos  = get_quartos_com_vagas()
    ocupados = sum(q['ocupados'] for q in quartos)
    lotados  = sum(1 for q in quartos if q['vagas'] <= 0)
    return jsonify(
        total=total, checkin=checkin, ocupados=ocupados,
        lotados=lotados, com_docs=com_docs,
        quartos=quartos
    )

@app.route('/api/admin/participantes')
@login_required
def api_admin_participantes():
    q      = request.args.get('q', '').lower()
    status = request.args.get('status', '')

    sql    = """
        SELECT p.*, d.doc_participante, d.doc_responsavel,
               d.autorizacao, d.atestado, d.comunicacao
        FROM participantes p
        LEFT JOIN documentos d ON p.id = d.ticket_id
        WHERE 1=1
    """
    params = []
    if q:
        sql += " AND (LOWER(p.nome) LIKE %s OR LOWER(p.email) LIKE %s OR LOWER(p.id) LIKE %s OR LOWER(p.quarto_nome) LIKE %s)"
        params += [f'%{q}%'] * 4
    if status == 'checkin':
        sql += " AND p.checkin='Sim'"
    elif status:
        sql += " AND p.status=%s"
        params.append(status)
    sql += " ORDER BY p.data DESC"

    with get_db() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]

    result = []
    for r in rows:
        r['docs'] = {
            'doc_participante': r.pop('doc_participante', '') or '',
            'doc_responsavel':  r.pop('doc_responsavel', '') or '',
            'autorizacao':      r.pop('autorizacao', '') or '',
            'atestado':         r.pop('atestado', '') or '',
            'comunicacao':      r.pop('comunicacao', '') or '',
        }
        result.append(r)
    return jsonify(result)

@app.route('/api/admin/participante', methods=['POST'])
@login_required
def api_admin_novo():
    data      = request.get_json(force=True)
    nome      = (data.get('nome') or '').strip()
    email     = (data.get('email') or '').strip()
    telefone  = (data.get('telefone') or '').strip()
    quarto_id = data.get('quarto_id', '').strip()
    if not nome or not email or not telefone or not quarto_id:
        return jsonify(error='Campos obrigatórios ausentes'), 400

    quartos = get_quartos_com_vagas()
    q_info  = next((q for q in quartos if q['id'] == quarto_id), None)
    if not q_info:
        return jsonify(error='Quarto inválido'), 400

    DIAS = {'sex': 'Sexta 01/08', 'sab': 'Sábado 02/08', 'dom': 'Domingo 03/08'}
    dias_str = ', '.join(DIAS[d] for d in data.get('dias', []) if d in DIAS) or '—'
    qNome    = f"Quarto {q_info['num']} — {q_info['genero']} {q_info['grupo']}"
    tid      = gen_id()
    now      = format_br(datetime.now())

    with get_db() as conn:
        execute(
            "INSERT INTO participantes (id,nome,email,telefone,idade,cidade,"
            "quarto_id,quarto_nome,dias,checkin,status,data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, nome, email, telefone,
             data.get('idade',''), data.get('cidade',''),
             quarto_id, qNome, dias_str, 'Não', 'Confirmado', now),
            conn=conn
        )
        execute("INSERT INTO documentos (ticket_id) VALUES (?)", (tid,), conn=conn)

    return jsonify(id=tid, quarto_nome=qNome), 201

@app.route('/api/admin/participante/<tid>', methods=['PUT'])
@login_required
def api_admin_editar(tid):
    data      = request.get_json(force=True)
    quarto_id = data.get('quarto_id', '').strip()
    quartos   = get_quartos_com_vagas()
    q_info    = next((q for q in quartos if q['id'] == quarto_id), None)
    qNome     = f"Quarto {q_info['num']} — {q_info['genero']} {q_info['grupo']}" if q_info else '—'
    DIAS      = {'sex': 'Sexta 01/08', 'sab': 'Sábado 02/08', 'dom': 'Domingo 03/08'}
    dias_str  = ', '.join(DIAS[d] for d in data.get('dias', []) if d in DIAS) or data.get('dias_str', '—')
    execute(
        "UPDATE participantes SET nome=?,email=?,telefone=?,idade=?,cidade=?,"
        "quarto_id=?,quarto_nome=?,dias=?,status=? WHERE id=?",
        (data.get('nome',''), data.get('email',''), data.get('telefone',''),
         data.get('idade',''), data.get('cidade',''),
         quarto_id, qNome, dias_str, data.get('status','Confirmado'), tid)
    )
    return jsonify(ok=True)

@app.route('/api/admin/participante/<tid>/cancelar', methods=['POST'])
@login_required
def api_admin_cancelar(tid):
    execute("UPDATE participantes SET status='Cancelado' WHERE id=?", (tid,))
    return jsonify(ok=True)

@app.route('/api/admin/participante/<tid>', methods=['DELETE'])
@login_required
def api_admin_excluir(tid):
    execute("DELETE FROM participantes WHERE id=?", (tid,))   # CASCADE apaga documentos
    return jsonify(ok=True)

@app.route('/api/admin/checkin', methods=['POST'])
@login_required
def api_admin_checkin():
    tid = (request.get_json(force=True).get('id') or '').strip().upper()
    p   = query("SELECT * FROM participantes WHERE id=?", (tid,), fetch='one')
    if not p:
        return jsonify(error='not_found'), 404
    if p['checkin'] == 'Sim':
        return jsonify(error='already_checked', nome=p['nome']), 409
    if p['status'] == 'Cancelado':
        return jsonify(error='cancelled'), 410
    execute("UPDATE participantes SET checkin='Sim' WHERE id=?", (tid,))
    return jsonify(ok=True, nome=p['nome'], quarto_nome=p['quarto_nome'])

@app.route('/api/admin/exportar-csv')
@login_required
def api_exportar_csv():
    rows = query(
        "SELECT p.*, d.doc_participante, d.doc_responsavel, d.autorizacao, d.atestado, d.comunicacao "
        "FROM participantes p LEFT JOIN documentos d ON p.id=d.ticket_id ORDER BY p.data DESC",
        fetch='all'
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID','Nome','Email','Telefone','Idade','Cidade',
                     'Quarto','Dias','Check-in','Status','Data',
                     'Doc.Participante','Doc.Responsável','Autorização','Atestado','Comunicação'])
    for r in rows:
        writer.writerow([
            r['id'], r['nome'], r['email'], r['telefone'],
            r.get('idade',''), r.get('cidade',''),
            r.get('quarto_nome',''), r.get('dias',''),
            r.get('checkin',''), r.get('status',''), r.get('data',''),
            r.get('doc_participante',''), r.get('doc_responsavel',''),
            r.get('atestado',''), r.get('autorizacao',''), r.get('comunicacao',''),
        ])
    output.seek(0)
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': 'attachment; filename=inscritos_fmg.csv'}
    )

@app.route('/admin/download/<filename>')
@login_required
def serve_upload(filename):
    """Serve arquivos de upload apenas para admins logados."""
    safe_name = os.path.basename(filename)  # evita path traversal
    filepath = os.path.join(UPLOAD_FOLDER, safe_name)
    if not os.path.exists(filepath):
        abort(404)
    return send_from_directory(UPLOAD_FOLDER, safe_name, as_attachment=False)

# ── Health check (Railway/Render usam isso) ───────────────────────────────────
@app.route('/health')
def health():
    try:
        query("SELECT 1", fetch='scalar')
        return jsonify(status='ok', db='postgresql'), 200
    except Exception as e:
        return jsonify(status='error', detail=str(e)), 503

# ══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════
# Chamado tanto pelo gunicorn quanto pelo python direto
with app.app_context():
    try:
        init_db()
    except Exception as e:
        print(f"AVISO init_db: {e}")

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_ENV', 'production') == 'development'
    print(f"✅  Férias Mil Grau — PostgreSQL — http://localhost:{port}")
    print(f"🔐  Admin: http://localhost:{port}/admin  |  {ADMIN_USER} / {ADMIN_PASS}")
    app.run(host='0.0.0.0', port=port, debug=debug)

  
