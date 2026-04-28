from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Mail, Message
import random
import base64
import time

import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'chave_secreta_familia')

# CONFIGURAÇÃO DE E-MAIL
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = os.getenv('MAIL_DEFAULT_SENDER', app.config['MAIL_USERNAME'])

mail = Mail(app)

def enviar_email(assunto, destinatario, html_corpo):
    if not app.config['MAIL_USERNAME']:
        print(f"DEBUG: E-mail não enviado (Sem credenciais): {assunto} para {destinatario}")
        return
    try:
        msg = Message(assunto, recipients=[destinatario])
        msg.html = html_corpo
        mail.send(msg)
    except Exception as e:
        print(f"Erro ao enviar e-mail: {e}")

# Ordem de prioridade para a URL do Banco de Dados
database_url = os.getenv('DATABASE_URL') or os.getenv('MYSQL_URL')

if not database_url:
    DB_USER = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', '1234')
    DB_NAME = os.getenv('DB_NAME', 'amigo_oculto')
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    database_url = f'mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}'
else:
    # Garante que a URL use pymysql se for um MySQL
    if database_url.startswith('mysql://'):
        database_url = database_url.replace('mysql://', 'mysql+pymysql://', 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {"pool_pre_ping": True}

db = SQLAlchemy(app)

from sqlalchemy.dialects.mysql import LONGTEXT

# --- MODELS ---

class Usuario(db.Model):
    __tablename__ = 'usuarios'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    senha = db.Column(db.String(255), nullable=False)
    foto = db.Column(LONGTEXT, nullable=True) # Foto em Base64
    is_admin = db.Column(db.Boolean, default=False)
    lista_desejos = db.Column(db.Text, nullable=True)

class Evento(db.Model):
    __tablename__ = 'eventos'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    sorteado = db.Column(db.Boolean, default=False)
    valor_min = db.Column(db.Numeric(10, 2), default=0.00)
    valor_max = db.Column(db.Numeric(10, 2), default=0.00)
    participantes = db.relationship('EventoParticipante', backref='evento', cascade="all, delete-orphan")

class EventoParticipante(db.Model):
    __tablename__ = 'evento_participantes'
    id = db.Column(db.Integer, primary_key=True)
    evento_id = db.Column(db.Integer, db.ForeignKey('eventos.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    amigo_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=True)
    
    usuario = db.relationship('Usuario', foreign_keys=[usuario_id])
    amigo = db.relationship('Usuario', foreign_keys=[amigo_id])

class Restricao(db.Model):
    __tablename__ = 'restricoes'
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    excluido_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)

    usuario = db.relationship('Usuario', foreign_keys=[usuario_id])
    excluido = db.relationship('Usuario', foreign_keys=[excluido_id])

class MensagemSecreta(db.Model):
    __tablename__ = 'mensagens'
    id = db.Column(db.Integer, primary_key=True)
    evento_id = db.Column(db.Integer, db.ForeignKey('eventos.id'), nullable=False)
    remetente_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    destinatario_id = db.Column(db.Integer, db.ForeignKey('usuarios.id'), nullable=False)
    texto = db.Column(db.Text, nullable=False)
    data_criacao = db.Column(db.DateTime, default=db.func.now())

    evento = db.relationship('Evento', backref='mensagens')
    remetente = db.relationship('Usuario', foreign_keys=[remetente_id])
    destinatario = db.relationship('Usuario', foreign_keys=[destinatario_id])

# --- HELPERS ---

def shuffle_with_restrictions(u_ids, restrictions_set):
    import random
    n = len(u_ids)
    u_ids_shuffled = list(u_ids)
    random.shuffle(u_ids_shuffled)
    
    res = {}
    avail = set(u_ids)

    def backtrack(idx):
        if idx == n: return True
        u = u_ids_shuffled[idx]
        choices = list(avail)
        random.shuffle(choices)
        for choice in choices:
            if u == choice: continue
            if (u, choice) in restrictions_set: continue
            
            res[u] = choice
            avail.remove(choice)
            if backtrack(idx + 1): return True
            avail.add(choice)
        return False

    if backtrack(0): return res
    return None

def process_photo(file):
    if file and file.filename != '':
        return base64.b64encode(file.read()).decode('utf-8')
    return None

def is_admin():
    return session.get('is_admin', False)

# --- ROTAS ---

@app.route('/')
def index():
    if 'user_id' in session: return redirect(url_for('dashboard'))
    return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        senha = request.form.get('senha')
        user = Usuario.query.filter_by(email=email).first()
        if user and check_password_hash(user.senha, senha):
            session['user_id'] = user.id
            session['user_nome'] = user.nome
            session['is_admin'] = user.is_admin
            return redirect(url_for('dashboard'))
        flash('Email ou senha incorretos!', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session: return redirect(url_for('login'))
    usuario = Usuario.query.get(session['user_id'])
    meus_eventos = EventoParticipante.query.filter_by(usuario_id=usuario.id).all()
    # Mensagens recebidas do meu "Amigo Oculto" (quem me tirou)
    # Aqui remetente é o cara que me tirou, destinatario sou eu.
    mensagens = MensagemSecreta.query.filter_by(destinatario_id=usuario.id).order_by(MensagemSecreta.data_criacao.desc()).all()
    # Busca quem tirou o usuário atual em cada evento (para poder responder)
    quem_me_tirou = {}
    for ep in meus_eventos:
        tirou_me = EventoParticipante.query.filter_by(evento_id=ep.evento_id, amigo_id=usuario.id).first()
        if tirou_me:
            quem_me_tirou[ep.evento_id] = tirou_me.usuario_id

    return render_template('dashboard.html', usuario=usuario, meus_eventos=meus_eventos, mensagens=mensagens, quem_me_tirou=quem_me_tirou)

@app.route('/perfil', methods=['GET', 'POST'])
def perfil():
    if 'user_id' not in session: return redirect(url_for('login'))
    u = Usuario.query.get(session['user_id'])
    if request.method == 'POST':
        u.nome = request.form.get('nome')
        u.email = request.form.get('email')
        u.lista_desejos = request.form.get('lista_desejos')
        if request.form.get('senha'):
            u.senha = generate_password_hash(request.form.get('senha'))
        
        # Lógica para foto
        if 'remover_foto' in request.form:
            u.foto = None
        else:
            nova_foto = process_photo(request.files.get('foto'))
            if nova_foto: u.foto = nova_foto
        
        db.session.commit()
        session['user_nome'] = u.nome
        flash('Perfil atualizado!', 'success')
        return redirect(url_for('dashboard'))
    return render_template('perfil.html', u=u)

@app.route('/amigo/<int:evento_id>')
def ver_amigo(evento_id):
    if 'user_id' not in session: return redirect(url_for('login'))
    participacao = EventoParticipante.query.filter_by(evento_id=evento_id, usuario_id=session['user_id']).first()
    if not participacao or not participacao.amigo_id:
        flash('Sorteio não disponível.', 'info')
        return redirect(url_for('dashboard'))
    
    # Mensagens enviadas por MIM para o MEU amigo sorteado
    mensagens = MensagemSecreta.query.filter_by(evento_id=evento_id, remetente_id=session['user_id'], destinatario_id=participacao.amigo_id).order_by(MensagemSecreta.data_criacao.asc()).all()
    
    return render_template('amigo.html', amigo=participacao.amigo, evento=participacao.evento, mensagens=mensagens)

# --- CHAT API ---
TYPING_STATUS = {} # {(evento_id, remetente_id, destinatario_id): timestamp}

@app.route('/api/chat/sync/<int:evento_id>/<int:destinatario_id>')
def chat_sync(evento_id, destinatario_id):
    if 'user_id' not in session: return {"error": "unauthorized"}, 401
    
    # Busca mensagens nos dois sentidos
    mensagens = MensagemSecreta.query.filter(
        MensagemSecreta.evento_id == evento_id,
        ((MensagemSecreta.remetente_id == session['user_id']) & (MensagemSecreta.destinatario_id == destinatario_id)) |
        ((MensagemSecreta.remetente_id == destinatario_id) & (MensagemSecreta.destinatario_id == session['user_id']))
    ).order_by(MensagemSecreta.data_criacao.asc()).all()

    # Verifica se o outro está digitando (destinatario -> eu)
    other_typing = False
    key = (evento_id, destinatario_id, session['user_id'])
    if key in TYPING_STATUS:
        if time.time() - TYPING_STATUS[key] < 5: # Typing lasts for 5 seconds
            other_typing = True
    
    return {
        "mensagens": [{
            "texto": m.texto,
            "eu": m.remetente_id == session['user_id'],
            "data": m.data_criacao.strftime('%H:%M')
        } for m in mensagens],
        "is_typing": other_typing
    }

@app.route('/api/chat/typing', methods=['POST'])
def chat_typing():
    if 'user_id' not in session: return {"status": "unauthorized"}, 401
    data = request.json
    key = (data['evento_id'], session['user_id'], data['destinatario_id'])
    TYPING_STATUS[key] = time.time()
    return {"status": "ok"}

@app.route('/chat/enviar/api', methods=['POST'])
def enviar_mensagem_api():
    if 'user_id' not in session: return {"error": "unauthorized"}, 401
    txt = request.json.get('mensagem')
    e_id = request.json.get('evento_id')
    d_id = request.json.get('destinatario_id')
    
    if txt:
        msg = MensagemSecreta(evento_id=e_id, remetente_id=session['user_id'], destinatario_id=d_id, texto=txt)
        db.session.add(msg)
        db.session.commit()

        # Notificar Destinatário via E-mail
        corpo = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #eee; border-radius: 10px;">
            <h2 style="color: #6366f1;">Você recebeu uma mensagem secreta! 📬</h2>
            <p>Olá!</p>
            <p>Seu <strong>Amigo Oculto</strong> (ou quem você tirou) acaba de te enviar uma mensagem no <strong>SecretJoy</strong>.</p>
            <div style="background: #f9f9f9; padding: 15px; border-left: 4px solid #6366f1; font-style: italic; margin: 20px 0;">
                "{txt[:50]}..."
            </div>
            <p>Para ler a mensagem completa e responder, acesse seu Dashboard:</p>
            <a href="{request.url_root}dashboard" style="display: inline-block; padding: 12px 25px; background: #6366f1; color: white; text-decoration: none; border-radius: 8px; font-weight: bold;">Ver Mensagem e Responder</a>
            <p style="color: #888; font-size: 0.8rem; margin-top: 20px;">Este é um e-mail automático do SecretJoy.</p>
        </div>
        """
        enviar_email("Nova Mensagem Secreta no SecretJoy! 💬", msg.destinatario.email, corpo)

    return {"status": "ok"}

@app.route('/chat/enviar', methods=['POST'])
def enviar_mensagem():
    if 'user_id' not in session: return redirect(url_for('login'))
    e_id = request.form.get('evento_id')
    d_id = request.form.get('destinatario_id')
    txt = request.form.get('mensagem')
    
    if txt:
        msg = MensagemSecreta(evento_id=e_id, remetente_id=session['user_id'], destinatario_id=d_id, texto=txt)
        db.session.add(msg)
        db.session.commit()
        flash('Mensagem enviada anônimamente!', 'success')
    return redirect(url_for('ver_amigo', evento_id=e_id))

# --- ADMIN: USUÁRIOS ---

@app.route('/admin')
def admin_panel():
    if not is_admin(): return redirect(url_for('dashboard'))
    return render_template('admin.html', usuarios=Usuario.query.all(), eventos=Evento.query.all())

@app.route('/usuario/criar', methods=['POST'])
def criar_usuario():
    if not is_admin(): return redirect(url_for('dashboard'))
    foto_b64 = process_photo(request.files.get('foto'))
    u = Usuario(
        nome=request.form.get('nome'),
        email=request.form.get('email'),
        senha=generate_password_hash(request.form.get('senha')),
        foto=foto_b64,
        lista_desejos=request.form.get('lista_desejos'),
        is_admin=(request.form.get('is_admin') == 'on')
    )
    db.session.add(u)
    db.session.commit()
    flash('Usuário criado!', 'success')
    return redirect(url_for('admin_panel'))

@app.route('/usuario/editar/<int:id>', methods=['GET', 'POST'])
def editar_usuario(id):
    if not is_admin(): return redirect(url_for('dashboard'))
    u = Usuario.query.get(id)
    if request.method == 'POST':
        u.nome = request.form.get('nome')
        u.email = request.form.get('email')
        if request.form.get('senha'):
            u.senha = generate_password_hash(request.form.get('senha'))
        
        nova_foto = process_photo(request.files.get('foto'))
        if nova_foto: u.foto = nova_foto
        
        u.is_admin = (request.form.get('is_admin') == 'on')
        db.session.commit()
        flash('Usuário atualizado!', 'success')
        return redirect(url_for('admin_panel'))
    return render_template('usuario_editar.html', u=u)

@app.route('/usuario/remover/<int:id>', methods=['POST'])
def remover_usuario(id):
    if not is_admin() or id == session['user_id']: return redirect(url_for('admin_panel'))
    u = Usuario.query.get(id)
    db.session.delete(u)
    db.session.commit()
    return redirect(url_for('admin_panel'))

# --- ADMIN: EVENTOS ---

@app.route('/evento/criar', methods=['POST'])
def criar_evento():
    if not is_admin(): return redirect(url_for('dashboard'))
    ev = Evento(
        nome=request.form.get('nome'),
        valor_min=request.form.get('valor_min', 0),
        valor_max=request.form.get('valor_max', 0)
    )
    db.session.add(ev)
    db.session.commit()
    return redirect(url_for('admin_panel'))

@app.route('/evento/deletar/<int:id>', methods=['POST'])
def deletar_evento(id):
    if not is_admin(): return redirect(url_for('dashboard'))
    db.session.delete(Evento.query.get(id))
    db.session.commit()
    return redirect(url_for('admin_panel'))

@app.route('/evento/<int:id>')
def gerenciar_evento(id):
    if not is_admin(): return redirect(url_for('dashboard'))
    ev = Evento.query.get(id)
    # Usuários que não estão neste evento
    ids_no_evento = [p.usuario_id for p in ev.participantes]
    usuarios_fora = Usuario.query.filter(~Usuario.id.in_(ids_no_evento)).all() if ids_no_evento else Usuario.query.all()
    
    # Restrições
    restricoes = Restricao.query.all()
    
    return render_template('evento_detalhes.html', evento=ev, usuarios_fora=usuarios_fora, restricoes=restricoes, todos_usuarios=Usuario.query.all())

@app.route('/restricao/add', methods=['POST'])
def add_restricao():
    if not is_admin(): return redirect(url_for('dashboard'))
    u1 = request.form.get('usuario_id')
    u2 = request.form.get('excluido_id')
    if u1 != u2:
        r = Restricao(usuario_id=u1, excluido_id=u2)
        db.session.add(r)
        db.session.commit()
    return redirect(request.referrer)

@app.route('/evento/add-participantes', methods=['POST'])
def add_participantes():
    if not is_admin(): return redirect(url_for('dashboard'))
    e_id = request.form.get('evento_id')
    u_ids = request.form.getlist('usuario_ids')
    for uid in u_ids:
        if not EventoParticipante.query.filter_by(evento_id=e_id, usuario_id=uid).first():
            db.session.add(EventoParticipante(evento_id=e_id, usuario_id=uid))
    db.session.commit()
    return redirect(url_for('gerenciar_evento', id=e_id))

@app.route('/evento/remove-participante', methods=['POST'])
def remove_participante():
    if not is_admin(): return redirect(url_for('dashboard'))
    ep = EventoParticipante.query.get(request.form.get('participante_id'))
    e_id = ep.evento_id
    db.session.delete(ep)
    db.session.commit()
    return redirect(url_for('gerenciar_evento', id=e_id))

@app.route('/evento/sortear', methods=['POST'])
def sortear_evento():
    if not is_admin(): return redirect(url_for('dashboard'))
    ev = Evento.query.get(request.form.get('evento_id'))
    parts = list(ev.participantes)
    if len(parts) < 2:
        flash('Mínimo 2 pessoas!', 'error')
        return redirect(url_for('gerenciar_evento', id=ev.id))
    
    u_ids = [p.usuario_id for p in parts]
    restricoes_db = Restricao.query.filter(Restricao.usuario_id.in_(u_ids)).all()
    restricoes_set = set((r.usuario_id, r.excluido_id) for r in restricoes_db)
    
    resultado = shuffle_with_restrictions(u_ids, restricoes_set)
    
    if not resultado:
        flash('Impossível realizar sorteio com as restrições atuais!', 'error')
        return redirect(url_for('gerenciar_evento', id=ev.id))
    
    for p in parts:
        p.amigo_id = resultado[p.usuario_id]
        
    ev.sorteado = True
    db.session.commit()

    # Notificar TODOS os participantes via e-mail
    for p in parts:
        corpo = f"""
        <div style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #eee; border-radius: 10px;">
            <h2 style="color: #6366f1;">O sorteio do {ev.nome} foi realizado! 🎁</h2>
            <p>Olá, <strong>{p.usuario.nome}</strong>!</p>
            <p>O administrador acaba de realizar o sorteio do Amigo Oculto.</p>
            <p>Agora você já pode descobrir quem você tirou e começar a planejar o presente perfeito!</p>
            <div style="background: #f9f9f9; padding: 15px; border-radius: 8px; margin: 20px 0;">
                <p><strong>Orçamento Sugerido:</strong> R$ {ev.valor_min} - R$ {ev.valor_max}</p>
            </div>
            <a href="{request.url_root}dashboard" style="display: inline-block; padding: 12px 25px; background: #6366f1; color: white; text-decoration: none; border-radius: 8px; font-weight: bold;">VER MEU AMIGO OCULTO 🎁</a>
            <p style="color: #888; font-size: 0.8rem; margin-top: 20px;">Divirta-se e feliz sorteio!</p>
        </div>
        """
        enviar_email(f"Sorteio Realizado: {ev.nome} 🎊", p.usuario.email, corpo)

    flash('Sorteio realizado com sucesso e todos foram notificados!', 'success')
    return redirect(url_for('gerenciar_evento', id=ev.id))

@app.route('/evento/cancelar', methods=['POST'])
def cancelar_sorteio():
    if not is_admin(): return redirect(url_for('dashboard'))
    ev = Evento.query.get(request.form.get('evento_id'))
    for p in ev.participantes: p.amigo_id = None
    ev.sorteado = False
    db.session.commit()
    return redirect(url_for('gerenciar_evento', id=ev.id))

with app.app_context():
    db.create_all()

if __name__ == '__main__':
    app.run(debug=True)
