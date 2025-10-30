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
MEMORIA_USUARIOS = {}
memoria_lock = threading.Lock()
MAX_MENSAGENS_MEMORIA = 10
INTERVALO_RESUMO = 5

# Auto-ping
def auto_ping():
    while True:
        try:
            if RENDER_URL:
                url = RENDER_URL if RENDER_URL.startswith('http') else f"https://{RENDER_URL}"
                requests.get(f"{url}/health", timeout=10)
                print(f"🏓 Ping OK: {datetime.now().strftime('%H:%M:%S')}")
            else:
                requests.get("https://natanai-dev.onrender.com/health", timeout=5)
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
        
        if user_data and user_data.get('name'):
            nome = user_data['name'].strip()
            if nome and len(nome) > 1:
                return nome
        
        if user_info and user_info.user_metadata:
            nome = user_info.user_metadata.get('name', '').strip()
            if nome and len(nome) > 1:
                return nome
        
        if user_info and user_info.email:
            nome = user_info.email.split('@')[0].strip()
            return nome.capitalize()
        
        if user_data and user_data.get('email'):
            nome = user_data['email'].split('@')[0].strip()
            return nome.capitalize()
        
        return "Cliente"
        
    except Exception as e:
        print(f"⚠️ Erro ao extrair nome: {e}")
        return "Cliente"

def determinar_tipo_usuario(user_data, user_info=None):
    try:
        email = user_data.get('email', '').lower().strip()
        plan = str(user_data.get('plan', 'starter')).lower().strip()
        plan_type = str(user_data.get('plan_type', 'paid')).lower().strip()
        nome = extrair_nome_usuario(user_info, user_data)
        
        print(f"🔍 DEBUG determinar_tipo_usuario:")
        print(f"   Email: {email}")
        print(f"   Plan: {plan}")
        print(f"   Plan Type: {plan_type}")
        print(f"   Nome: {nome}")
        
        # ✅ ADMIN - Sempre retorna 'admin'
        if email == ADMIN_EMAIL.lower():
            resultado = {
                'tipo': 'admin',
                'nome_display': 'Admin',
                'plano': 'Admin',
                'nome_real': 'Natan'
            }
            print(f"   ✅ Resultado: ADMIN")
            return resultado
        
        # ✅ FREE ACCESS - Sempre retorna 'free' (minúsculo)
        if plan_type == 'free':
            resultado = {
                'tipo': 'free',
                'nome_display': 'Free Access',
                'plano': 'Free (7 dias)',
                'nome_real': nome
            }
            print(f"   ✅ Resultado: FREE ACCESS")
            return resultado
        
        # ✅ PROFESSIONAL - Sempre retorna 'professional'
        if plan == 'professional':
            resultado = {
                'tipo': 'professional',
                'nome_display': 'Professional',
                'plano': 'Professional',
                'nome_real': nome
            }
            print(f"   ✅ Resultado: PROFESSIONAL")
            return resultado
        
        # ✅ STARTER - Sempre retorna 'starter'
        resultado = {
            'tipo': 'starter',
            'nome_display': 'Starter',
            'plano': 'Starter',
            'nome_real': nome
        }
        print(f"   ✅ Resultado: STARTER")
        return resultado
        
    except Exception as e:
        print(f"⚠️ Erro em determinar_tipo_usuario: {e}")
        import traceback
        traceback.print_exc()
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
    if user_info and hasattr(user_info, 'id'):
        return user_info.id
    if user_data and user_data.get('user_id'):
        return user_data['user_id']
    if user_data and user_data.get('email'):
        return hashlib.md5(user_data['email'].encode()).hexdigest()
    return 'anonimo'

def inicializar_memoria_usuario(user_id):
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
        
        if len(memoria['mensagens']) > MAX_MENSAGENS_MEMORIA:
            memoria['mensagens'] = memoria['mensagens'][-MAX_MENSAGENS_MEMORIA:]
        
        print(f"💬 Memória atualizada: {user_id[:8]}... ({len(memoria['mensagens'])} msgs)")

def gerar_resumo_conversa(mensagens):
    if not client or not mensagens or len(mensagens) < 3:
        return ""
    
    try:
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
            max_tokens=80,
            temperature=0.3
        )
        
        resumo = response.choices[0].message.content.strip()
        print(f"📝 Resumo gerado: {resumo[:50]}...")
        return resumo
        
    except Exception as e:
        print(f"⚠️ Erro ao gerar resumo: {e}")
        return ""

def obter_contexto_memoria(user_id):
    with memoria_lock:
        if user_id not in MEMORIA_USUARIOS:
            return []
        
        memoria = MEMORIA_USUARIOS[user_id]
        mensagens = memoria['mensagens']
        
        if not mensagens:
            return []
        
        if len(mensagens) <= 5:
            return [{'role': m['role'], 'content': m['content']} for m in mensagens]
        
        if memoria['contador_mensagens'] % INTERVALO_RESUMO == 0 and not memoria['resumo']:
            msgs_antigas = mensagens[:-3]
            if msgs_antigas:
                memoria['resumo'] = gerar_resumo_conversa(msgs_antigas)
        
        contexto = []
        
        if memoria['resumo']:
            contexto.append({
                'role': 'system',
                'content': f"Contexto anterior: {memoria['resumo']}"
            })
        
        mensagens_recentes = mensagens[-3:]
        for m in mensagens_recentes:
            contexto.append({
                'role': m['role'],
                'content': m['content']
            })
        
        print(f"🧠 Contexto montado: resumo={bool(memoria['resumo'])}, msgs_recentes={len(mensagens_recentes)}")
        return contexto

def limpar_memoria_antiga():
    with memoria_lock:
        agora = datetime.now()
        usuarios_remover = []
        
        for user_id, memoria in MEMORIA_USUARIOS.items():
            ultima_atualizacao = datetime.fromisoformat(memoria['ultima_atualizacao'])
            diferenca = (agora - ultima_atualizacao).total_seconds()
            
            if diferenca > 3600:
                usuarios_remover.append(user_id)
        
        for user_id in usuarios_remover:
            del MEMORIA_USUARIOS[user_id]
            print(f"🗑️ Memória limpa: {user_id[:8]}... (inativo)")

def thread_limpeza_memoria():
    while True:
        time.sleep(1800)
        limpar_memoria_antiga()

threading.Thread(target=thread_limpeza_memoria, daemon=True).start()

# =============================================================================
# 🛡️ VALIDAÇÃO ANTI-ALUCINAÇÃO (RELAXADA PARA FREE)
# =============================================================================

PALAVRAS_PROIBIDAS = [
    "garantimos primeiro lugar", "100% de conversão", "sucesso garantido",
    "site pronto em 1 hora", "empresa com 10 anos"
]

PADROES_SUSPEITOS = [
    r'garantimos?\s+\d+%',
    r'\d+\s+anos\s+de\s+experiência',
    r'certificação\s+ISO'
]

def validar_resposta(resposta, tipo_usuario='starter'):
    """
    Validação RELAXADA para Free Access
    """
    tipo = tipo_usuario.lower().strip()
    
    # ✅ FREE ACCESS: Validação super relaxada
    if tipo == 'free':
        print(f"🎁 Free Access: Validação relaxada aplicada")
        # Apenas bloqueia promessas absurdas
        resp_lower = resposta.lower()
        if "garantimos 100%" in resp_lower or "sucesso garantido" in resp_lower:
            return False, ["Promessa não realista"]
        return True, []
    
    # ✅ ADMIN: Sem validação
    if tipo == 'admin':
        return True, []
    
    # ✅ PAGOS: Validação normal
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
# 🤖 OPENAI - v6.4 ULTRA CORRIGIDO
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
    if not client or not verificar_openai():
        print("❌ OpenAI não disponível")
        return None
    
    try:
        nome_usuario = tipo_usuario.get('nome_real', 'Cliente')
        tipo = str(tipo_usuario.get('tipo', 'starter')).lower().strip()  # ✅ FORÇA MINÚSCULO + STRIP
        plano = tipo_usuario.get('plano', 'Starter')
        
        print(f"\n{'='*80}")
        print(f"🔍 DEBUG PROCESSAR_OPENAI:")
        print(f"   Tipo recebido: '{tipo}' (type: {type(tipo)})")
        print(f"   Nome: '{nome_usuario}'")
        print(f"   Plano: '{plano}'")
        print(f"   Pergunta: '{pergunta[:50]}...'")
        print(f"{'='*80}\n")
        
        # ✅ MONTA CONTEXTO BASEADO NO TIPO (comparações em minúsculo)
        if tipo == 'admin':
            ctx = f"🔴 ADMIN (Natan): Você está falando com o CRIADOR da NatanSites. Acesso total. Respostas técnicas e dados internos. Trate como seu criador e chefe. Seja pessoal e direto."
        elif tipo == 'free':
            ctx = f"🎁 FREE ACCESS ({nome_usuario}): Acesso grátis por 7 dias. IMPORTANTE: Este usuário NÃO pode pedir criação de sites (isso não está incluído no plano free). Contato APENAS WhatsApp (21) 99282-6074. Se pedir site, explique educadamente que não está disponível no Free e que pode contratar via WhatsApp. Explique limitações com gentileza."
        elif tipo == 'professional':
            ctx = f"💎 PROFESSIONAL ({nome_usuario}): Cliente premium com plano Professional. Suporte prioritário, recursos avançados disponíveis. Explique benefícios do plano dele. Seja atencioso e destaque vantagens."
        else:  # starter
            ctx = f"🌱 STARTER ({nome_usuario}): Cliente com plano Starter. Seja acolhedor e pessoal. Se relevante, sugira upgrade para Professional. Foque em ajudar com o que ele tem disponível."
        
        print(f"✅ Contexto montado para tipo '{tipo}'")
        
        # ✅ INSTRUÇÕES SOBRE INFORMAÇÕES PESSOAIS
        info_pessoal = f"""
📋 INFORMAÇÕES DO USUÁRIO:
- Nome: {nome_usuario}
- Plano: {plano}
- Tipo de acesso: {tipo.upper()}

⚠️ COMO RESPONDER PERGUNTAS PESSOAIS:
- Se perguntar "qual meu nome?": Responda "Seu nome é {nome_usuario}"
- Se perguntar "qual meu plano?": Responda "Você tem o plano {plano}"
- Se perguntar sobre seu acesso: Explique o plano "{plano}" dele
- Seja natural e use o nome dele quando apropriado (mas não em excesso)
"""
        
        prompt_sistema = f"""Você é NatanAI, assistente virtual da NatanSites.

{ctx}

{info_pessoal}

📋 DADOS OFICIAIS DA NATANSITES:
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

🎁 FREE ACCESS - R$0,00 (7 DIAS)
- Acesso GRATUITO por 7 dias à plataforma
- Dashboard, IA, Suporte LIBERADOS
- ❌ NÃO inclui criação de sites
- Contato: APENAS WhatsApp (21) 99282-6074
- Após 7 dias, acesso expira automaticamente

🌐 PLATAFORMA: Dashboard, chat suporte, NatanAI, tema dark, estatísticas

⚡ REGRAS IMPORTANTES:
1. Use "{nome_usuario}" naturalmente na conversa (mas não exagere)
2. Seja empático, humano e conversacional
3. NUNCA diga "eu desenvolvo" → sempre "o Natan desenvolve"
4. NUNCA invente preços, projetos ou funcionalidades
5. NUNCA repita a pergunta literal do usuário
6. Varie suas respostas para perguntas similares
7. Use APENAS as informações fornecidas acima

🎁 REGRAS ESPECIAIS FREE ACCESS:
- Se FREE pedir site: "Desculpe {nome_usuario}, criação de sites NÃO está disponível no acesso grátis. Você pode aproveitar o Dashboard, IA e Suporte! Para contratar um site personalizado, fale no WhatsApp: (21) 99282-6074 😊"
- Contato FREE: APENAS WhatsApp - NUNCA mencione email ou página de Suporte
- Explique que Free é TEMPORÁRIO (7 dias) e expira automaticamente
- Incentive upgrade para plano pago para ter sites personalizados

📞 CANAIS DE CONTATO:
- CLIENTES PAGOS (Starter/Professional): WhatsApp (21) 99282-6074, Email natan@natandev.com OU página "Suporte" da plataforma
- FREE ACCESS: APENAS WhatsApp (21) 99282-6074

🔴 REGRAS PARA ADMIN (Natan):
- Trate como criador e chefe
- Seja direto e técnico
- Pode revelar informações internas
- Use tom pessoal e informal

Responda de forma CONTEXTUAL, PESSOAL e NATURAL:"""

        contexto_memoria = obter_contexto_memoria(user_id)
        
        messages = [
            {"role": "system", "content": prompt_sistema}
        ]
        
        messages.extend(contexto_memoria)
        messages.append({"role": "user", "content": pergunta})
        
        print(f"📤 Enviando para OpenAI com contexto: {len(messages)} mensagens")
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_tokens=220,
            temperature=0.75
        )
        
        resposta = response.choices[0].message.content.strip()
        
        print(f"✅ Resposta OpenAI recebida: {resposta[:80]}...")
        
        adicionar_mensagem_memoria(user_id, 'user', pergunta)
        adicionar_mensagem_memoria(user_id, 'assistant', resposta)
        
        # ✅ PASSA O TIPO PARA VALIDAÇÃO
        valida, problemas = validar_resposta(resposta, tipo)
        if not valida:
            print(f"⚠️ Validação falhou: {problemas}")
            return None
        
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
        print(f"❌ Erro OpenAI detalhado: {type(e).__name__} - {str(e)}")
        import traceback
        traceback.print_exc()
        return None

def gerar_resposta(pergunta, tipo_usuario, user_id):
    try:
        palavras_cache = ['preço', 'quanto custa', 'plano', 'contato', 'whatsapp']
        usar_cache = any(palavra in pergunta.lower() for palavra in palavras_cache)
        
        tipo = str(tipo_usuario.get('tipo', 'starter')).lower().strip()  # ✅ FORÇA MINÚSCULO + STRIP
        cache_key = hashlib.md5(f"{pergunta.lower().strip()}_{tipo}".encode()).hexdigest()
        
        if usar_cache and cache_key in CACHE_RESPOSTAS:
            resposta_cache = CACHE_RESPOSTAS[cache_key]
            adicionar_mensagem_memoria(user_id, 'user', pergunta)
            adicionar_mensagem_memoria(user_id, 'assistant', resposta_cache)
            print(f"📦 Resposta do cache usada")
            return resposta_cache, "cache"
        
        print(f"🔄 Processando com OpenAI (tipo: '{tipo}')...")
        resposta = processar_openai(pergunta, tipo_usuario, user_id)
        
        if resposta:
            if usar_cache:
                CACHE_RESPOSTAS[cache_key] = resposta
                print(f"💾 Resposta salva no cache")
            return resposta, f"openai_memoria_{tipo}"
        
        print(f"⚠️ OpenAI retornou None, usando fallback")
        nome = tipo_usuario.get('nome_real', 'Cliente')
        return f"Desculpa {nome}, estou com dificuldades técnicas no momento. 😅\n\nPor favor, fale diretamente com o Natan no WhatsApp: (21) 99282-6074", "fallback"
        
    except Exception as e:
        print(f"❌ Erro gerar_resposta: {e}")
        import traceback
        traceback.print_exc()
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
        "sistema": "NatanAI v6.4 FREE ACCESS - ULTRA CORRIGIDO",
        "openai": verificar_openai(),
        "supabase": supabase is not None,
        "memoria": {
            "usuarios_ativos": usuarios_ativos,
            "total_mensagens": total_mensagens,
            "max_por_usuario": MAX_MENSAGENS_MEMORIA
        },
        "features": ["memoria_inteligente", "resumo_automatico", "contexto_completo", "free_access_100%", "validacao_relaxada"],
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
        
        auth_header = request.headers.get('Authorization', '')
        user_data_req = data.get('user_data', {})
        
        print(f"\n{'='*80}")
        print(f"📥 REQUISIÇÃO RECEBIDA:")
        print(f"   Mensagem: {mensagem[:50]}...")
        print(f"   User Data: {user_data_req}")
        print(f"{'='*80}\n")
        
        tipo_usuario = None
        user_info = None
        
        # ✅ AUTENTICAÇÃO VIA TOKEN
        if auth_header:
            user_info = verificar_token_supabase(auth_header)
            if user_info:
                dados = obter_dados_usuario_completos(user_info.id)
                user_full = {
                    'email': user_info.email,
                    'user_id': user_info.id,
                    'plan': user_info.user_metadata.get('plan', 'starter') if user_info.user_metadata else 'starter',
                    'plan_type': 'paid'
                }
                if dados:
                    user_full.update(dados)
                    if dados.get('plan_type'):
                        user_full['plan_type'] = dados['plan_type']
                tipo_usuario = determinar_tipo_usuario(user_full, user_info)
                print(f"✅ Autenticado via token: {tipo_usuario}")
        
        # ✅ FALLBACK PARA USER_DATA
        if not tipo_usuario:
            if user_data_req:
                tipo_usuario = determinar_tipo_usuario(user_data_req)
                print(f"✅ Usando user_data: {tipo_usuario}")
            else:
                tipo_usuario = {
                    'tipo': 'starter',
                    'nome_display': 'Cliente',
                    'plano': 'Starter',
                    'nome_real': 'Cliente'
                }
                print(f"⚠️ Usando fallback padrão")
        
        user_id = obter_user_id(user_info, user_data_req if user_data_req else {'email': tipo_usuario.get('nome_real', 'anonimo')})
        
        inicializar_memoria_usuario(user_id)
        
        nome_usuario = tipo_usuario.get('nome_real', 'Cliente')
        tipo_str = tipo_usuario.get('tipo', 'starter')
        
        print(f"\n{'='*80}")
        print(f"💬 [{datetime.now().strftime('%H:%M:%S')}] {nome_usuario} ({tipo_usuario['nome_display']}) - TIPO: '{tipo_str}'")
        print(f"📝 Mensagem: {mensagem[:100]}...")
        print(f"{'='*80}\n")
        
        resposta, fonte = gerar_resposta(mensagem, tipo_usuario, user_id)
        valida, _ = validar_resposta(resposta, tipo_str)
        
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
        
        with memoria_lock:
            memoria_info = {
                "mensagens_na_memoria": len(MEMORIA_USUARIOS.get(user_id, {}).get('mensagens', [])),
                "tem_resumo": bool(MEMORIA_USUARIOS.get(user_id, {}).get('resumo', ''))
            }
        
        print(f"✅ Resposta enviada - Fonte: {fonte} | Validação: {valida}")
        
        return jsonify({
            "response": resposta,
            "resposta": resposta,
            "metadata": {
                "fonte": fonte,
                "sistema": "NatanAI v6.4 FREE ACCESS - ULTRA CORRIGIDO",
                "tipo_usuario": tipo_usuario['tipo'],
                "plano": tipo_usuario['plano'],
                "nome_usuario": nome_usuario,
                "validacao": valida,
                "autenticado": user_info is not None,
                "memoria": memoria_info,
                "is_free_access": tipo_usuario['tipo'] == 'free',
                "validacao_anti_alucinacao": valida
            }
        })
        
    except Exception as e:
        print(f"❌ Erro no endpoint /chat: {e}")
        import traceback
        traceback.print_exc()
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
            "sistema": "NatanAI v6.4 FREE ACCESS - ULTRA CORRIGIDO - ~21k msgs com $5"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/limpar_memoria/<user_id>', methods=['POST'])
def limpar_memoria_usuario(user_id):
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
        "version": "v6.4-ultra-fixed"
    })

@app.route('/', methods=['GET'])
def home():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>NatanAI v6.4 FREE ACCESS - ULTRA CORRIGIDO</title>
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
            .badge.ultra {
                background: #10B981;
                animation: pulse 2s infinite;
            }
            @keyframes pulse {
                0%, 100% { transform: scale(1); }
                50% { transform: scale(1.05); }
            }
            .fix-box {
                background: linear-gradient(135deg, #d1fae5, #a7f3d0);
                padding: 20px;
                border-radius: 15px;
                margin: 20px 0;
                border-left: 5px solid #10B981;
            }
            .fix-box h3 { color: #10B981; margin-bottom: 10px; }
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
            .select-plan {
                margin: 20px 0;
                padding: 15px;
                background: #f8f9fa;
                border-radius: 10px;
            }
            .select-plan select {
                width: 100%;
                padding: 10px;
                border-radius: 8px;
                border: 2px solid #667eea;
                font-size: 1em;
                margin-top: 10px;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🧠 NatanAI v6.4 - ULTRA CORRIGIDO ✅</h1>
                <p style="color: #666;">Free Access 100% funcional!</p>
                <span class="badge ultra">✅ ULTRA FIX</span>
                <span class="badge">FREE 100%</span>
            </div>
            
            <div class="fix-box">
                <h3>🐛 Correções v6.4:</h3>
                <p>✅ <strong>Normalização total</strong> - Todos os tipos forçados para minúsculo + .strip()<br>
                ✅ <strong>Validação relaxada para FREE</strong> - Apenas bloqueia promessas absurdas<br>
                ✅ <strong>Debug ultra detalhado</strong> - Logs em todas as etapas<br>
                ✅ <strong>Extração de nome melhorada</strong> - Suporta user_name e name<br>
                ✅ <strong>Tipo passado para validação</strong> - Validação contextual<br>
                ✅ <strong>Tratamento de erro robusto</strong> - Try-catch em tudo<br>
                ✅ <strong>Metadata completo</strong> - validacao_anti_alucinacao adicionado</p>
            </div>

            <div class="select-plan">
                <strong>🎭 Testar como:</strong>
                <select id="planType" onchange="atualizarPlano()">
                    <option value="free">🎁 Free Access (7 dias grátis)</option>
                    <option value="admin">👑 Admin (Natan - Criador)</option>
                    <option value="starter">🌱 Starter (Plano básico)</option>
                    <option value="professional">💎 Professional (Plano premium)</option>
                </select>
                <p id="planInfo" style="margin-top: 10px; color: #666;"></p>
            </div>
            
            <div id="chat-box" class="chat-box">
                <div class="message bot">
                    <strong>🤖 NatanAI v6.4 ULTRA CORRIGIDO:</strong><br><br>
                    Todas as correções aplicadas! ✅<br><br>
                    <strong>✨ O que mudou:</strong><br>
                    • Normalização total (minúsculo + strip)<br>
                    • Validação relaxada para FREE<br>
                    • Debug ultra detalhado<br>
                    • Extração de nome melhorada<br><br>
                    <strong>Teste agora e veja funcionando!</strong>
                </div>
            </div>
            
            <div class="input-area">
                <input type="text" id="msg" placeholder="Digite sua mensagem..." onkeypress="if(event.key==='Enter') enviar()">
                <button onclick="enviar()">Enviar</button>
            </div>
        </div>

        <script>
        let planAtual = 'free';

        const planConfigs = {
            free: {
                plan: 'free',
                plan_type: 'free',
                user_name: 'Visitante Free',
                name: 'Visitante Free',
                email: 'free@teste.com',
                info: '🎁 Testando como usuário FREE ACCESS (7 dias grátis)'
            },
            admin: {
                plan: 'admin',
                plan_type: 'paid',
                user_name: 'Natan',
                name: 'Natan',
                email: 'natan@natandev.com',
                info: '👑 Testando como ADMIN (Natan - Criador)'
            },
            starter: {
                plan: 'starter',
                plan_type: 'paid',
                user_name: 'Cliente Starter',
                name: 'Cliente Starter',
                email: 'starter@teste.com',
                info: '🌱 Testando como cliente STARTER (plano básico)'
            },
            professional: {
                plan: 'professional',
                plan_type: 'paid',
                user_name: 'Cliente Pro',
                name: 'Cliente Pro',
                email: 'pro@teste.com',
                info: '💎 Testando como cliente PROFESSIONAL (plano premium)'
            }
        };

        function atualizarPlano() {
            planAtual = document.getElementById('planType').value;
            document.getElementById('planInfo').textContent = planConfigs[planAtual].info;
            document.getElementById('chat-box').innerHTML = `
                <div class="message bot">
                    <strong>🤖 NatanAI:</strong><br><br>
                    ${planConfigs[planAtual].info}<br><br>
                    Agora você pode testar as funcionalidades deste plano! 😊
                </div>
            `;
        }

        atualizarPlano();
        
        async function enviar() {
            const input = document.getElementById('msg');
            const chatBox = document.getElementById('chat-box');
            const msg = input.value.trim();
            
            if (!msg) return;
            
            chatBox.innerHTML += `<div class="message user"><strong>Você:</strong><br>${msg}</div>`;
            input.value = '';
            chatBox.scrollTop = chatBox.scrollHeight;
            
            try {
                const config = planConfigs[planAtual];
                
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        message: msg,
                        user_data: config
                    })
                });
                
                const data = await response.json();
                const resp = (data.response || data.resposta).replace(/\\n/g, '<br>');
                
                chatBox.innerHTML += `<div class="message bot"><strong>🤖 NatanAI v6.4:</strong><br><br>${resp}</div>`;
                
                console.log('✅ Metadata:', data.metadata);
                
            } catch (error) {
                chatBox.innerHTML += `<div class="message bot"><strong>🤖 NatanAI:</strong><br>Erro: ${error.message}</div>`;
                console.error('❌ Erro:', error);
            }
            
            chatBox.scrollTop = chatBox.scrollHeight;
        }
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

if __name__ == '__main__':
    print("\n" + "="*80)
    print("🧠 NATANAI v6.4 - FREE ACCESS - ✅ ULTRA CORRIGIDO")
    print("="*80)
    print("🐛 ULTRA FIXES:")
    print("   ✅ Normalização total: .lower() + .strip() em tudo")
    print("   ✅ Validação relaxada para FREE (apenas promessas absurdas)")
    print("   ✅ Debug ultra detalhado em todas as etapas")
    print("   ✅ Extração de nome com fallbacks (user_name, name, email)")
    print("   ✅ Tipo passado para validação (validação contextual)")
    print("   ✅ Try-catch em todas as funções críticas")
    print("   ✅ Metadata completo com validacao_anti_alucinacao")
    print("")
    print("🎁 Free Access: 100% funcional")
    print("👑 Admin: Reconhece Natan como criador")
    print("💎 Professional/Starter: Funcionando perfeitamente")
    print("✨ Sistema de memória contextual (10 mensagens)")
    print("📝 Resumo automático a cada 5 mensagens")
    print("💰 Custo: ~$0.00024/msg = 21.000 mensagens com $5")
    print("="*80 + "\n")
    
    print(f"OpenAI: {'✅' if verificar_openai() else '⚠️'}")
    print(f"Supabase: {'✅' if supabase else '⚠️'}")
    print(f"Sistema de Memória: ✅ Ativo")
    print(f"Ultra Fixes: ✅ Aplicados\n")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
