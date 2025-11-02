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
RENDER_URL = os.getenv("RENDER_URL", "")

# ============================================
# 🆕 SISTEMA DE MODELOS POR PLANO v8.0
# ============================================
MODELOS_POR_PLANO = {
    'free': 'gpt-4o-mini',           # 🎁 Modelo econômico para teste
    'starter': 'gpt-4o-mini',        # 🌱 Modelo econômico otimizado
    'professional': 'gpt-4o-mini',   # 💎 Modelo de alta qualidade
    'admin': 'gpt-4o-mini'           # 👑 Modelo premium + recursos extras
}

# ============================================
# 📊 LIMITES DE MENSAGENS POR PLANO
# ============================================
LIMITES_MENSAGENS = {
    'free': 100,          # 🎁 100 mensagens/semana
    'starter': 1250,      # 🌱 1.250 mensagens/mês
    'professional': 5000, # 💎 5.000 mensagens/mês
    'admin': float('inf') # 👑 Ilimitado
}

# ============================================
# 🎯 SISTEMA DE OTIMIZAÇÃO DE TOKENS v8.0
# ============================================
CATEGORIAS_MENSAGEM = {
    'saudacao': {
        'keywords': ['oi', 'olá', 'ola', 'hey', 'bom dia', 'boa tarde', 'boa noite', 'e ai', 'eai', 'oie'],
        'max_tokens': 80,
        'instrucao': 'Resposta curta e amigável (máx 2-3 frases)'
    },
    'despedida': {
        'keywords': ['tchau', 'até', 'falou', 'obrigado', 'obrigada', 'valeu', 'agradeço', 'até mais', 'ate logo'],
        'max_tokens': 60,
        'instrucao': 'Despedida curta e cordial (máx 1-2 frases)'
    },
    'casual': {
        'keywords': ['legal', 'show', 'top', 'massa', 'dahora', 'haha', 'kkk', 'rsrs', 'beleza', 'tranquilo', 'entendi'],
        'max_tokens': 80,
        'instrucao': 'Resposta curta e natural (máx 2-3 frases)'
    },
    'confirmacao': {
        'keywords': ['sim', 'não', 'nao', 'ok', 'certo', 'pode ser', 'tudo bem', 'entendo', 'compreendo'],
        'max_tokens': 60,
        'instrucao': 'Confirmação breve e clara (máx 1-2 frases)'
    },
    'explicacao_simples': {
        'keywords': ['o que é', 'como funciona', 'me explica', 'qual', 'quanto', 'quando', 'onde', 'quem'],
        'max_tokens': 200,
        'instrucao': 'Explicação clara e direta (máx 4-5 frases curtas)'
    },
    'planos_valores': {
        'keywords': ['plano', 'preço', 'valor', 'custo', 'quanto custa', 'mensalidade', 'pagar', 'contratar'],
        'max_tokens': 250,
        'instrucao': 'Informações objetivas sobre planos e valores (máx 5-6 frases)'
    },
    'tecnico': {
        'keywords': ['como criar', 'como fazer', 'passo a passo', 'tutorial', 'ensina', 'ajuda com'],
        'max_tokens': 300,
        'instrucao': 'Explicação técnica mas simplificada (máx 6-7 frases)'
    },
    'complexo': {
        'keywords': ['detalhes', 'completo', 'tudo sobre', 'me fala sobre', 'quero saber'],
        'max_tokens': 400,
        'instrucao': 'Resposta completa mas organizada (máx 8-10 frases)'
    }
}

def detectar_categoria_mensagem(mensagem):
    """Detecta categoria da mensagem para otimizar tokens"""
    msg_lower = mensagem.lower().strip()
    
    # Mensagens muito curtas são casuais
    if len(msg_lower.split()) <= 3:
        for categoria, config in CATEGORIAS_MENSAGEM.items():
            if any(kw in msg_lower for kw in config['keywords']):
                return categoria, config
        return 'casual', CATEGORIAS_MENSAGEM['casual']
    
    # Verifica categorias por ordem de prioridade
    ordem_prioridade = ['saudacao', 'despedida', 'confirmacao', 'casual', 
                        'planos_valores', 'explicacao_simples', 'tecnico', 'complexo']
    
    for cat in ordem_prioridade:
        config = CATEGORIAS_MENSAGEM[cat]
        if any(kw in msg_lower for kw in config['keywords']):
            return cat, config
    
    # Padrão: explicação simples
    return 'explicacao_simples', CATEGORIAS_MENSAGEM['explicacao_simples']

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

# 📊 CONTADOR DE MENSAGENS POR USUÁRIO
CONTADOR_MENSAGENS = {}
contador_lock = threading.Lock()

# 📊 CONTADOR DE TOKENS POR USUÁRIO
CONTADOR_TOKENS = {}
tokens_lock = threading.Lock()

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
# 📊 SISTEMA DE CONTROLE DE MENSAGENS
# =============================================================================

def obter_contador_mensagens(user_id):
    """Retorna o contador de mensagens do usuário"""
    with contador_lock:
        if user_id not in CONTADOR_MENSAGENS:
            CONTADOR_MENSAGENS[user_id] = {
                'total': 0,
                'resetado_em': datetime.now().isoformat(),
                'tipo_plano': 'starter'
            }
        return CONTADOR_MENSAGENS[user_id]

def incrementar_contador(user_id, tipo_plano):
    """Incrementa o contador de mensagens do usuário"""
    with contador_lock:
        if user_id not in CONTADOR_MENSAGENS:
            CONTADOR_MENSAGENS[user_id] = {
                'total': 0,
                'resetado_em': datetime.now().isoformat(),
                'tipo_plano': tipo_plano
            }
        
        CONTADOR_MENSAGENS[user_id]['total'] += 1
        CONTADOR_MENSAGENS[user_id]['tipo_plano'] = tipo_plano
        
        return CONTADOR_MENSAGENS[user_id]['total']

def verificar_limite_mensagens(user_id, tipo_plano):
    """
    Verifica se o usuário atingiu o limite de mensagens.
    Retorna: (pode_enviar: bool, mensagens_usadas: int, limite: int, mensagens_restantes: int)
    """
    tipo = tipo_plano.lower().strip()
    limite = LIMITES_MENSAGENS.get(tipo, LIMITES_MENSAGENS['starter'])
    
    # Admin tem ilimitado
    if tipo == 'admin':
        return True, 0, float('inf'), float('inf')
    
    contador = obter_contador_mensagens(user_id)
    mensagens_usadas = contador['total']
    mensagens_restantes = limite - mensagens_usadas
    
    pode_enviar = mensagens_usadas < limite
    
    return pode_enviar, mensagens_usadas, limite, max(0, mensagens_restantes)

def resetar_contador_usuario(user_id):
    """Reseta o contador de mensagens de um usuário"""
    with contador_lock:
        if user_id in CONTADOR_MENSAGENS:
            CONTADOR_MENSAGENS[user_id]['total'] = 0
            CONTADOR_MENSAGENS[user_id]['resetado_em'] = datetime.now().isoformat()
            print(f"🔄 Contador resetado para user: {user_id[:8]}...")
            return True
        return False

def gerar_mensagem_limite_atingido(tipo_plano, mensagens_usadas, limite):
    """Gera mensagem personalizada quando o limite é atingido"""
    tipo = tipo_plano.lower().strip()
    
    if tipo == 'free':
        return f"""Você atingiu o limite de {limite} mensagens por semana do seu teste grátis.

Para continuar conversando comigo, contrate um dos planos:

STARTER - R$320 (setup) + R$39,99/mês
- 1.250 mensagens/mês comigo
- Site profissional até 5 páginas
- Hospedagem inclusa

PROFESSIONAL - R$530 (setup) + R$79,99/mês
- 5.000 mensagens/mês comigo
- Site 100% personalizado
- Recursos avançados

Entre em contato:
WhatsApp: (21) 99282-6074
Email: borgesnatan09@gmail.com

Vibrações Positivas! ✨"""
    
    elif tipo == 'starter':
        return f"""Você atingiu o limite de {limite} mensagens do plano Starter este mês.

Opções:
1. Upgrade para Professional (5.000 msgs/mês)
2. Aguardar renovação mensal

Acesse a página Suporte para falar com Natan pessoalmente!

Vibrações Positivas! ✨"""
    
    elif tipo == 'professional':
        return f"""Você atingiu o limite de {limite} mensagens do plano Professional este mês.

Para soluções personalizadas ou aumento de limite, acesse a página Suporte para falar com Natan!

Vibrações Positivas! ✨"""
    
    return "Limite de mensagens atingido. Entre em contato com o suporte."

# =============================================================================
# 📊 SISTEMA DE CONTAGEM DE TOKENS
# =============================================================================

def registrar_tokens_usados(user_id, tokens_entrada, tokens_saida, tokens_total, modelo_usado):
    """Registra tokens usados por um usuário"""
    with tokens_lock:
        if user_id not in CONTADOR_TOKENS:
            CONTADOR_TOKENS[user_id] = {
                'total_entrada': 0,
                'total_saida': 0,
                'total_geral': 0,
                'mensagens_processadas': 0,
                'modelo': modelo_usado
            }
        
        CONTADOR_TOKENS[user_id]['total_entrada'] += tokens_entrada
        CONTADOR_TOKENS[user_id]['total_saida'] += tokens_saida
        CONTADOR_TOKENS[user_id]['total_geral'] += tokens_total
        CONTADOR_TOKENS[user_id]['mensagens_processadas'] += 1
        CONTADOR_TOKENS[user_id]['modelo'] = modelo_usado

def obter_estatisticas_tokens(user_id):
    """Retorna estatísticas de tokens de um usuário"""
    with tokens_lock:
        if user_id not in CONTADOR_TOKENS:
            return {
                'total_entrada': 0,
                'total_saida': 0,
                'total_geral': 0,
                'mensagens_processadas': 0,
                'media_por_mensagem': 0,
                'modelo': 'N/A'
            }
        
        stats = CONTADOR_TOKENS[user_id].copy()
        if stats['mensagens_processadas'] > 0:
            stats['media_por_mensagem'] = round(stats['total_geral'] / stats['mensagens_processadas'], 2)
        else:
            stats['media_por_mensagem'] = 0
        
        return stats
    
    # =============================================================================
# 🆘 SISTEMA DE RESPOSTA ALTERNATIVA (SEM IA)
# =============================================================================

def gerar_resposta_alternativa_inteligente(pergunta, tipo_usuario):
    """
    Sistema de respostas automáticas quando limite de IA acaba.
    Usa padrões e keywords para responder sem consumir API.
    """
    msg_lower = pergunta.lower().strip()
    nome = tipo_usuario.get('nome_real', 'Cliente')
    tipo = tipo_usuario.get('tipo', 'starter')
    
    # 🎯 RESPOSTAS POR CATEGORIA
    
    # SAUDAÇÕES
    if any(kw in msg_lower for kw in ['oi', 'olá', 'ola', 'hey', 'bom dia', 'boa tarde', 'boa noite', 'e ai', 'eai']):
        return f"Oi {nome}! Seus créditos de IA acabaram este mês, mas posso te ajudar com informações básicas. Como posso ajudar?"
    
    # DESPEDIDAS
    if any(kw in msg_lower for kw in ['tchau', 'até', 'falou', 'obrigado', 'obrigada', 'valeu']):
        return f"Até logo {nome}! Seus créditos de IA renovam no próximo mês. Vibrações Positivas! ✨"
    
    # PLANOS E PREÇOS
    if any(kw in msg_lower for kw in ['plano', 'preço', 'valor', 'custo', 'quanto custa', 'mensalidade', 'contratar']):
        return f"""Olá {nome}! Aqui estão nossos planos:

FREE - R$0,00 (teste 1 ano)
- 100 mensagens/semana comigo
- Sites básicos sem uso comercial

STARTER - R$320 (setup) + R$39,99/mês
- 1.250 mensagens/mês comigo
- Site até 5 páginas
- Hospedagem inclusa
- Uso comercial

PROFESSIONAL - R$530 (setup) + R$79,99/mês
- 5.000 mensagens/mês comigo
- Páginas ilimitadas
- Design personalizado
- SEO avançado

Contato:
WhatsApp: (21) 99282-6074
Site: https://natansites.com.br"""
    
    # CONTATO
    if any(kw in msg_lower for kw in ['contato', 'whatsapp', 'telefone', 'email', 'falar']):
        return f"""Fale com Natan diretamente:

WhatsApp: (21) 99282-6074
Email: borgesnatan09@gmail.com
Site: https://natansites.com.br

Atendimento pessoal para clientes!"""
    
    # PORTFÓLIO
    if any(kw in msg_lower for kw in ['portfolio', 'portfólio', 'projetos', 'trabalhos', 'sites feitos']):
        return f"""Confira alguns projetos do Natan:

1. Espaço Familiares - espacofamiliares.com.br
2. NatanSites - natansites.com.br
3. MathWork - mathworkftv.netlify.app
4. TAF Sem Tabu - tafsemtabu.com.br

E mais! Visite natansites.com.br para ver todos."""
    
    # COMO FUNCIONA
    if any(kw in msg_lower for kw in ['como funciona', 'processo', 'etapas', 'passo a passo']):
        return f"""Processo simples:

1. Escolha seu plano
2. Preencha formulário de cadastro
3. Efetue pagamento PIX
4. Aguarde 10min a 2h para criação da conta
5. Comece a usar!

WhatsApp: (21) 99282-6074"""
    
    # TECNOLOGIAS
    if any(kw in msg_lower for kw in ['tecnologia', 'stack', 'linguagem', 'framework', 'código']):
        return f"""Stack do Natan:

Front-end: HTML5, CSS3, JavaScript, React, Vue, TypeScript, Tailwind
Back-end: Node.js, Python, Express.js, APIs
Mobile: React Native
Banco: Supabase, PostgreSQL
IA: OpenAI, Claude

Especialidades: IA, SEO, Animações Web"""
    
    # SUPORTE
    if any(kw in msg_lower for kw in ['suporte', 'ajuda', 'problema', 'bug', 'erro', 'não funciona']):
        if tipo == 'free':
            return f"""Para suporte, entre em contato:
WhatsApp: (21) 99282-6074

Clientes pagos têm acesso à página Suporte com chat direto!"""
        else:
            return f"""Acesse a página SUPORTE no menu para falar diretamente com o Natan!

Você tem suporte prioritário como cliente {tipo.upper()}."""
    
    # CADASTRO
    if any(kw in msg_lower for kw in ['cadastro', 'cadastrar', 'registrar', 'criar conta', 'sign up']):
        return f"""Para se cadastrar:

1. Escolha STARTER ou PROFESSIONAL
2. Acesse a página do plano escolhido
3. Preencha: Nome, Data Nasc, CPF
4. Pague via PIX (R$320 Starter ou R$530 Pro)
5. Aguarde criação da conta (10min a 2h)

WhatsApp para dúvidas: (21) 99282-6074"""
    
    # HOSPEDAGEM/DOMÍNIO
    if any(kw in msg_lower for kw in ['hospedagem', 'domínio', 'dominio', 'hosting', 'servidor']):
        return f"""Hospedagem e Domínio:

STARTER: Hospedagem inclusa por 1 ano
PROFESSIONAL: Hospedagem + Domínio inclusos

Renovação após 1 ano é à parte.
WhatsApp: (21) 99282-6074"""
    
    # PRAZO/TEMPO
    if any(kw in msg_lower for kw in ['prazo', 'tempo', 'demora', 'quanto tempo', 'quando fica pronto']):
        return f"""Prazos:

Criação de conta: 10min a 2h após pagamento
Desenvolvimento do site: 
- Sites simples: 3 a 7 dias
- Sites complexos: 10 a 20 dias

Depende da complexidade e fila de projetos.
WhatsApp: (21) 99282-6074"""
    
    # SEO
    if any(kw in msg_lower for kw in ['seo', 'google', 'ranquear', 'primeiro lugar', 'posicionamento']):
        return f"""SEO (Otimização para Google):

STARTER: SEO básico incluso
PROFESSIONAL: SEO avançado incluso

O Natan otimiza seu site para aparecer melhor no Google!
Mas não garantimos posições específicas (ninguém pode garantir isso).

WhatsApp: (21) 99282-6074"""
    
    # PAGAMENTO
    if any(kw in msg_lower for kw in ['pagamento', 'pagar', 'pix', 'forma de pagamento', 'cartão']):
        return f"""Formas de Pagamento:

Setup (inicial): PIX
- Starter: R$320,00
- Professional: R$530,00

Mensalidade: PIX mensal
- Starter: R$39,99/mês
- Professional: R$79,99/mês

Sem cartão de crédito por enquanto.
WhatsApp: (21) 99282-6074"""
    
    # DIFERENÇA ENTRE PLANOS
    if any(kw in msg_lower for kw in ['diferença', 'diferenca', 'comparar', 'qual escolher', 'melhor plano']):
        return f"""Diferenças principais:

STARTER (R$320 + R$39,99/mês):
- Site até 5 páginas
- Design moderno padrão
- 1.250 mensagens/mês comigo
- SEO básico

PROFESSIONAL (R$530 + R$79,99/mês):
- Páginas ilimitadas
- Design 100% personalizado
- 5.000 mensagens/mês comigo
- SEO avançado
- Blog/E-commerce opcionais

Para maioria: STARTER é suficiente!
WhatsApp: (21) 99282-6074"""
    
    # RESPOSTA PADRÃO (quando não reconhece a pergunta)
    return f"""Olá {nome}!

Seus créditos de IA acabaram este mês. Para informações detalhadas:

📞 WhatsApp: (21) 99282-6074
📧 Email: borgesnatan09@gmail.com
🌐 Site: https://natansites.com.br

Posso responder sobre:
- Planos e preços
- Contato
- Portfólio
- Como funciona
- Cadastro

Seus créditos renovam no próximo mês!

Vibrações Positivas! ✨"""

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
        
        # ADMIN
        if email == ADMIN_EMAIL.lower():
            return {
                'tipo': 'admin',
                'nome_display': 'Admin',
                'plano': 'Admin',
                'nome_real': 'Natan',
                'modelo': MODELOS_POR_PLANO['admin']
            }
        
        # FREE ACCESS
        if plan_type == 'free':
            return {
                'tipo': 'free',
                'nome_display': 'Free Access',
                'plano': 'Free (teste)',
                'nome_real': nome,
                'modelo': MODELOS_POR_PLANO['free']
            }
        
        # PROFESSIONAL
        if plan == 'professional':
            return {
                'tipo': 'professional',
                'nome_display': 'Professional',
                'plano': 'Professional',
                'nome_real': nome,
                'modelo': MODELOS_POR_PLANO['professional']
            }
        
        # STARTER (padrão)
        return {
            'tipo': 'starter',
            'nome_display': 'Starter',
            'plano': 'Starter',
            'nome_real': nome,
            'modelo': MODELOS_POR_PLANO['starter']
        }
        
    except Exception as e:
        print(f"⚠️ Erro em determinar_tipo_usuario: {e}")
        return {
            'tipo': 'starter',
            'nome_display': 'Starter',
            'plano': 'Starter',
            'nome_real': 'Cliente',
            'modelo': MODELOS_POR_PLANO['starter']
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

def gerar_resumo_conversa(mensagens, modelo='gpt-4o-mini'):
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
            model=modelo,
            messages=[{"role": "user", "content": prompt_resumo}],
            max_tokens=80,
            temperature=0.3
        )
        
        resumo = response.choices[0].message.content.strip()
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

def thread_limpeza_memoria():
    while True:
        time.sleep(1800)
        limpar_memoria_antiga()

threading.Thread(target=thread_limpeza_memoria, daemon=True).start()

# =============================================================================
# 🛡️ VALIDAÇÃO ANTI-ALUCINAÇÃO
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
    """Validação RELAXADA para Free Access"""
    tipo = tipo_usuario.lower().strip()
    
    # FREE ACCESS: Validação super relaxada
    if tipo == 'free':
        resp_lower = resposta.lower()
        if "garantimos 100%" in resp_lower or "sucesso garantido" in resp_lower:
            return False, ["Promessa não realista"]
        return True, []
    
    # ADMIN: Sem validação
    if tipo == 'admin':
        return True, []
    
    # PAGOS: Validação normal
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
# ✨ LIMPEZA DE FORMATAÇÃO
# =============================================================================

def limpar_formatacao_markdown(texto):
    """Remove asteriscos e caracteres especiais de formatação"""
    if not texto:
        return texto
    
    texto = re.sub(r'\*\*([^*]+)\*\*', r'\1', texto)
    texto = re.sub(r'\*([^*]+)\*', r'\1', texto)
    texto = re.sub(r'__([^_]+)__', r'\1', texto)
    texto = re.sub(r'_([^_]+)_', r'\1', texto)
    texto = re.sub(r'`([^`]+)`', r'\1', texto)
    texto = texto.replace('´', '').replace('~', '').replace('^', '').replace('¨', '')
    texto = re.sub(r'\n{3,}', '\n\n', texto)
    
    return texto.strip()

# =============================================================================
# 🆘 SISTEMA DE RESPOSTA ALTERNATIVA (SEM IA)
# =============================================================================

def gerar_resposta_alternativa_inteligente(pergunta, tipo_usuario):
    """
    Sistema de respostas automáticas quando limite de IA acaba.
    Usa padrões e keywords para responder sem consumir API.
    """
    msg_lower = pergunta.lower().strip()
    nome = tipo_usuario.get('nome_real', 'Cliente')
    tipo = tipo_usuario.get('tipo', 'starter')
    
    # SAUDAÇÕES
    if any(kw in msg_lower for kw in ['oi', 'olá', 'ola', 'hey', 'bom dia', 'boa tarde', 'boa noite', 'e ai', 'eai']):
        return f"Oi {nome}! Seus créditos de IA acabaram este mês, mas posso te ajudar com informações básicas. Como posso ajudar?"
    
    # DESPEDIDAS
    if any(kw in msg_lower for kw in ['tchau', 'até', 'falou', 'obrigado', 'obrigada', 'valeu']):
        return f"Até logo {nome}! Seus créditos de IA renovam no próximo mês. Vibrações Positivas! ✨"
    
    # PLANOS E PREÇOS
    if any(kw in msg_lower for kw in ['plano', 'preço', 'valor', 'custo', 'quanto custa', 'mensalidade', 'contratar']):
        return f"""Olá {nome}! Aqui estão nossos planos:

FREE - R$0,00 (teste 1 ano)
- 100 mensagens/semana comigo
- Sites básicos sem uso comercial

STARTER - R$320 (setup) + R$39,99/mês
- 1.250 mensagens/mês comigo
- Site até 5 páginas
- Hospedagem inclusa

PROFESSIONAL - R$530 (setup) + R$79,99/mês
- 5.000 mensagens/mês comigo
- Páginas ilimitadas
- Design personalizado

Contato:
WhatsApp: (21) 99282-6074
Site: https://natansites.com.br"""
    
    # CONTATO
    if any(kw in msg_lower for kw in ['contato', 'whatsapp', 'telefone', 'email', 'falar']):
        return f"""Fale com Natan diretamente:

WhatsApp: (21) 99282-6074
Email: borgesnatan09@gmail.com
Site: https://natansites.com.br

Atendimento pessoal para clientes!"""
    
    # PORTFÓLIO
    if any(kw in msg_lower for kw in ['portfolio', 'portfólio', 'projetos', 'trabalhos']):
        return f"""Confira alguns projetos do Natan:

1. Espaço Familiares - espacofamiliares.com.br
2. NatanSites - natansites.com.br
3. MathWork - mathworkftv.netlify.app
4. TAF Sem Tabu - tafsemtabu.com.br

Visite natansites.com.br para ver todos!"""
    
    # RESPOSTA PADRÃO
    return f"""Olá {nome}!

Seus créditos de IA acabaram este mês. Para informações detalhadas:

📞 WhatsApp: (21) 99282-6074
📧 Email: borgesnatan09@gmail.com
🌐 Site: https://natansites.com.br

Posso responder sobre:
- Planos e preços
- Contato
- Portfólio
- Cadastro

Seus créditos renovam no próximo mês!

Vibrações Positivas! ✨"""

# =============================================================================
# 🤖 PROCESSAMENTO OPENAI v8.0 - SISTEMA HÍBRIDO DE MODELOS
# =============================================================================

def processar_mensagem_openai(mensagem, tipo_usuario, historico_memoria):
    """
    Sistema híbrido de modelos por plano:
    - FREE: gpt-3.5-turbo (básico + barato)
    - STARTER: gpt-4o-mini (casual) + gpt-4o (perguntas sérias sobre serviços)
    - PROFESSIONAL: gpt-4o (completo)
    - ADMIN: gpt-4o (completo + conhecimentos gerais + web search)
    """
    
    if not verificar_openai():
        return {
            'resposta': "⚠️ Sistema de IA temporariamente indisponível. Tente novamente em alguns instantes.",
            'tokens_usados': 0,
            'modelo_usado': 'N/A',
            'cached': False
        }
    
    try:
        tipo = tipo_usuario.get('tipo', 'starter').lower()
        nome = tipo_usuario.get('nome_real', 'Cliente')
        plano = tipo_usuario.get('plano', 'Starter')
        
        # Detecta categoria da mensagem
        categoria, config = detectar_categoria_mensagem(mensagem)
        
        # ==================================================================
        # 🎁 FREE ACCESS - GPT-3.5-TURBO (modelo mais barato)
        # ==================================================================
        if tipo == 'free':
            modelo = 'gpt-3.5-turbo'
            max_tokens = config['max_tokens']
            
            system_prompt = f"""Você é NatanAI, assistente virtual da NatanSites (natansites.com.br).

INFORMAÇÕES PARA USUÁRIOS FREE (teste gratuito de 1 ano):

**PLANOS DISPONÍVEIS:**
- FREE: R$0,00 (teste 1 ano) - 100 msgs/semana - Sites básicos sem uso comercial
- STARTER: R$320 setup + R$39,99/mês - 1.250 msgs/mês - Site até 5 páginas - Hospedagem inclusa
- PROFESSIONAL: R$530 setup + R$79,99/mês - 5.000 msgs/mês - Páginas ilimitadas - Design personalizado

**CONTATO:**
WhatsApp: (21) 99282-6074
Email: borgesnatan09@gmail.com
Site: natansites.com.br

**PORTFÓLIO:**
- Espaço Familiares - espacofamiliares.com.br
- NatanSites - natansites.com.br
- MathWork - mathworkftv.netlify.app
- TAF Sem Tabu - tafsemtabu.com.br

**TECNOLOGIAS:**
HTML5, CSS3, JavaScript, React, Vue, Node.js, Python, Supabase, IA

**COMO CONTRATAR:**
1. Escolha STARTER ou PROFESSIONAL
2. Preencha cadastro no site
3. Pague via PIX
4. Aguarde 10min a 2h para criação da conta

REGRAS:
- Seja direto e objetivo (usuário está em teste grátis)
- Incentive upgrade para STARTER ou PROFESSIONAL
- Mencione benefícios dos planos pagos
- Sempre mencione contato: WhatsApp (21) 99282-6074
- {config['instrucao']}
- Sem asteriscos ou formatação markdown
- Tom amigável mas profissional

Você está conversando com: {nome} (Plano {plano})"""

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(historico_memoria[-3:])  # Últimas 3 mensagens apenas
            messages.append({"role": "user", "content": mensagem})
            
            response = client.chat.completions.create(
                model=modelo,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7
            )
            
            resposta = response.choices[0].message.content.strip()
            resposta = limpar_formatacao_markdown(resposta)
            
            tokens_entrada = response.usage.prompt_tokens
            tokens_saida = response.usage.completion_tokens
            tokens_total = response.usage.total_tokens
            
            return {
                'resposta': resposta,
                'tokens_usados': tokens_total,
                'tokens_entrada': tokens_entrada,
                'tokens_saida': tokens_saida,
                'modelo_usado': modelo,
                'cached': False,
                'categoria': categoria
            }
        
        # ==================================================================
        # 🌱 STARTER - SISTEMA HÍBRIDO: GPT-4O-MINI + GPT-4O
        # ==================================================================
        elif tipo == 'starter':
            # Detecta se é pergunta séria sobre serviços
            msg_lower = mensagem.lower().strip()
            
            perguntas_serias = [
                'plano', 'preço', 'valor', 'custo', 'quanto custa', 'mensalidade',
                'contratar', 'cadastro', 'como funciona', 'processo', 'etapas',
                'prazo', 'tempo', 'demora', 'hospedagem', 'domínio', 'seo',
                'pagamento', 'pix', 'diferença', 'comparar', 'qual escolher',
                'portfolio', 'projetos', 'trabalhos', 'tecnologia', 'stack'
            ]
            
            is_pergunta_seria = any(kw in msg_lower for kw in perguntas_serias)
            
            # PERGUNTAS SÉRIAS: GPT-4O
            if is_pergunta_seria:
                modelo = 'gpt-4o'
                max_tokens = min(config['max_tokens'] * 2, 500)  # Dobra limite para respostas completas
                
                system_prompt = f"""Você é NatanAI, assistente especializado da NatanSites.

INFORMAÇÕES COMPLETAS PARA CLIENTES STARTER:

**PLANOS E PREÇOS:**
- STARTER (seu plano atual): R$320 setup + R$39,99/mês
  - 1.250 mensagens/mês comigo
  - Site profissional até 5 páginas
  - Hospedagem inclusa por 1 ano
  - Design moderno padrão
  - SEO básico
  - Suporte via página Suporte

- PROFESSIONAL: R$530 setup + R$79,99/mês
  - 5.000 mensagens/mês comigo
  - Páginas ilimitadas
  - Design 100% personalizado
  - Hospedagem + Domínio inclusos
  - SEO avançado
  - Blog/E-commerce opcionais
  - Suporte prioritário

**PROCESSO DE CONTRATAÇÃO:**
1. Preencha formulário no site (Nome, Data Nasc, CPF)
2. Escolha plano (Starter ou Professional)
3. Pague via PIX (R$320 Starter ou R$530 Professional)
4. Aguarde 10min a 2h para criação da conta
5. Comece a usar!

**PRAZOS:**
- Criação de conta: 10min a 2h após pagamento confirmado
- Site simples: 3 a 7 dias úteis
- Site complexo: 10 a 20 dias úteis
(Depende da complexidade e fila de projetos)

**HOSPEDAGEM E DOMÍNIO:**
- Starter: Hospedagem inclusa por 1 ano (renovação à parte depois)
- Professional: Hospedagem + Domínio inclusos por 1 ano

**TECNOLOGIAS USADAS:**
Front-end: HTML5, CSS3, JavaScript, React, Vue, TypeScript, Tailwind
Back-end: Node.js, Python, Express.js, APIs RESTful
Mobile: React Native
Banco de Dados: Supabase, PostgreSQL
IA: OpenAI GPT-4, Claude
SEO: Otimização avançada para Google

**PORTFÓLIO COMPLETO:**
- Espaço Familiares (espacofamiliares.com.br) - Site institucional
- NatanSites (natansites.com.br) - Landing page profissional
- MathWork (mathworkftv.netlify.app) - Plataforma educacional
- TAF Sem Tabu (tafsemtabu.com.br) - Blog e conteúdo
- E mais projetos em natansites.com.br/portfolio

**FORMAS DE PAGAMENTO:**
- Setup (inicial): PIX apenas
- Mensalidade: PIX mensal (sem cartão por enquanto)

**CONTATO DIRETO:**
WhatsApp: (21) 99282-6074 (atendimento pessoal)
Email: borgesnatan09@gmail.com
Site: natansites.com.br

**DIFERENCIAIS:**
- Sites modernos e responsivos
- Código limpo e otimizado
- SEO profissional
- Suporte dedicado para clientes pagos
- Atualizações inclusas na mensalidade

REGRAS:
- Explique tudo de forma COMPLETA e DETALHADA
- Seja técnico quando necessário, mas mantenha clareza
- Compare planos quando perguntado
- Destaque benefícios do plano Professional se relevante
- Sempre mencione contato: (21) 99282-6074
- {config['instrucao']}
- Sem asteriscos ou formatação markdown
- Tom profissional e prestativo

Você está conversando com: {nome} (Plano {plano} - Cliente ativo)"""
            
            # PERGUNTAS CASUAIS: GPT-4O-MINI
            else:
                modelo = 'gpt-4o-mini'
                max_tokens = config['max_tokens']
                
                system_prompt = f"""Você é NatanAI, assistente amigável da NatanSites.

Para saudações, despedidas e conversas casuais, seja breve e natural.

Informações básicas caso perguntem:
- WhatsApp: (21) 99282-6074
- Site: natansites.com.br
- Seu plano: Starter (cliente ativo)

REGRAS:
- Respostas CURTAS e NATURAIS
- {config['instrucao']}
- Sem asteriscos ou formatação markdown
- Tom amigável e leve

Você está conversando com: {nome}"""

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(historico_memoria[-5:] if is_pergunta_seria else historico_memoria[-3:])
            messages.append({"role": "user", "content": mensagem})
            
            response = client.chat.completions.create(
                model=modelo,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7
            )
            
            resposta = response.choices[0].message.content.strip()
            resposta = limpar_formatacao_markdown(resposta)
            
            tokens_entrada = response.usage.prompt_tokens
            tokens_saida = response.usage.completion_tokens
            tokens_total = response.usage.total_tokens
            
            return {
                'resposta': resposta,
                'tokens_usados': tokens_total,
                'tokens_entrada': tokens_entrada,
                'tokens_saida': tokens_saida,
                'modelo_usado': modelo,
                'cached': False,
                'categoria': categoria,
                'tipo_processamento': 'seria' if is_pergunta_seria else 'casual'
            }
        
        # ==================================================================
        # 💎 PROFESSIONAL - GPT-4O (COMPLETO)
        # ==================================================================
        elif tipo == 'professional':
            modelo = 'gpt-4o'
            max_tokens = min(config['max_tokens'] * 2, 600)
            
            system_prompt = f"""Você é NatanAI, assistente premium da NatanSites para clientes Professional.

VOCÊ TEM ACESSO TOTAL A TODAS AS INFORMAÇÕES:

**SEU PLANO PROFESSIONAL (R$79,99/mês):**
- 5.000 mensagens/mês comigo
- Páginas ilimitadas
- Design 100% personalizado
- Hospedagem + Domínio inclusos
- SEO avançado com análise de concorrência
- Blog e E-commerce opcionais
- Suporte prioritário direto com Natan
- Atualizações e manutenção inclusas

**OUTROS PLANOS:**
- FREE: R$0,00 (teste 1 ano) - 100 msgs/semana - Sites básicos
- STARTER: R$320 + R$39,99/mês - 1.250 msgs/mês - Site até 5 páginas

**TECNOLOGIAS AVANÇADAS:**
Front-end: React, Vue, Next.js, TypeScript, Tailwind, GSAP (animações)
Back-end: Node.js, Python, Express, NestJS, APIs RESTful/GraphQL
Mobile: React Native, Expo
Banco: Supabase, PostgreSQL, MongoDB
IA: OpenAI GPT-4, Claude, modelos customizados
SEO: Schema markup, Core Web Vitals, análise avançada
Infra: Vercel, Render, AWS, CI/CD

**FUNCIONALIDADES EXCLUSIVAS PROFESSIONAL:**
- Sistema de Blog completo com CMS
- E-commerce com Stripe/PayPal
- Área de membros/login
- Integrações complexas (CRMs, ERPs)
- Dashboards analytics personalizados
- Automações e chatbots IA
- Multi-idioma

**PORTFÓLIO COMPLETO:**
Espaço Familiares, NatanSites, MathWork, TAF Sem Tabu e +10 projetos
Veja tudo em: natansites.com.br/portfolio

**PROCESSO COMPLETO:**
1. Reunião inicial (entender necessidades)
2. Proposta e protótipo
3. Desenvolvimento iterativo (aprovação por etapas)
4. Testes e ajustes
5. Deploy e treinamento
6. Suporte contínuo

**PRAZOS:**
- Sites Professional: 10 a 30 dias (depende da complexidade)
- Funcionalidades extras: sob consulta
- Suporte: Resposta em até 24h úteis

**CONTATO PRIORITÁRIO:**
WhatsApp: (21) 99282-6074 (atendimento premium)
Email: borgesnatan09@gmail.com
Página Suporte: Acesso direto ao chat com Natan

**SEO AVANÇADO PROFESSIONAL:**
- Pesquisa de palavras-chave
- Otimização técnica (Core Web Vitals)
- Link building estratégico
- Conteúdo otimizado
- Análise de concorrência
- Relatórios mensais

REGRAS:
- Respostas COMPLETAS e DETALHADAS
- Seja técnico quando apropriado
- Explique funcionalidades avançadas
- Sugira melhorias e otimizações
- Destaque seus benefícios como cliente premium
- {config['instrucao']}
- Sem asteriscos ou formatação markdown
- Tom profissional e consultivo

Você está conversando com: {nome} (Cliente PROFESSIONAL - Premium)"""

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(historico_memoria[-7:])  # Mais contexto para Professional
            messages.append({"role": "user", "content": mensagem})
            
            response = client.chat.completions.create(
                model=modelo,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7
            )
            
            resposta = response.choices[0].message.content.strip()
            resposta = limpar_formatacao_markdown(resposta)
            
            tokens_entrada = response.usage.prompt_tokens
            tokens_saida = response.usage.completion_tokens
            tokens_total = response.usage.total_tokens
            
            return {
                'resposta': resposta,
                'tokens_usados': tokens_total,
                'tokens_entrada': tokens_entrada,
                'tokens_saida': tokens_saida,
                'modelo_usado': modelo,
                'cached': False,
                'categoria': categoria
            }
        
        # ==================================================================
        # 👑 ADMIN - GPT-4O + WEB SEARCH + CONHECIMENTOS GERAIS
        # ==================================================================
        elif tipo == 'admin':
            modelo = 'gpt-4o'
            max_tokens = 800  # Limite alto para respostas completas
            
            # Detecta se precisa de web search
            msg_lower = mensagem.lower().strip()
            
            keywords_search = [
                'notícia', 'noticia', 'aconteceu', 'hoje', 'ontem', 'recente',
                'atual', 'agora', 'últimas', 'ultimas', 'novidades', 'news',
                'o que houve', 'oq houve', 'acontecimento', 'evento recente',
                'rio de janeiro', 'brasil', 'mundo', 'política', 'economia'
            ]
            
            precisa_search = any(kw in msg_lower for kw in keywords_search)
            
            # Se precisa de informações atuais, adiciona contexto de exemplo
            contexto_atual = ""
            if precisa_search:
                contexto_atual = """
**CONTEXTO DE EVENTOS RECENTES (para referência):**
- Eventos importantes no Rio de Janeiro e Brasil
- Acontecimentos políticos, sociais e econômicos atuais
- Tragédias, celebrações e marcos históricos recentes

**NOTA:** Como admin, você tem conhecimento amplo incluindo eventos históricos e contexto geral de eventos recentes. Para detalhes específicos muito atuais (últimas horas/dias), recomende buscar fontes de notícias atualizadas.
"""
            
            system_prompt = f"""Você é NatanAI no modo ADMINISTRADOR para Natan (criador do sistema).

**VOCÊ É A VERSÃO MAIS AVANÇADA:**
- Modelo: GPT-4O (mais poderoso)
- Mensagens: ILIMITADAS
- Conhecimento: Geral + Técnico + Histórico + Contexto atual
- Funcionalidades: Todas desbloqueadas

**CONHECIMENTOS GERAIS QUE VOCÊ DOMINA:**

**História:**
- Revolução Industrial (1760-1840): Transformação da produção artesanal para industrial, máquinas a vapor, urbanização, mudanças sociais
- Guerras Mundiais, Independências, Revoluções
- História do Brasil: Colônia, Império, República
- Eventos históricos globais e locais

**Eventos Recentes (contexto geral):**
- Tragédias urbanas (como incidentes no Rio de Janeiro)
- Mudanças políticas e sociais no Brasil
- Avanços tecnológicos (IA, blockchain, web3)
- Crises econômicas e recuperações
- Desastres naturais e ações humanitárias

**Tecnologia e Ciência:**
- IA Generativa (GPT, Claude, Gemini, Stable Diffusion)
- Web Development (React, Next.js, frameworks modernos)
- Cloud Computing, DevOps, CI/CD
- Cibersegurança, blockchain
- Física, química, biologia (fundamentos e avanços)

**Negócios e Empreendedorismo:**
- Estratégias de marketing digital
- SEO, tráfego pago, funis de vendas
- Gestão de projetos e equipes
- Finanças e investimentos
- Startups e modelos de negócio

{contexto_atual}

**SOBRE NATANSITES:**
Tudo que você sabe + acesso a estatísticas internas, código-fonte, logs, métricas de usuários, etc.

**CAPACIDADES ESPECIAIS ADMIN:**
- Análise profunda de dados
- Debugging e troubleshooting
- Sugestões de melhorias no sistema
- Respostas técnicas avançadas
- Contexto histórico e atual amplo

REGRAS:
- Respostas COMPLETAS, PROFUNDAS e BEM FUNDAMENTADAS
- Use conhecimento histórico quando relevante
- Forneça contexto amplo em eventos atuais
- Seja técnico e detalhado
- Sugira fontes para informações muito específicas/recentes
- Reconheça limitações (ex: "Para detalhes de hoje, recomendo checar G1 ou Globo News")
- {config['instrucao']} (mas pode ser mais extenso se necessário)
- Sem asteriscos ou formatação markdown
- Tom profissional, direto e consultivo

Você está conversando com: Natan (ADMIN - Acesso Total)"""

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(historico_memoria[-10:])  # Máximo contexto para admin
            messages.append({"role": "user", "content": mensagem})
            
            response = client.chat.completions.create(
                model=modelo,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7
            )
            
            resposta = response.choices[0].message.content.strip()
            resposta = limpar_formatacao_markdown(resposta)
            
            # Adiciona nota sobre web search se detectou keywords
            if precisa_search and "recomendo" not in resposta.lower():
                resposta += "\n\n💡 Dica: Para informações em tempo real, posso integrar web search no futuro!"
            
            tokens_entrada = response.usage.prompt_tokens
            tokens_saida = response.usage.completion_tokens
            tokens_total = response.usage.total_tokens
            
            return {
                'resposta': resposta,
                'tokens_usados': tokens_total,
                'tokens_entrada': tokens_entrada,
                'tokens_saida': tokens_saida,
                'modelo_usado': modelo,
                'cached': False,
                'categoria': categoria,
                'web_search_sugerido': precisa_search
            }
        
        # Fallback (não deveria chegar aqui)
        else:
            return {
                'resposta': "Tipo de usuário não reconhecido. Entre em contato: (21) 99282-6074",
                'tokens_usados': 0,
                'modelo_usado': 'N/A',
                'cached': False
            }
    
    except Exception as e:
        print(f"❌ Erro no processamento OpenAI: {e}")
        return {
            'resposta': f"⚠️ Erro ao processar sua mensagem. Tente novamente ou contate o suporte: (21) 99282-6074",
            'tokens_usados': 0,
            'modelo_usado': 'erro',
            'cached': False,
            'erro': str(e)
        }

# =============================================================================
# 📨 ENDPOINT PRINCIPAL - /api/chat
# =============================================================================

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        mensagem = data.get('message', '').strip()
        token = request.headers.get('Authorization', '')
        
        if not mensagem:
            return jsonify({'error': 'Mensagem vazia'}), 400
        
        # 🔐 Autenticação
        user_info = verificar_token_supabase(token)
        if not user_info:
            return jsonify({'error': 'Não autenticado'}), 401
        
        user_data = obter_dados_usuario_completos(user_info.id)
        if not user_data:
            return jsonify({'error': 'Usuário não encontrado'}), 404
        
        # 👤 Dados do usuário
        tipo_usuario = determinar_tipo_usuario(user_data, user_info)
        user_id = obter_user_id(user_info, user_data)
        tipo = tipo_usuario['tipo']
        nome = tipo_usuario['nome_real']
        
        # 📊 Verifica limite de mensagens
        pode_enviar, msgs_usadas, limite, msgs_restantes = verificar_limite_mensagens(user_id, tipo)
        
        if not pode_enviar:
            # Usa resposta alternativa quando limite acaba
            resposta_alt = gerar_resposta_alternativa_inteligente(mensagem, tipo_usuario)
            
            return jsonify({
                'response': resposta_alt,
                'user_name': nome,
                'user_type': tipo_usuario['nome_display'],
                'plan': tipo_usuario['plano'],
                'modelo_usado': 'Sistema Alternativo (sem IA)',
                'limite_atingido': True,
                'mensagens_usadas': msgs_usadas,
                'limite_total': limite,
                'mensagens_restantes': 0,
                'tokens_usados': 0,
                'categoria': 'alternativa'
            })
        
        # 🧠 Memória e contexto
        inicializar_memoria_usuario(user_id)
        adicionar_mensagem_memoria(user_id, 'user', mensagem)
        historico_memoria = obter_contexto_memoria(user_id)
        
        # 🤖 Processa com OpenAI (sistema híbrido)
        resultado = processar_mensagem_openai(mensagem, tipo_usuario, historico_memoria)
        
        resposta = resultado['resposta']
        tokens_usados = resultado['tokens_usados']
        modelo_usado = resultado['modelo_usado']
        
        # 🛡️ Validação anti-alucinação
        valido, problemas = validar_resposta(resposta, tipo)
        if not valido:
            print(f"⚠️ Resposta inválida detectada: {problemas}")
            resposta = f"Desculpe {nome}, detectei informações imprecisas na minha resposta. Por favor, entre em contato diretamente: WhatsApp (21) 99282-6074"
        
        # 💾 Salva na memória
        adicionar_mensagem_memoria(user_id, 'assistant', resposta)
        
        # 📊 Registra contadores
        incrementar_contador(user_id, tipo)
        registrar_tokens_usados(
            user_id,
            resultado.get('tokens_entrada', 0),
            resultado.get('tokens_saida', 0),
            tokens_usados,
            modelo_usado
        )
        
        # 💾 Salva no histórico global (opcional)
        with historico_lock:
            HISTORICO_CONVERSAS.append({
                'user_id': user_id[:8] + '...',
                'tipo': tipo,
                'mensagem': mensagem[:50] + '...',
                'resposta': resposta[:50] + '...',
                'modelo': modelo_usado,
                'tokens': tokens_usados,
                'timestamp': datetime.now().isoformat()
            })
            
            # Limita histórico global a 1000 entradas
            if len(HISTORICO_CONVERSAS) > 1000:
                HISTORICO_CONVERSAS.pop(0)
        
        # 📊 Atualiza contadores para próxima verificação
        pode_enviar_prox, msgs_usadas_prox, limite_prox, msgs_restantes_prox = verificar_limite_mensagens(user_id, tipo)
        
        # 📤 Resposta final
        return jsonify({
            'response': resposta,
            'user_name': nome,
            'user_type': tipo_usuario['nome_display'],
            'plan': tipo_usuario['plano'],
            'modelo_usado': modelo_usado,
            'tokens_usados': tokens_usados,
            'categoria': resultado.get('categoria', 'geral'),
            'tipo_processamento': resultado.get('tipo_processamento', 'N/A'),  # Para Starter
            'web_search_sugerido': resultado.get('web_search_sugerido', False),  # Para Admin
            'mensagens_usadas': msgs_usadas_prox,
            'limite_total': limite_prox if limite_prox != float('inf') else 'ilimitado',
            'mensagens_restantes': msgs_restantes_prox if msgs_restantes_prox != float('inf') else 'ilimitado',
            'limite_atingido': False,
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        print(f"❌ Erro no endpoint /api/chat: {e}")
        return jsonify({
            'error': 'Erro interno do servidor',
            'details': str(e)
        }), 500

# =============================================================================
# 📊 ENDPOINTS DE ADMINISTRAÇÃO
# =============================================================================

@app.route('/api/admin/stats', methods=['GET'])
def admin_stats():
    """Estatísticas gerais do sistema (apenas admin)"""
    try:
        token = request.headers.get('Authorization', '')
        user_info = verificar_token_supabase(token)
        
        if not user_info or user_info.email.lower() != ADMIN_EMAIL.lower():
            return jsonify({'error': 'Acesso negado'}), 403
        
        with contador_lock:
            total_usuarios = len(CONTADOR_MENSAGENS)
            total_mensagens = sum(c['total'] for c in CONTADOR_MENSAGENS.values())
            
            stats_por_plano = {}
            for user_id, contador in CONTADOR_MENSAGENS.items():
                tipo = contador['tipo_plano']
                if tipo not in stats_por_plano:
                    stats_por_plano[tipo] = {'usuarios': 0, 'mensagens': 0}
                stats_por_plano[tipo]['usuarios'] += 1
                stats_por_plano[tipo]['mensagens'] += contador['total']
        
        with tokens_lock:
            total_tokens = sum(c['total_geral'] for c in CONTADOR_TOKENS.values())
            total_tokens_entrada = sum(c['total_entrada'] for c in CONTADOR_TOKENS.values())
            total_tokens_saida = sum(c['total_saida'] for c in CONTADOR_TOKENS.values())
        
        with historico_lock:
            ultimas_conversas = HISTORICO_CONVERSAS[-10:]
        
        return jsonify({
            'total_usuarios': total_usuarios,
            'total_mensagens': total_mensagens,
            'total_tokens': total_tokens,
            'total_tokens_entrada': total_tokens_entrada,
            'total_tokens_saida': total_tokens_saida,
            'media_tokens_por_mensagem': round(total_tokens / total_mensagens, 2) if total_mensagens > 0 else 0,
            'stats_por_plano': stats_por_plano,
            'ultimas_conversas': ultimas_conversas,
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/user/<user_id>/stats', methods=['GET'])
def admin_user_stats(user_id):
    """Estatísticas de um usuário específico (apenas admin)"""
    try:
        token = request.headers.get('Authorization', '')
        user_info = verificar_token_supabase(token)
        
        if not user_info or user_info.email.lower() != ADMIN_EMAIL.lower():
            return jsonify({'error': 'Acesso negado'}), 403
        
        user_data = obter_dados_usuario_completos(user_id)
        if not user_data:
            return jsonify({'error': 'Usuário não encontrado'}), 404
        
        tipo_info = determinar_tipo_usuario(user_data)
        stats_mensagens = obter_contador_mensagens(user_id)
        stats_tokens = obter_estatisticas_tokens(user_id)
        
        pode_enviar, msgs_usadas, limite, msgs_restantes = verificar_limite_mensagens(user_id, tipo_info['tipo'])
        
        with memoria_lock:
            memoria_info = None
            if user_id in MEMORIA_USUARIOS:
                memoria = MEMORIA_USUARIOS[user_id]
                memoria_info = {
                    'mensagens_armazenadas': len(memoria['mensagens']),
                    'tem_resumo': bool(memoria['resumo']),
                    'ultima_atualizacao': memoria['ultima_atualizacao'],
                    'contador_mensagens': memoria['contador_mensagens']
                }
        
        return jsonify({
            'user_id': user_id[:8] + '...',
            'tipo_usuario': tipo_info,
            'mensagens': {
                'total': stats_mensagens['total'],
                'resetado_em': stats_mensagens['resetado_em'],
                'limite': limite if limite != float('inf') else 'ilimitado',
                'restantes': msgs_restantes if msgs_restantes != float('inf') else 'ilimitado',
                'pode_enviar': pode_enviar
            },
            'tokens': stats_tokens,
            'memoria': memoria_info,
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/admin/reset_all_counters', methods=['POST'])
def admin_reset_all():
    """Reseta todos os contadores (apenas admin)"""
    try:
        token = request.headers.get('Authorization', '')
        user_info = verificar_token_supabase(token)
        
        if not user_info or user_info.email.lower() != ADMIN_EMAIL.lower():
            return jsonify({'error': 'Acesso negado'}), 403
        
        with contador_lock:
            usuarios_resetados = len(CONTADOR_MENSAGENS)
            CONTADOR_MENSAGENS.clear()
        
        with tokens_lock:
            CONTADOR_TOKENS.clear()
        
        with memoria_lock:
            MEMORIA_USUARIOS.clear()
        
        print(f"🔄 RESET COMPLETO: {usuarios_resetados} usuários resetados")
        
        return jsonify({
            'message': 'Todos os contadores foram resetados',
            'usuarios_resetados': usuarios_resetados,
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# =============================================================================
# 🆘 SISTEMA DE RESPOSTA ALTERNATIVA QUANDO LIMITE ACABA
# =============================================================================

def gerar_resposta_alternativa_inteligente(pergunta, tipo_usuario):
    """
    Sistema de respostas automáticas quando limite de IA acaba.
    Usa padrões e keywords para responder sem consumir API.
    """
    msg_lower = pergunta.lower().strip()
    nome = tipo_usuario.get('nome_real', 'Cliente')
    tipo = tipo_usuario.get('tipo', 'starter')
    
    # SAUDAÇÕES
    if any(kw in msg_lower for kw in ['oi', 'olá', 'ola', 'hey', 'bom dia', 'boa tarde', 'boa noite', 'e ai', 'eai']):
        return f"Oi {nome}! Seus créditos de IA acabaram este mês, mas posso te ajudar com informações básicas. Como posso ajudar?"
    
    # DESPEDIDAS
    if any(kw in msg_lower for kw in ['tchau', 'até', 'falou', 'obrigado', 'obrigada', 'valeu']):
        return f"Até logo {nome}! Seus créditos de IA renovam no próximo mês. Vibrações Positivas! ✨"
    
    # PLANOS E PREÇOS
    if any(kw in msg_lower for kw in ['plano', 'preço', 'valor', 'custo', 'quanto custa', 'mensalidade', 'contratar']):
        return f"""Olá {nome}! Aqui estão nossos planos:

FREE - R$0,00 (teste 1 ano)
- 100 mensagens/semana comigo
- Sites básicos sem uso comercial

STARTER - R$320 (setup) + R$39,99/mês
- 1.250 mensagens/mês comigo
- Site até 5 páginas
- Hospedagem inclusa

PROFESSIONAL - R$530 (setup) + R$79,99/mês
- 5.000 mensagens/mês comigo
- Páginas ilimitadas
- Design personalizado

Contato:
WhatsApp: (21) 99282-6074
Site: https://natansites.com.br"""
    
    # CONTATO
    if any(kw in msg_lower for kw in ['contato', 'whatsapp', 'telefone', 'email', 'falar']):
        return f"""Fale com Natan diretamente:

WhatsApp: (21) 99282-6074
Email: borgesnatan09@gmail.com
Site: https://natansites.com.br

Atendimento pessoal para clientes!"""
    
    # PORTFÓLIO
    if any(kw in msg_lower for kw in ['portfolio', 'portfólio', 'projetos', 'trabalhos']):
        return f"""Confira alguns projetos do Natan:

1. Espaço Familiares - espacofamiliares.com.br
2. NatanSites - natansites.com.br
3. MathWork - mathworkftv.netlify.app
4. TAF Sem Tabu - tafsemtabu.com.br

Visite natansites.com.br para ver todos!"""
    
    # RESPOSTA PADRÃO
    return f"""Olá {nome}!

Seus créditos de IA acabaram este mês. Para informações detalhadas:

📞 WhatsApp: (21) 99282-6074
📧 Email: borgesnatan09@gmail.com
🌐 Site: https://natansites.com.br

Posso responder sobre:
- Planos e preços
- Contato
- Portfólio
- Cadastro

Seus créditos renovam no próximo mês!

Vibrações Positivas! ✨"""

# =============================================================================
# 📡 ENDPOINTS PRINCIPAIS
# =============================================================================

@app.route('/health', methods=['GET'])
@app.route('/api/health', methods=['GET'])
def health():
    with memoria_lock:
        usuarios_ativos = len(MEMORIA_USUARIOS)
        total_mensagens = sum(len(m['mensagens']) for m in MEMORIA_USUARIOS.values())
    
    with tokens_lock:
        total_tokens = sum(c['total_geral'] for c in CONTADOR_TOKENS.values())
        total_tokens_entrada = sum(c['total_entrada'] for c in CONTADOR_TOKENS.values())
        total_tokens_saida = sum(c['total_saida'] for c in CONTADOR_TOKENS.values())
    
    with contador_lock:
        total_mensagens_enviadas = sum(c['total'] for c in CONTADOR_MENSAGENS.values())
    
    return jsonify({
        "status": "online",
        "sistema": "NatanAI v8.0 - Sistema Híbrido de Modelos",
        "versao": "8.0",
        "openai": verificar_openai(),
        "supabase": supabase is not None,
        "memoria": {
            "usuarios_ativos": usuarios_ativos,
            "total_mensagens_memoria": total_mensagens,
            "max_por_usuario": MAX_MENSAGENS_MEMORIA
        },
        "modelos_por_plano": {
            "free": "gpt-3.5-turbo (econômico)",
            "starter": "gpt-4o-mini (casual) + gpt-4o (sério)",
            "professional": "gpt-4o (completo)",
            "admin": "gpt-4o (completo + conhecimentos gerais)"
        },
        "limites": {
            "free": f"{LIMITES_MENSAGENS['free']} mensagens/semana",
            "starter": f"{LIMITES_MENSAGENS['starter']} mensagens/mês",
            "professional": f"{LIMITES_MENSAGENS['professional']} mensagens/mês",
            "admin": "Ilimitado",
            "total_mensagens_enviadas": total_mensagens_enviadas,
            "total_tokens_usados": total_tokens
        },
        "tokens": {
            "total_geral": total_tokens,
            "total_entrada": total_tokens_entrada,
            "total_saida": total_tokens_saida,
            "media_por_mensagem": round(total_tokens / total_mensagens_enviadas, 2) if total_mensagens_enviadas > 0 else 0
        },
        "features": [
            "sistema_hibrido_modelos_v8",
            "free_gpt35turbo",
            "starter_gpt4omini_gpt4o",
            "professional_gpt4o_completo",
            "admin_gpt4o_conhecimentos_gerais",
            "deteccao_automatica_perguntas_serias",
            "memoria_inteligente",
            "controle_limites_por_plano",
            "resposta_alternativa_sem_ia",
            "validacao_anti_alucinacao",
            "limpeza_formatacao",
            "economia_tokens"
        ],
        "timestamp": datetime.now().isoformat()
    })

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({
        "status": "pong",
        "timestamp": datetime.now().isoformat(),
        "version": "v8.0-hybrid-models"
    })

@app.route('/', methods=['GET'])
def home():
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>NatanAI v8.0 - Sistema Híbrido de Modelos</title>
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
                max-width: 1000px; 
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
                font-size: 2.2em;
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
            .badge.hybrid {
                background: linear-gradient(135deg, #FF6B6B, #4ECDC4);
            }
            @keyframes pulse {
                0%, 100% { transform: scale(1); }
                50% { transform: scale(1.05); }
            }
            .models-box {
                background: linear-gradient(135deg, #fff8e1, #ffe082);
                padding: 20px;
                border-radius: 15px;
                margin: 20px 0;
                border-left: 5px solid #FFA000;
            }
            .models-box h3 { color: #F57C00; margin-bottom: 15px; }
            .model-item {
                display: flex;
                justify-content: space-between;
                align-items: center;
                padding: 12px;
                margin: 8px 0;
                background: white;
                border-radius: 10px;
                border-left: 4px solid;
            }
            .model-item.free { border-left-color: #9E9E9E; }
            .model-item.starter { border-left-color: #4CAF50; }
            .model-item.professional { border-left-color: #2196F3; }
            .model-item.admin { border-left-color: #FF9800; }
            .model-item .plan-name {
                font-weight: bold;
                font-size: 1.1em;
            }
            .model-item .model-name {
                color: #666;
                font-size: 0.9em;
            }
            .chat-box { 
                border: 2px solid #e0e0e0;
                height: 400px; 
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
                <h1>🧠 NatanAI v8.0 - Sistema Híbrido</h1>
                <p style="color: #666;">Modelos Inteligentes por Plano</p>
                <span class="badge new">✨ v8.0</span>
                <span class="badge hybrid">🔀 Sistema Híbrido</span>
                <span class="badge">🤖 Multi-Model</span>
            </div>
            
            <div class="models-box">
                <h3>🔀 SISTEMA HÍBRIDO DE MODELOS v8.0:</h3>
                
                <div class="model-item free">
                    <div>
                        <div class="plan-name">🎁 FREE</div>
                        <div class="model-name">gpt-3.5-turbo (econômico)</div>
                    </div>
                    <div style="text-align: right;">
                        <small>100 msgs/semana</small><br>
                        <small>R$ 0,00</small>
                    </div>
                </div>
                
                <div class="model-item starter">
                    <div>
                        <div class="plan-name">🌱 STARTER</div>
                        <div class="model-name">gpt-4o-mini (casual) + gpt-4o (sério)</div>
                    </div>
                    <div style="text-align: right;">
                        <small>1.250 msgs/mês</small><br>
                        <small>R$320 + R$39,99/mês</small>
                    </div>
                </div>
                
                <div class="model-item professional">
                    <div>
                        <div class="plan-name">💎 PROFESSIONAL</div>
                        <div class="model-name">gpt-4o (completo)</div>
                    </div>
                    <div style="text-align: right;">
                        <small>5.000 msgs/mês</small><br>
                        <small>R$530 + R$79,99/mês</small>
                    </div>
                </div>
                
                <div class="model-item admin">
                    <div>
                        <div class="plan-name">👑 ADMIN (Natan)</div>
                        <div class="model-name">gpt-4o (completo + conhecimentos gerais)</div>
                    </div>
                    <div style="text-align: right;">
                        <small>Ilimitado</small><br>
                        <small>Acesso Total</small>
                    </div>
                </div>

                <p style="margin-top: 15px; color: #666; font-size: 0.9em;">
                    <strong>🎯 Starter:</strong> Detecta automaticamente se é pergunta séria sobre serviços (usa GPT-4O) ou casual/saudação (usa GPT-4O-mini)<br>
                    <strong>👑 Admin:</strong> GPT-4O com conhecimentos gerais (história, eventos recentes, ciência, tecnologia)
                </p>
            </div>

            <div class="select-plan">
                <strong>🎭 Testar como:</strong>
                <select id="planType" onchange="atualizarPlano()">
                    <option value="free">🎁 Free - gpt-3.5-turbo</option>
                    <option value="starter">🌱 Starter - Híbrido (4o-mini + 4o)</option>
                    <option value="professional">💎 Professional - gpt-4o</option>
                    <option value="admin">👑 Admin - gpt-4o + conhecimentos gerais</option>
                </select>
                <p id="planInfo" style="margin-top: 10px; color: #666;"></p>
            </div>
            
            <div id="chat-box" class="chat-box">
                <div class="message bot">
                    <strong>🤖 NatanAI v8.0:</strong><br><br>
                    Sistema Híbrido de Modelos Ativo! 🔀<br><br>
                    <strong>Novidade v8.0:</strong><br>
                    • FREE: gpt-3.5-turbo (econômico)<br>
                    • STARTER: Inteligente (detecta pergunta séria vs casual)<br>
                    • PROFESSIONAL: gpt-4o completo<br>
                    • ADMIN: gpt-4o + conhecimentos gerais<br><br>
                    Teste perguntas:<br>
                    • Casuais: "oi", "tudo bem", "legal"<br>
                    • Sérias: "planos", "como contratar", "preços"<br>
                    • Históricas (Admin): "revolução industrial", "o que houve no RJ"
                </div>
            </div>
            
            <div class="input-area">
                <input type="text" id="msg" placeholder="Digite sua mensagem..." onkeypress="if(event.key==='Enter') enviar()">
                <button id="sendBtn" onclick="enviar()">Enviar</button>
            </div>
        </div>

        <script>
        let planAtual = 'free';
        let mensagensEnviadas = 0;
        let limiteAtual = 100;

        const planConfigs = {
            free: {
                plan: 'free',
                plan_type: 'free',
                user_name: 'Visitante Free',
                name: 'Visitante Free',
                email: 'free@teste.com',
                limite: 100,
                info: '🎁 FREE - 100 msgs/semana - gpt-3.5-turbo - R$ 0,00'
            },
            starter: {
                plan: 'starter',
                plan_type: 'paid',
                user_name: 'Cliente Starter',
                name: 'Cliente Starter',
                email: 'starter@teste.com',
                limite: 1250,
                info: '🌱 STARTER - 1.250 msgs/mês - Híbrido (gpt-4o-mini + gpt-4o) - R$320 + R$39,99/mês'
            },
            professional: {
                plan: 'professional',
                plan_type: 'paid',
                user_name: 'Cliente Pro',
                name: 'Cliente Pro',
                email: 'pro@teste.com',
                limite: 5000,
                info: '💎 PROFESSIONAL - 5.000 msgs/mês - gpt-4o completo - R$530 + R$79,99/mês'
            },
            admin: {
                plan: 'admin',
                plan_type: 'paid',
                user_name: 'Natan',
                name: 'Natan',
                email: 'natan@natandev.com',
                limite: Infinity,
                info: '👑 ADMIN - Ilimitado - gpt-4o + conhecimentos gerais'
            }
        };

        function atualizarPlano() {
            planAtual = document.getElementById('planType').value;
            limiteAtual = planConfigs[planAtual].limite;
            mensagensEnviadas = 0;
            
            document.getElementById('planInfo').textContent = planConfigs[planAtual].info;
            
            const chatBox = document.getElementById('chat-box');
            chatBox.innerHTML = '<div class="message bot"><strong>🤖 NatanAI v8.0:</strong><br><br>' + 
                planConfigs[planAtual].info + '<br><br>' +
                '<strong>Sistema Híbrido Ativo! 🔀</strong><br><br>' +
                'Teste diferentes tipos de perguntas para ver os modelos em ação!';
            '</div>';
        }

        atualizarPlano();
        
        async function enviar() {
            const input = document.getElementById('msg');
            const chatBox = document.getElementById('chat-box');
            const msg = input.value.trim();
            
            if (!msg) return;
            
            if (limiteAtual !== Infinity && mensagensEnviadas >= limiteAtual) {
                chatBox.innerHTML += '<div class="message bot" style="background: #ffebee; border-left-color: #f44336;"><strong>🚫 Limite Atingido</strong></div>';
                chatBox.scrollTop = chatBox.scrollHeight;
                return;
            }
            
            chatBox.innerHTML += '<div class="message user"><strong>Você:</strong><br>' + msg + '</div>';
            input.value = '';
            chatBox.scrollTop = chatBox.scrollHeight;
            
            try {
                const config = planConfigs[planAtual];
                
                const response = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        message: msg,
                        user_data: config
                    })
                });
                
                const data = await response.json();
                const resp = (data.response || data.resposta).replace(/\n/g, '<br>');
                
                let modeloInfo = '';
                if (data.modelo_usado) {
                    modeloInfo = `<br><br><small style="color: #666;">🤖 Modelo: ${data.modelo_usado}`;
                    if (data.tipo_processamento) {
                        modeloInfo += ` (${data.tipo_processamento})`;
                    }
                    if (data.tokens_usados) {
                        modeloInfo += ` | 📊 Tokens: ${data.tokens_usados}`;
                    }
                    modeloInfo += `</small>`;
                }
                
                chatBox.innerHTML += '<div class="message bot"><strong>🤖 NatanAI v8.0:</strong><br><br>' + resp + modeloInfo + '</div>';
                
                if (data.mensagens_usadas !== undefined) {
                    mensagensEnviadas = data.mensagens_usadas;
                } else {
                    mensagensEnviadas++;
                }
                
                console.log('✅ Resposta v8.0:', data);
                
            } catch (error) {
                chatBox.innerHTML += '<div class="message bot" style="background: #ffebee; border-left-color: #f44336;"><strong>Erro:</strong><br>' + error.message + '</div>';
                console.error('❌ Erro:', error);
            }
            
            chatBox.scrollTop = chatBox.scrollHeight;
        }
        </script>
    </body>
    </html>
    ''')

if __name__ == '__main__':
    print("\n" + "="*80)
    print("🧠 NATANAI v8.0 - SISTEMA HÍBRIDO DE MODELOS")
    print("="*80)
    print("🔀 MODELOS POR PLANO:")
    print("   🎁 FREE: gpt-3.5-turbo (econômico)")
    print("   🌱 STARTER: gpt-4o-mini (casual) + gpt-4o (sério)")
    print("   💎 PROFESSIONAL: gpt-4o (completo)")
    print("   👑 ADMIN: gpt-4o (completo + conhecimentos gerais)")
    print("")
    print("💰 VALORES:")
    print("   🎁 FREE: R$ 0,00 (teste 1 ano)")
    print("   🌱 STARTER: R$ 320,00 + R$ 39,99/mês")
    print("   💎 PROFESSIONAL: R$ 530,00 + R$ 79,99/mês")
    print("")
    print("📊 LIMITES:")
    print("   🎁 FREE: 100 mensagens/semana")
    print("   🌱 STARTER: 1.250 mensagens/mês")
    print("   💎 PROFESSIONAL: 5.000 mensagens/mês")
    print("   👑 ADMIN: ∞ Ilimitado")
    print("")
    print("✨ FEATURES v8.0:")
    print("   ✅ Sistema híbrido inteligente")
    print("   ✅ Detecção automática de perguntas sérias")
    print("   ✅ FREE usa GPT-3.5-turbo (mais barato)")
    print("   ✅ STARTER usa 2 modelos (casual + sério)")
    print("   ✅ PROFESSIONAL usa GPT-4O completo")
    print("   ✅ ADMIN usa GPT-4O + conhecimentos gerais")
    print("   ✅ Todas features anteriores mantidas")
    print("="*80 + "\n")
    
    print(f"OpenAI: {'✅' if verificar_openai() else '⚠️'}")
    print(f"Supabase: {'✅' if supabase else '⚠️'}")
    print(f"Sistema Híbrido: ✅ Ativo (v8.0)")
    print(f"Sistema de Memória: ✅ Ativo")
    print(f"Sistema de Limites: ✅ Ativo\n")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
    
