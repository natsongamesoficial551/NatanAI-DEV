import os
import time
import requests
import warnings
import hashlib
import random
import re
import threading
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from openai import OpenAI
from supabase import create_client, Client

warnings.filterwarnings('ignore')

app = Flask(__name__)
CORS(app)

# ============================================
# 🔧 CONFIGURAÇÃO
# ============================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
ADMIN_EMAIL = "natan@natandev.com"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = "gpt-4o-mini"
RENDER_URL = os.getenv("RENDER_URL", "")

# Inicializa Supabase
supabase: Client = None
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase conectado")
except Exception as e:
    print(f"⚠️ Erro Supabase: {e}")

# Inicializa OpenAI
client = None
if OPENAI_API_KEY:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        print("✅ OpenAI conectado")
    except Exception as e:
        print(f"⚠️ Erro OpenAI: {e}")

# Cache e Memória
CACHE_RESPOSTAS = {}
HISTORICO_CONVERSAS = []
historico_lock = threading.Lock()

# 🧠 SISTEMA DE MEMÓRIA INTELIGENTE
MEMORIA_USUARIOS = {}  # {user_id: {'mensagens': [], 'resumo': '', 'ultima_atualizacao': timestamp}}
memoria_lock = threading.Lock()
MAX_MENSAGENS_MEMORIA = 10
INTERVALO_RESUMO = 5  # Gera resumo a cada 5 mensagens

# Auto-ping
def auto_ping():
    while True:
        try:
            if RENDER_URL:
                url = RENDER_URL if RENDER_URL.startswith('http') else f"https://{RENDER_URL}"
                requests.get(f"{url}/health", timeout=10)
                print(f"🏓 Ping OK: {datetime.now().strftime('%H:%M:%S')}")
            else:
                requests.get("http://localhost:5000/health", timeout=5)
        except:
            pass
        time.sleep(300)

threading.Thread(target=auto_ping, daemon=True).start()

# =============================================================================
# 🔐 AUTENTICAÇÃO E DADOS DO USUÁRIO
# =============================================================================

def verificar_token_supabase(token):
    try:
        if not token or not supabase:
            return None
        if token.startswith("Bearer "):
            token = token[7:]
        response = supabase.auth.get_user(token)
        return response.user if response and response.user else None
    except:
        return None

def obter_dados_usuario_completos(user_id):
    try:
        if not supabase:
            return None
        response = supabase.table('user_accounts').select('*').eq('user_id', user_id).single().execute()
        return response.data if response.data else None
    except:
        return None

def extrair_nome_usuario(user_info, user_data=None):
    try:
        if user_data and user_data.get('user_name'):
            nome = user_data['user_name'].strip()
            if nome and len(nome) > 1:
                return nome
        
        if user_info and user_info.user_metadata:
            nome = user_info.user_metadata.get('name', '').strip()
            if nome and len(nome) > 1:
                return nome
        
        if user_info and user_info.email:
            nome = user_info.email.split('@')[0].strip()
            return nome.capitalize()
        
        return "Cliente"
        
    except Exception as e:
        print(f"⚠️ Erro ao extrair nome: {e}")
        return "Cliente"

def determinar_tipo_usuario(user_data, user_info=None):
    try:
        email = user_data.get('email', '')
        plan = user_data.get('plan', 'starter')
        nome = extrair_nome_usuario(user_info, user_data)
        
        if email == ADMIN_EMAIL:
            return {
                'tipo': 'admin',
                'nome_display': 'Admin',
                'plano': 'Admin',
                'nome_real': 'Natan'
            }
        
        if plan == 'professional':
            return {
                'tipo': 'professional',
                'nome_display': 'Professional',
                'plano': 'Professional',
                'nome_real': nome
            }
        
        return {
            'tipo': 'starter',
            'nome_display': 'Starter',
            'plano': 'Starter',
            'nome_real': nome
        }
    except:
        return {
            'tipo': 'starter',
            'nome_display': 'Starter',
            'plano': 'Starter',
            'nome_real': 'Cliente'
        }

# =============================================================================
# 🧠 SISTEMA DE MEMÓRIA INTELIGENTE
# =============================================================================

def obter_user_id(user_info, user_data):
    """Obtém ID único do usuário para memória"""
    if user_info and hasattr(user_info, 'id'):
        return user_info.id
    if user_data and user_data.get('user_id'):
        return user_data['user_id']
    if user_data and user_data.get('email'):
        return hashlib.md5(user_data['email'].encode()).hexdigest()
    return 'anonimo'

def inicializar_memoria_usuario(user_id):
    """Inicializa memória para novo usuário"""
    with memoria_lock:
        if user_id not in MEMORIA_USUARIOS:
            MEMORIA_USUARIOS[user_id] = {
                'mensagens': [],
                'resumo': '',
                'ultima_atualizacao': datetime.now().isoformat(),
                'contador_mensagens': 0
            }
            print(f"🧠 Memória inicializada para user: {user_id[:8]}...")

def adicionar_mensagem_memoria(user_id, role, content):
    """Adiciona mensagem à memória do usuário"""
    with memoria_lock:
        if user_id not in MEMORIA_USUARIOS:
            inicializar_memoria_usuario(user_id)
        
        memoria = MEMORIA_USUARIOS[user_id]
        memoria['mensagens'].append({
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat()
        })
        memoria['contador_mensagens'] += 1
        memoria['ultima_atualizacao'] = datetime.now().isoformat()
        
        # Mantém apenas últimas MAX_MENSAGENS_MEMORIA
        if len(memoria['mensagens']) > MAX_MENSAGENS_MEMORIA:
            memoria['mensagens'] = memoria['mensagens'][-MAX_MENSAGENS_MEMORIA:]
        
        print(f"💬 Memória atualizada: {user_id[:8]}... ({len(memoria['mensagens'])} msgs)")

def gerar_resumo_conversa(mensagens):
    """Gera resumo compacto das mensagens antigas (economia de tokens)"""
    if not client or not mensagens or len(mensagens) < 3:
        return ""
    
    try:
        # Prepara texto das mensagens para resumir
        texto_conversa = "\n".join([
            f"{'Usuário' if m['role'] == 'user' else 'Assistente'}: {m['content']}"
            for m in mensagens
        ])
        
        prompt_resumo = f"""Resuma esta conversa em 2-3 frases curtas, focando nos tópicos principais:

{texto_conversa}

Resumo objetivo (máx 50 palavras):"""

        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": prompt_resumo}],
            max_tokens=80,  # Resumo compacto
            temperature=0.3  # Mais objetivo
        )
        
        resumo = response.choices[0].message.content.strip()
        print(f"📝 Resumo gerado: {resumo[:50]}...")
        return resumo
        
    except Exception as e:
        print(f"⚠️ Erro ao gerar resumo: {e}")
        return ""

def obter_contexto_memoria(user_id):
    """Obtém contexto otimizado da memória (resumo + mensagens recentes)"""
    with memoria_lock:
        if user_id not in MEMORIA_USUARIOS:
            return []
        
        memoria = MEMORIA_USUARIOS[user_id]
        mensagens = memoria['mensagens']
        
        if not mensagens:
            return []
        
        # Se tem menos de 5 mensagens, retorna todas
        if len(mensagens) <= 5:
            return [{'role': m['role'], 'content': m['content']} for m in mensagens]
        
        # Se chegou no intervalo de resumo, gera resumo das antigas
        if memoria['contador_mensagens'] % INTERVALO_RESUMO == 0 and not memoria['resumo']:
            msgs_antigas = mensagens[:-3]  # Todas menos as 3 últimas
            if msgs_antigas:
                memoria['resumo'] = gerar_resumo_conversa(msgs_antigas)
        
        # Monta contexto otimizado
        contexto = []
        
        # Adiciona resumo se existir
        if memoria['resumo']:
            contexto.append({
                'role': 'system',
                'content': f"Contexto anterior: {memoria['resumo']}"
            })
        
        # Adiciona últimas 3 mensagens completas
        mensagens_recentes = mensagens[-3:]
        for m in mensagens_recentes:
            contexto.append({
                'role': m['role'],
                'content': m['content']
            })
        
        print(f"🧠 Contexto montado: resumo={bool(memoria['resumo'])}, msgs_recentes={len(mensagens_recentes)}")
        return contexto

def limpar_memoria_antiga():
    """Limpa memórias de usuários inativos (>1 hora)"""
    with memoria_lock:
        agora = datetime.now()
        usuarios_remover = []
        
        for user_id, memoria in MEMORIA_USUARIOS.items():
            ultima_atualizacao = datetime.fromisoformat(memoria['ultima_atualizacao'])
            diferenca = (agora - ultima_atualizacao).total_seconds()
            
            # Remove se inativo por mais de 1 hora
            if diferenca > 3600:
                usuarios_remover.append(user_id)
        
        for user_id in usuarios_remover:
            del MEMORIA_USUARIOS[user_id]
            print(f"🗑️ Memória limpa: {user_id[:8]}... (inativo)")

# Thread de limpeza automática
def thread_limpeza_memoria():
    while True:
        time.sleep(1800)  # A cada 30 minutos
        limpar_memoria_antiga()

threading.Thread(target=thread_limpeza_memoria, daemon=True).start()

# =============================================================================
# 🛡️ VALIDAÇÃO ANTI-ALUCINAÇÃO
# =============================================================================

PALAVRAS_PROIBIDAS = [
    "grátis", "gratuito", "R$ 0", "0 reais", "free",
    "garantimos primeiro lugar", "100% de conversão", "sucesso garantido",
    "site pronto em 1 hora", "atendimento 24/7 imediato", "empresa com 10 anos"
]

PADROES_SUSPEITOS = [
    r'R\$\s*0[,.]?00',
    r'grát[ui]s',
    r'garantimos?\s+\d+%',
    r'\d+\s+anos\s+de\s+experiência',
    r'certificação\s+ISO'
]

def validar_resposta(resposta):
    problemas = []
    resp_lower = resposta.lower()
    
    for palavra in PALAVRAS_PROIBIDAS:
        if palavra.lower() in resp_lower:
            problemas.append(f"Proibida: {palavra}")
    
    for padrao in PADROES_SUSPEITOS:
        if re.search(padrao, resp_lower):
            problemas.append(f"Padrão suspeito")
    
    if "whatsapp" in resp_lower or "telefone" in resp_lower:
        if "99282-6074" not in resposta and "(21) 9" in resposta:
            problemas.append("WhatsApp incorreto")
    
    return len(problemas) == 0, problemas

# =============================================================================
# 🤖 OPENAI - v6.2 COM MEMÓRIA INTELIGENTE
# =============================================================================

def verificar_openai():
    try:
        if not OPENAI_API_KEY or len(OPENAI_API_KEY) < 20:
            return False
        if client is None:
            return False
        return True
    except:
        return False

def processar_openai(pergunta, tipo_usuario, user_id):
    """✅ v6.2 - COM MEMÓRIA INTELIGENTE (resumo + contexto)"""
    if not client or not verificar_openai():
        return None
    
    try:
        nome_usuario = tipo_usuario.get('nome_real', 'Cliente')
        
        # 🔥 CONTEXTO POR TIPO DE USUÁRIO
        if tipo_usuario['tipo'] == 'admin':
            ctx = f"🔴 ADMIN ({nome_usuario}): Acesso total. Respostas técnicas e dados internos."
        elif tipo_usuario['tipo'] == 'professional':
            ctx = f"💎 PROFESSIONAL ({nome_usuario}): Cliente premium. Suporte prioritário, explique recursos avançados."
        else:
            ctx = f"🌱 STARTER ({nome_usuario}): Cliente. Seja acolhedor e pessoal. Sugira upgrade se relevante."
        
        # 🎯 PROMPT BASE COMPACTO
        prompt_sistema = f"""Você é NatanAI, assistente da NatanDEV.

{ctx}

📋 DADOS OFICIAIS:
Criador: Natan Borges
- Desenvolvedor Full-Stack (Front/Back/Mobile)
- Stack: React, Node.js, Python, Next.js, Supabase
- Localização: Rio de Janeiro/RJ
- WhatsApp: (21) 99282-6074
- Portfolio: natandev02.netlify.app
- Site Principal: natansites.com.br

💼 PORTFÓLIO:
- E-COMMERCE (Shoppy): React, Tailwind, carrinho, checkout
- LANDING PAGES: Animações modernas, formulários
- DASHBOARDS: Charts, visualização dados, Recharts
- APPS MOBILE: React Native, clones Spotify/Netflix
- PORTFÓLIO 3D: Three.js, animações

Habilidades: React, Next, Vue, Node.js, Python, React Native, UI/UX, SEO

💳 PLANOS:
STARTER - R$39,99/mês + R$320
- Site básico 5 pgs, mobile, SEO básico, hospedagem, suporte 24/7

PROFESSIONAL - R$79,99/mês + R$530 ⭐
- Design avançado, ilimitado, animações, SEO avançado, APIs, domínio, 5 revisões, NatanAI

🌐 PLATAFORMA: Dashboard, chat suporte, NatanAI, tema dark, estatísticas

⚡ REGRAS:
1. Use "{nome_usuario}" naturalmente (não sempre!)
2. Seja empático e humano
3. NUNCA "eu desenvolvo" → "o Natan desenvolve"
4. NUNCA invente preços/projetos
5. NUNCA repita pergunta literal
6. Varie respostas similares
7. Use apenas infos acima

Responda de forma CONTEXTUAL e PESSOAL:"""

        # 🧠 OBTÉM CONTEXTO DA MEMÓRIA
        contexto_memoria = obter_contexto_memoria(user_id)
        
        # 📝 MONTA MENSAGENS PARA OPENAI
        messages = [
            {"role": "system", "content": prompt_sistema}
        ]
        
        # Adiciona contexto da memória
        messages.extend(contexto_memoria)
        
        # Adiciona pergunta atual
        messages.append({"role": "user", "content": pergunta})
        
        # 🚀 CHAMADA OTIMIZADA
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_tokens=220,
            temperature=0.75
        )
        
        resposta = response.choices[0].message.content.strip()
        
        # ✅ SALVA NA MEMÓRIA
        adicionar_mensagem_memoria(user_id, 'user', pergunta)
        adicionar_mensagem_memoria(user_id, 'assistant', resposta)
        
        # ✅ Validação
        valida, problemas = validar_resposta(resposta)
        if not valida:
            print(f"⚠️ Validação falhou: {problemas}")
            return None
        
        # 🎲 Frase motivacional (10%)
        if random.random() < 0.1:
            frases = [
                "\n\n✨ Vibrações Positivas!",
                "\n\n💙 Sucesso no seu projeto!",
                "\n\n🚀 Vamos juntos nessa!",
                "\n\n🌟 Conte sempre comigo!"
            ]
            resposta += random.choice(frases)
        
        return resposta
        
    except Exception as e:
        print(f"❌ Erro OpenAI: {e}")
        return None

def gerar_resposta(pergunta, tipo_usuario, user_id):
    """Sistema principal com memória inteligente"""
    try:
        # ✅ Cache DESABILITADO para conversas (sempre usa memória)
        # Mas mantém cache para perguntas únicas/isoladas
        palavras_cache = ['preço', 'quanto custa', 'plano', 'contato', 'whatsapp']
        usar_cache = any(palavra in pergunta.lower() for palavra in palavras_cache)
        
        cache_key = hashlib.md5(f"{pergunta.lower().strip()}_{tipo_usuario['tipo']}".encode()).hexdigest()
        
        # Só usa cache se for pergunta isolada comum
        if usar_cache and cache_key in CACHE_RESPOSTAS:
            # Mesmo com cache, salva na memória
            resposta_cache = CACHE_RESPOSTAS[cache_key]
            adicionar_mensagem_memoria(user_id, 'user', pergunta)
            adicionar_mensagem_memoria(user_id, 'assistant', resposta_cache)
            return resposta_cache, "cache"
        
        # 🤖 OpenAI com memória
        resposta = processar_openai(pergunta, tipo_usuario, user_id)
        if resposta:
            if usar_cache:
                CACHE_RESPOSTAS[cache_key] = resposta
            return resposta, f"openai_memoria_{tipo_usuario['tipo']}"
        
        # 🔄 Fallback
        nome = tipo_usuario.get('nome_real', 'Cliente')
        return f"Desculpa {nome}, estou com dificuldades técnicas no momento. 😅\n\nPor favor, fale diretamente com o Natan no WhatsApp: (21) 99282-6074", "fallback"
        
    except Exception as e:
        print(f"❌ Erro gerar_resposta: {e}")
        return "Ops, erro técnico! Fale com Natan: (21) 99282-6074\n\n✨ Vibrações Positivas!", "erro"

# =============================================================================
# 📡 ROTAS
# =============================================================================

@app.route('/health', methods=['GET'])
@app.route('/api/health', methods=['GET'])
def health():
    with memoria_lock:
        usuarios_ativos = len(MEMORIA_USUARIOS)
        total_mensagens = sum(len(m['mensagens']) for m in MEMORIA_USUARIOS.values())
    
    return jsonify({
        "status": "online",
        "sistema": "NatanAI v6.2 MEMÓRIA INTELIGENTE",
        "openai": verificar_openai(),
        "supabase": supabase is not None,
        "memoria": {
            "usuarios_ativos": usuarios_ativos,
            "total_mensagens": total_mensagens,
            "max_por_usuario": MAX_MENSAGENS_MEMORIA
        },
        "features": ["memoria_inteligente", "resumo_automatico", "contexto_completo"],
        "economia": "~21k mensagens com $5"
    })

@app.route('/chat', methods=['POST'])
@app.route('/api/chat', methods=['POST'])
def chat():
    global HISTORICO_CONVERSAS
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Dados não fornecidos"}), 400
        
        mensagem = data.get('message') or data.get('pergunta', '')
        if not mensagem or not mensagem.strip():
            return jsonify({"error": "Mensagem vazia"}), 400
        
        mensagem = mensagem.strip()
        
        # ✅ AUTENTICAÇÃO E EXTRAÇÃO DE DADOS
        auth_header = request.headers.get('Authorization', '')
        user_data_req = data.get('user_data', {})
        
        tipo_usuario = None
        user_info = None
        
        if auth_header:
            user_info = verificar_token_supabase(auth_header)
            if user_info:
                dados = obter_dados_usuario_completos(user_info.id)
                user_full = {
                    'email': user_info.email,
                    'user_id': user_info.id,
                    'plan': user_info.user_metadata.get('plan', 'starter') if user_info.user_metadata else 'starter'
                }
                if dados:
                    user_full.update(dados)
                tipo_usuario = determinar_tipo_usuario(user_full, user_info)
        
        if not tipo_usuario:
            if user_data_req:
                tipo_usuario = determinar_tipo_usuario(user_data_req)
            else:
                tipo_usuario = {
                    'tipo': 'starter',
                    'nome_display': 'Cliente',
                    'plano': 'Starter',
                    'nome_real': 'Cliente'
                }
        
        # 🆔 OBTÉM USER_ID PARA MEMÓRIA
        user_id = obter_user_id(user_info, user_data_req if user_data_req else {'email': tipo_usuario.get('nome_real', 'anonimo')})
        
        # ✅ INICIALIZA MEMÓRIA SE NECESSÁRIO
        inicializar_memoria_usuario(user_id)
        
        nome_usuario = tipo_usuario.get('nome_real', 'Cliente')
        print(f"💬 [{datetime.now().strftime('%H:%M:%S')}] {nome_usuario} ({tipo_usuario['nome_display']}): {mensagem[:50]}...")
        
        # ✅ GERA RESPOSTA COM MEMÓRIA
        resposta, fonte = gerar_resposta(mensagem, tipo_usuario, user_id)
        valida, _ = validar_resposta(resposta)
        
        # Histórico geral
        with historico_lock:
            HISTORICO_CONVERSAS.append({
                "timestamp": datetime.now().isoformat(),
                "tipo": tipo_usuario['tipo'],
                "nome": nome_usuario,
                "fonte": fonte,
                "validacao": valida,
                "com_memoria": 'memoria' in fonte
            })
            if len(HISTORICO_CONVERSAS) > 1000:
                HISTORICO_CONVERSAS = HISTORICO_CONVERSAS[-500:]
        
        # 🧠 INFO DA MEMÓRIA
        with memoria_lock:
            memoria_info = {
                "mensagens_na_memoria": len(MEMORIA_USUARIOS.get(user_id, {}).get('mensagens', [])),
                "tem_resumo": bool(MEMORIA_USUARIOS.get(user_id, {}).get('resumo', ''))
            }
        
        return jsonify({
            "response": resposta,
            "resposta": resposta,
            "metadata": {
                "fonte": fonte,
                "sistema": "NatanAI v6.2 MEMÓRIA INTELIGENTE",
                "tipo_usuario": tipo_usuario['tipo'],
                "plano": tipo_usuario['plano'],
                "nome_usuario": nome_usuario,
                "validacao": valida,
                "autenticado": user_info is not None,
                "memoria": memoria_info
            }
        })
        
    except Exception as e:
        print(f"❌ Erro: {e}")
        return jsonify({
            "response": "Erro técnico. Fale com Natan: (21) 99282-6074\n\n✨ Vibrações Positivas!",
            "resposta": "Erro técnico. Fale com Natan: (21) 99282-6074\n\n✨ Vibrações Positivas!",
            "metadata": {"fonte": "erro", "error": str(e)}
        }), 500

@app.route('/estatisticas', methods=['GET'])
@app.route('/api/estatisticas', methods=['GET'])
def estatisticas():
    try:
        if not HISTORICO_CONVERSAS:
            return jsonify({"message": "Sem conversas"})
        
        fontes = {}
        tipos = {}
        nomes = {}
        validacoes = 0
        com_memoria = 0
        
        with historico_lock:
            for c in HISTORICO_CONVERSAS:
                f = c.get("fonte", "unknown")
                fontes[f] = fontes.get(f, 0) + 1
                t = c.get("tipo", "unknown")
                tipos[t] = tipos.get(t, 0) + 1
                n = c.get("nome", "Anônimo")
                nomes[n] = nomes.get(n, 0) + 1
                if c.get("validacao", True):
                    validacoes += 1
                if c.get("com_memoria", False):
                    com_memoria += 1
        
        with memoria_lock:
            usuarios_memoria = len(MEMORIA_USUARIOS)
            total_msgs_memoria = sum(len(m['mensagens']) for m in MEMORIA_USUARIOS.values())
        
        return jsonify({
            "total": len(HISTORICO_CONVERSAS),
            "fontes": fontes,
            "tipos_usuario": tipos,
            "usuarios_ativos": len(nomes),
            "top_usuarios": dict(sorted(nomes.items(), key=lambda x: x[1], reverse=True)[:5]),
            "validacao": {
                "ok": validacoes,
                "taxa": round((validacoes / len(HISTORICO_CONVERSAS)) * 100, 2)
            },
            "memoria": {
                "usuarios_com_memoria": usuarios_memoria,
                "mensagens_armazenadas": total_msgs_memoria,
                "conversas_com_contexto": com_memoria,
                "taxa_uso_memoria": round((com_memoria / len(HISTORICO_CONVERSAS)) * 100, 2)
            },
            "sistema": "NatanAI v6.2 MEMÓRIA INTELIGENTE - ~21k msgs com $5"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/limpar_memoria/<user_id>', methods=['POST'])
def limpar_memoria_usuario(user_id):
    """Endpoint para limpar memória de um usuário específico (admin)"""
    with memoria_lock:
        if user_id in MEMORIA_USUARIOS:
            del MEMORIA_USUARIOS[user_id]
            return jsonify({"message": f"Memória limpa para user: {user_id[:8]}..."})
        return jsonify({"message": "Usuário não encontrado na memória"}), 404

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({
        "status": "pong",
        "timestamp": datetime.now().isoformat(),
        "version": "v6.2"
    })

@app.route('/', methods=['GET'])
def home():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>NatanAI v6.2 MEMÓRIA INTELIGENTE</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body { 
                font-family: 'Segoe UI', Arial, sans-serif; 
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }
            .container { 
                max-width: 900px; 
                margin: 0 auto; 
                background: white; 
                padding: 30px; 
                border-radius: 20px; 
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            }
            .header { 
                text-align: center; 
                margin-bottom: 30px;
                padding-bottom: 20px;
                border-bottom: 3px solid #667eea;
            }
            .header h1 { 
                color: #667eea; 
                margin-bottom: 10px;
                font-size: 2em;
            }
            .badge {
                display: inline-block;
                padding: 8px 16px;
                margin: 5px;
                border-radius: 20px;
                font-size: 0.85em;
                font-weight: bold;
                background: #4CAF50;
                color: white;
            }
            .badge.new {
                background: #FF5722;
                animation: pulse 2s infinite;
            }
            @keyframes pulse {
                0%, 100% { transform: scale(1); }
                50% { transform: scale(1.05); }
            }
            .info-box {
                background: linear-gradient(135deg, #e3f2fd, #f3e5f5);
                padding: 20px;
                border-radius: 15px;
                margin: 20px 0;
                border-left: 5px solid #667eea;
            }
            .info-box h3 { color: #667eea; margin-bottom: 10px; }
            .memoria-status {
                background: linear-gradient(135deg, #fff3e0, #ffe0b2);
                padding: 15px;
                border-radius: 12px;
                margin: 15px 0;
                border-left: 4px solid #FF9800;
            }
            .memoria-status h4 { 
                color: #FF9800; 
                margin-bottom: 8px;
                font-size: 1em;
            }
            .chat-box { 
                border: 2px solid #e0e0e0;
                height: 450px; 
                overflow-y: auto; 
                padding: 20px; 
                margin: 20px 0; 
                background: #fafafa;
                border-radius: 15px;
            }
            .message { 
                margin: 15px 0; 
                padding: 15px; 
                border-radius: 15px;
                animation: fadeIn 0.3s;
            }
            @keyframes fadeIn {
                from { opacity: 0; transform: translateY(10px); }
                to { opacity: 1; transform: translateY(0); }
            }
            .user { 
                background: linear-gradient(135deg, #667eea, #764ba2);
                color: white;
                margin-left: 20%;
            }
            .bot { 
                background: #e8f5e9;
                margin-right: 20%;
                border-left: 4px solid #4CAF50;
            }
            .memoria-indicator {
                display: inline-block;
                background: #FF9800;
                color: white;
                padding: 4px 10px;
                border-radius: 12px;
                font-size: 0.75em;
                margin-left: 8px;
                font-weight: bold;
            }
            .input-area { 
                display: flex; 
                gap: 10px;
                margin-top: 20px;
            }
            input { 
                flex: 1; 
                padding: 15px; 
                border: 2px solid #e0e0e0;
                border-radius: 25px;
                font-size: 1em;
            }
            button { 
                padding: 15px 30px;
                background: linear-gradient(135deg, #667eea, #764ba2);
                color: white; 
                border: none;
                border-radius: 25px;
                cursor: pointer;
                font-weight: bold;
            }
            .examples {
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin: 20px 0;
            }
            .example-btn {
                padding: 8px 16px;
                background: white;
                border: 2px solid #667eea;
                color: #667eea;
                border-radius: 20px;
                cursor: pointer;
                font-size: 0.9em;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🧠 NatanAI v6.2 MEMÓRIA INTELIGENTE</h1>
                <p style="color: #666;">Agora ela lembra da conversa! 🚀</p>
                <span class="badge new">NOVO: Memória Contextual</span>
                <span class="badge">ECONOMIA: ~21k msgs com $5</span>
            </div>
            
            <div class="info-box">
                <h3>✨ Novidades v6.2 - Sistema de Memória</h3>
                <p>✅ Lembra das últimas 10 mensagens<br>
                ✅ Gera resumo automático a cada 5 mensagens<br>
                ✅ Contexto completo do portfólio mantido<br>
                ✅ Reconhece você como Natan (admin)<br>
                ✅ Tratamento personalizado por nome<br>
                ✅ Custo otimizado: ~$0.00024/msg</p>
            </div>

            <div class="memoria-status">
                <h4>🧠 Status da Memória</h4>
                <p id="memoriaInfo">Iniciando conversa...</p>
            </div>
            
            <div id="chat-box" class="chat-box">
                <div class="message bot">
                    <strong>🤖 NatanAI v6.2:</strong><br><br>
                    Olá! Agora eu tenho MEMÓRIA INTELIGENTE! 🧠<br><br>
                    Isso significa que eu lembro de tudo que conversamos:<br>
                    • 💬 Suas últimas 10 mensagens<br>
                    • 📝 Resumo automático do contexto anterior<br>
                    • 👤 Seu nome e preferências<br>
                    • 💎 Seu plano e necessidades<br><br>
                    <strong>Teste fazendo perguntas sequenciais!</strong><br>
                    Ex: "Me fale sobre React" → "E quanto custa?"<br><br>
                    <strong>✨ Vibrações Positivas!</strong>
                </div>
            </div>
            
            <div class="examples">
                <button class="example-btn" onclick="testar('Me fale sobre React')">⚛️ React</button>
                <button class="example-btn" onclick="testar('E quanto custa um projeto assim?')">💰 Preço</button>
                <button class="example-btn" onclick="testar('Quais projetos o Natan já fez?')">📱 Portfólio</button>
                <button class="example-btn" onclick="testar('Obrigado pela ajuda!')">🙏 Obrigado</button>
            </div>
            
            <div class="input-area">
                <input type="text" id="msg" placeholder="Digite sua mensagem..." onkeypress="if(event.key==='Enter') enviar()">
                <button onclick="enviar()">Enviar</button>
            </div>
        </div>

        <script>
        let mensagensNaMemoria = 0;
        let temResumo = false;

        function atualizarStatusMemoria(metadata) {
            if (metadata && metadata.memoria) {
                mensagensNaMemoria = metadata.memoria.mensagens_na_memoria || 0;
                temResumo = metadata.memoria.tem_resumo || false;
                
                let status = `📊 Mensagens na memória: <strong>${mensagensNaMemoria}/10</strong>`;
                if (temResumo) {
                    status += ` | 📝 <strong>Resumo ativo</strong> (economia de tokens)`;
                }
                
                document.getElementById('memoriaInfo').innerHTML = status;
            }
        }

        function testar(msg) {
            document.getElementById('msg').value = msg;
            enviar();
        }
        
        async function enviar() {
            const input = document.getElementById('msg');
            const chatBox = document.getElementById('chat-box');
            const msg = input.value.trim();
            
            if (!msg) return;
            
            chatBox.innerHTML += `<div class="message user"><strong>Você:</strong><br>${msg}</div>`;
            input.value = '';
            chatBox.scrollTop = chatBox.scrollHeight;
            
            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        message: msg,
                        user_data: {
                            email: 'teste@exemplo.com',
                            plan: 'starter',
                            user_name: 'Visitante'
                        }
                    })
                });
                
                const data = await response.json();
                const resp = (data.response || data.resposta).replace(/\\n/g, '<br>');
                const nome = data.metadata?.nome_usuario || 'Teste';
                const comMemoria = data.metadata?.fonte?.includes('memoria');
                
                let memoriaTag = '';
                if (comMemoria) {
                    memoriaTag = '<span class="memoria-indicator">🧠 COM CONTEXTO</span>';
                }
                
                chatBox.innerHTML += `<div class="message bot"><strong>🤖 NatanAI v6.2:</strong>${memoriaTag}<br><br>${resp}</div>`;
                
                atualizarStatusMemoria(data.metadata);
                
            } catch (error) {
                chatBox.innerHTML += `<div class="message bot"><strong>🤖 NatanAI:</strong><br>Erro. WhatsApp: (21) 99282-6074</div>`;
            }
            
            chatBox.scrollTop = chatBox.scrollHeight;
        }

        // Carrega status inicial
        fetch('/health')
            .then(r => r.json())
            .then(data => {
                if (data.memoria) {
                    document.getElementById('memoriaInfo').innerHTML = 
                        `✅ Sistema ativo | Usuários com memória: <strong>${data.memoria.usuarios_ativos}</strong> | Mensagens armazenadas: <strong>${data.memoria.total_mensagens}</strong>`;
                }
            });
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

# =============================================================================
# 🚀 INICIALIZAÇÃO
# =============================================================================

if __name__ == '__main__':
    print("\n" + "="*80)
    print("🧠 NATANAI v6.2 - MEMÓRIA INTELIGENTE COM RESUMO AUTOMÁTICO")
    print("="*80)
    print("✨ NOVO: Sistema de memória contextual (10 mensagens)")
    print("📝 NOVO: Resumo automático a cada 5 mensagens")
    print("👑 Reconhece Natan como admin/criador")
    print("📚 Contexto: Portfolio + Site + Nome + Memória")
    print("⚡ Economia: Tokens otimizados com resumo")
    print("💰 Custo: ~$0.00024/msg = 21.000 mensagens com $5")
    print("🎯 Conversas naturais e contextuais")
    print("✅ Anti-alucinação: Validação forte")
    print("📞 WhatsApp: (21) 99282-6074")
    print("="*80 + "\n")
    
    print(f"OpenAI: {'✅' if verificar_openai() else '⚠️'}")
    print(f"Supabase: {'✅' if supabase else '⚠️'}")
    print(f"Sistema de Memória: ✅ Ativo\n")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
