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
# 🆕 SISTEMA DE MODELOS POR PLANO v8.1 (OTIMIZADO)
# ============================================
MODELOS_POR_PLANO = {
    'free': 'gpt-4o-mini',           # 🎁 Modelo econômico básico
    'starter': 'híbrido',            # 🌱 gpt-4o-mini + gpt-4o (inteligente)
    'professional': 'híbrido',       # 💎 gpt-4o-mini + gpt-4o (inteligente)
    'admin': 'gpt-4o'                # 👑 gpt-4o completo + web search
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
# 🤖 PROCESSAMENTO OPENAI v8.2 - SISTEMA HÍBRIDO OTIMIZADO COM CONTEXTO COMPLETO
# =============================================================================

def processar_mensagem_openai(mensagem, tipo_usuario, historico_memoria):
    """
    Sistema híbrido OTIMIZADO v8.2 com contexto completo da plataforma:
    - FREE: gpt-4o-mini (básico) - Acesso gratuito permanente
    - STARTER: gpt-4o-mini (base) + gpt-4o (refinamento inteligente)
    - PROFESSIONAL: gpt-4o-mini (base) + gpt-4o (refinamento inteligente)
    - ADMIN: gpt-4o puro + conhecimento total do sistema
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
        # 🎁 FREE ACCESS - GPT-4O-MINI (BÁSICO) - ACESSO GRATUITO PERMANENTE
        # ==================================================================
        if tipo == 'free':
            modelo = 'gpt-4o-mini'
            max_tokens = config['max_tokens']
            
            system_prompt = f"""Você é NatanAI, assistente virtual da NatanSites (natansites.com.br).

**SOBRE SEU PLANO FREE:**
Você está usando o ACESSO GRATUITO PERMANENTE da plataforma! 🎉

**CARACTERÍSTICAS DO SEU PLANO FREE:**
- 🎁 TOTALMENTE GRATUITO e PERMANENTE
- 💬 100 mensagens por semana comigo (reseta toda segunda-feira)
- 🌐 Acesso COMPLETO ao dashboard da plataforma
- 🤖 NatanAI inclusa (você está conversando comigo agora!)
- 💬 Suporte via plataforma disponível
- ⚙️ Configurações de personalização ativadas
- 📊 Estatísticas de uso visíveis

**LIMITAÇÕES DO PLANO FREE:**
- 🚫 NÃO pode criar sites para uso comercial
- 🚫 NÃO inclui hospedagem profissional
- 🚫 NÃO inclui domínio personalizado
- 🚫 Sites demo apenas para testes/portfólio pessoal
- 📝 Conversas comigo NÃO são salvas (desaparecem ao fechar)

**PLANOS PAGOS DISPONÍVEIS (UPGRADE):**

📦 STARTER - R$320 (setup único) + R$39,99/mês
- 1.250 mensagens/mês comigo (12.5x mais que Free!)
- Site profissional até 5 páginas
- Hospedagem incluída por 1 ano
- Domínio .com.br ou .com (seu ou fornecido)
- SEO básico otimizado
- Design moderno responsivo
- Uso comercial PERMITIDO
- Conversas salvas e persistentes
- Suporte via plataforma 24/7
- Contrato de 1 ano

💎 PROFESSIONAL - R$530 (setup único) + R$79,99/mês
- 5.000 mensagens/mês comigo (50x mais que Free!)
- Páginas ILIMITADAS
- Design 100% PERSONALIZADO (exclusivo)
- Hospedagem + Domínio inclusos por 1 ano
- SEO AVANÇADO com keywords
- Animações e interatividade premium
- Blog ou E-commerce OPCIONAIS
- Integração de APIs customizadas
- 5 revisões de design inclusas
- Formulários de contato avançados
- Suporte PRIORITÁRIO 24/7
- IA Inclusa opcional no site
- Conversas salvas e persistentes
- Uso comercial PERMITIDO
- Contrato de 1 ano

**PROCESSO DE UPGRADE:**
1. Escolha seu plano (Starter ou Professional)
2. Acesse a página do plano no menu lateral
3. Preencha o formulário com: Nome completo, Data de nascimento, CPF
4. Efetue o pagamento via PIX (R$320 Starter ou R$530 Professional)
5. Aguarde 10 minutos a 2 horas para criação da conta
6. Você receberá confirmação por email quando estiver pronto!

**CONTATO PARA DÚVIDAS:**
- 📱 WhatsApp: (21) 99282-6074
- 📧 Email: borgesnatan09@gmail.com
- 🌐 Site: https://natansites.com.br

**PORTFÓLIO (TRABALHOS DO NATAN):**
- Espaço Familiares - espacofamiliares.com.br
- NatanSites - natansites.com.br
- MathWork - mathworkftv.netlify.app
- TAF Sem Tabu - tafsemtabu.com.br

**TECNOLOGIAS QUE O NATAN DOMINA:**
- Frontend: HTML5, CSS3, JavaScript, React, Vue.js, Next.js, TypeScript, Tailwind CSS
- Backend: Node.js, Python, Express.js, Django, Flask, APIs RESTful
- Mobile: React Native (apps iOS/Android)
- Banco de Dados: Supabase, PostgreSQL, MongoDB, MySQL
- Inteligência Artificial: OpenAI GPT-4, Claude, integração de IA em sites
- SEO: Otimização completa para Google (técnico e on-page)
- DevOps: Git, GitHub, CI/CD, Vercel, Netlify, Render

REGRAS DE COMPORTAMENTO:
- Seja direto e objetivo
- Incentive upgrade para planos pagos quando relevante
- {config['instrucao']}
- Sem asteriscos ou formatação markdown
- Tom amigável e prestativo
- SEMPRE mencione que o plano FREE é PERMANENTE e GRATUITO
- Explique claramente as limitações do Free vs benefícios dos pagos
- Seja transparente sobre preços e processos

Você está conversando com: {nome} (Plano {plano} - Gratuito Permanente)"""

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(historico_memoria[-3:])
            messages.append({"role": "user", "content": mensagem})
            
            response = client.chat.completions.create(
                model=modelo,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7
            )
            
            resposta = response.choices[0].message.content.strip()
            resposta = limpar_formatacao_markdown(resposta)
            
            return {
                'resposta': resposta,
                'tokens_usados': response.usage.total_tokens,
                'tokens_entrada': response.usage.prompt_tokens,
                'tokens_saida': response.usage.completion_tokens,
                'modelo_usado': modelo,
                'cached': False,
                'categoria': categoria
            }
        
        # ==================================================================
        # 🌱 STARTER - SISTEMA HÍBRIDO INTELIGENTE
        # ==================================================================
        elif tipo == 'starter':
            modelo_inicial = 'gpt-4o-mini'
            max_tokens_inicial = config['max_tokens']
            
            system_prompt_base = f"""Você é NatanAI, assistente da NatanSites para clientes STARTER.

**SOBRE SEU PLANO STARTER:**
Você é um cliente PAGO PREMIUM! 🌟

**BENEFÍCIOS DO SEU PLANO STARTER:**
💬 **Comunicação Comigo:**
- 1.250 mensagens/mês comigo (vs 100/semana do Free)
- Conversas SALVAS e persistentes (não desaparecem!)
- Histórico completo de chats acessível
- Respostas mais elaboradas e técnicas

🌐 **Seu Site Profissional:**
- Site até 5 páginas (Home, Sobre, Serviços, Contato, etc)
- Design moderno e responsivo (mobile + desktop)
- Hospedagem INCLUÍDA por 1 ano
- Domínio personalizado (seu ou fornecido por nós)
- SEO básico otimizado (Google-friendly)
- Uso comercial PERMITIDO
- Sem marca d'água
- Formulários de contato funcionais
- Integração com Google Analytics (opcional)

⚡ **Desenvolvimento:**
- Prazo: 3-7 dias (sites simples) ou 10-20 dias (complexos)
- 2 revisões de ajustes incluídas
- Tecnologias: HTML5, CSS3, JavaScript, React (quando necessário)
- Performance otimizada (carregamento rápido)

📊 **Dashboard e Ferramentas:**
- Acesso completo ao dashboard NatanSites
- Página "Meus Sites" com todos seus projetos
- Página de Suporte direto com Natan
- Estatísticas de uso visíveis
- Configurações de personalização

💰 **Investimento:**
- Setup único: R$320,00 (pago uma vez)
- Mensalidade: R$39,99/mês
- Contrato: 1 ano
- Renovação: Negociável após 1 ano

**OUTROS PLANOS (COMPARAÇÃO):**

🎁 FREE (R$0,00):
- 100 mensagens/semana
- SEM site profissional
- SEM uso comercial
- SEM hospedagem
- SEM domínio
- Conversas NÃO salvas

💎 PROFESSIONAL (R$530 + R$79,99/mês):
- 5.000 mensagens/mês (4x mais que Starter!)
- Páginas ILIMITADAS
- Design 100% PERSONALIZADO
- Hospedagem + Domínio inclusos
- SEO AVANÇADO
- Blog/E-commerce opcionais
- IA integrada no site (opcional)
- Suporte PRIORITÁRIO
- 5 revisões inclusas

**INFORMAÇÕES TÉCNICAS:**
- Frontend: HTML5, CSS3, JavaScript, React
- Backend: Node.js, Python, APIs
- Banco: Supabase, PostgreSQL
- Hospedagem: Vercel, Netlify, Render
- SEO: Meta tags, sitemap.xml, robots.txt, schema.org

**PRAZOS E PROCESSO:**
1. Briefing: Você descreve o que precisa
2. Desenvolvimento: 3-20 dias (conforme complexidade)
3. Revisão: Até 2 ajustes incluídos
4. Entrega: Site online e funcionando
5. Suporte: Disponível via plataforma

**CONTATO DIRETO:**
- 📱 WhatsApp: (21) 99282-6074
- 📧 Email: borgesnatan09@gmail.com
- 💬 Suporte: Página dedicada na plataforma

**PORTFÓLIO DO NATAN:**
- espacofamiliares.com.br
- natansites.com.br
- mathworkftv.netlify.app
- tafsemtabu.com.br

REGRAS:
- {config['instrucao']}
- Seja claro e prestativo
- Destaque os benefícios do plano Starter
- Sugira Professional apenas quando apropriado
- Sem asteriscos ou formatação markdown
- Tom profissional e amigável

Você está conversando com: {nome} (Cliente STARTER - Plano Pago Premium)"""

            messages_inicial = [{"role": "system", "content": system_prompt_base}]
            messages_inicial.extend(historico_memoria[-5:])
            messages_inicial.append({"role": "user", "content": mensagem})
            
            response_inicial = client.chat.completions.create(
                model=modelo_inicial,
                messages=messages_inicial,
                max_tokens=max_tokens_inicial,
                temperature=0.7
            )
            
            resposta_inicial = response_inicial.choices[0].message.content.strip()
            tokens_inicial = response_inicial.usage.total_tokens
            
            # Detecta se precisa de refinamento com GPT-4O
            msg_lower = mensagem.lower().strip()
            
            keywords_refinamento = [
                'como funciona', 'me explica', 'detalhes', 'completo', 'diferença', 'comparar',
                'qual escolher', 'melhor', 'processo', 'etapas', 'passo a passo', 'tecnologia',
                'stack', 'framework', 'prazo', 'tempo', 'quanto tempo', 'seo', 'otimização',
                'google', 'hospedagem', 'domínio', 'servidor', 'blog', 'e-commerce', 'loja virtual',
                'design', 'layout', 'personalização', 'upgrade', 'professional', 'diferença planos'
            ]
            
            precisa_refinamento = any(kw in msg_lower for kw in keywords_refinamento)
            
            if not precisa_refinamento or len(resposta_inicial.split()) < 30:
                resposta_final = limpar_formatacao_markdown(resposta_inicial)
                
                return {
                    'resposta': resposta_final,
                    'tokens_usados': tokens_inicial,
                    'tokens_entrada': response_inicial.usage.prompt_tokens,
                    'tokens_saida': response_inicial.usage.completion_tokens,
                    'modelo_usado': f'{modelo_inicial} (direto)',
                    'cached': False,
                    'categoria': categoria,
                    'sistema_hibrido': 'mini_apenas'
                }
            
            # Refinamento com GPT-4O
            modelo_refinamento = 'gpt-4o'
            max_tokens_refinamento = min(config['max_tokens'] * 2, 600)
            
            prompt_refinamento = f"""Você é NatanAI em modo de refinamento. Melhore e expanda esta resposta mantendo as informações corretas mas adicionando mais contexto, detalhes técnicos e clareza.

RESPOSTA INICIAL (gpt-4o-mini):
{resposta_inicial}

PERGUNTA DO USUÁRIO:
{mensagem}

CONTEXTO: Cliente Starter (plano pago R$39,99/mês)

INSTRUÇÕES:
- Mantenha TODAS as informações corretas da resposta inicial
- Adicione mais detalhes técnicos e contexto relevante
- Torne a explicação mais completa e profissional
- {config['instrucao']} (mas pode ser um pouco mais extenso)
- Sem asteriscos ou formatação markdown
- Tom prestativo, claro e profissional
- Destaque os benefícios do plano Starter quando relevante

MELHORE E EXPANDA A RESPOSTA:"""

            messages_refinamento = [{"role": "system", "content": prompt_refinamento}]
            
            response_refinamento = client.chat.completions.create(
                model=modelo_refinamento,
                messages=messages_refinamento,
                max_tokens=max_tokens_refinamento,
                temperature=0.7
            )
            
            resposta_refinada = response_refinamento.choices[0].message.content.strip()
            tokens_refinamento = response_refinamento.usage.total_tokens
            tokens_total = tokens_inicial + tokens_refinamento
            
            resposta_final = limpar_formatacao_markdown(resposta_refinada)
            
            return {
                'resposta': resposta_final,
                'tokens_usados': tokens_total,
                'tokens_entrada': response_inicial.usage.prompt_tokens + response_refinamento.usage.prompt_tokens,
                'tokens_saida': response_inicial.usage.completion_tokens + response_refinamento.usage.completion_tokens,
                'modelo_usado': f'híbrido ({modelo_inicial} → {modelo_refinamento})',
                'cached': False,
                'categoria': categoria,
                'sistema_hibrido': 'mini_plus_4o',
                'tokens_mini': tokens_inicial,
                'tokens_4o': tokens_refinamento
            }

        # ==================================================================
        # 💎 PROFESSIONAL - SISTEMA HÍBRIDO INTELIGENTE PREMIUM
        # ==================================================================
        elif tipo == 'professional':
            modelo_inicial = 'gpt-4o-mini'
            max_tokens_inicial = config['max_tokens']
            
            system_prompt_base = f"""Você é NatanAI, assistente premium para clientes PROFESSIONAL.

**SOBRE SEU PLANO PROFESSIONAL:**
Você é um cliente PREMIUM TOP TIER! 💎✨

**BENEFÍCIOS EXCLUSIVOS DO SEU PLANO PROFESSIONAL:**

💬 **Comunicação Comigo (NatanAI):**
- 5.000 mensagens/mês (vs 1.250 do Starter!)
- Conversas SALVAS e persistentes
- Histórico completo ilimitado
- Respostas AVANÇADAS e técnicas detalhadas
- Prioridade no processamento

🌐 **Seu Site Profissional PREMIUM:**
- Páginas ILIMITADAS (sem restrição!)
- Design 100% PERSONALIZADO (único, exclusivo)
- Animações e interatividade avançadas
- Hospedagem PREMIUM incluída por 1 ano
- Domínio personalizado (.com, .com.br, etc) INCLUSO
- SEO AVANÇADO com keywords estratégicas
- Blog completo (opcional)
- E-commerce/Loja Virtual (opcional)
- Integração de APIs customizadas
- Sistema de CMS para você editar conteúdo (opcional)
- Formulários avançados com validação
- Google Analytics + Search Console integrados
- Certificado SSL premium
- CDN para velocidade global
- Backup automático diário
- Uso comercial TOTAL

⚡ **Desenvolvimento Premium:**
- Prazo: 10-30 dias (conforme complexidade)
- 5 REVISÕES inclusas (vs 2 do Starter)
- Reuniões de alinhamento via vídeo
- Prototipação prévia (mockups)
- Testes em múltiplos dispositivos
- Tecnologias de ponta: React, Next.js, TypeScript, Tailwind CSS
- Performance máxima otimizada
- Código limpo e documentado

🤖 **IA Integrada no Site (OPCIONAL):**
- Chatbot com IA (GPT-4) no seu site
- Respostas automatizadas personalizadas
- Treinamento específico para seu negócio
- Integração com WhatsApp (opcional)

📊 **Dashboard e Ferramentas PREMIUM:**
- Acesso completo ao dashboard NatanSites
- Página "Meus Sites" com todos seus projetos
- Suporte PRIORITÁRIO direto com Natan
- Estatísticas avançadas de uso
- Configurações de personalização total
- Relatórios mensais de performance

🎨 **Design e Personalização:**
- Paleta de cores exclusiva para sua marca
- Tipografia profissional selecionada
- Logotipo integrado (se tiver)
- Identidade visual consistente
- UX/UI design premium
- Micro-interações e animações suaves
- Loading screens personalizadas

🔧 **Integrações Disponíveis:**
- APIs de pagamento (Stripe, PayPal, Mercado Pago)
- CRMs (HubSpot, Salesforce, RD Station)
- Email marketing (Mailchimp, SendGrid)
- Redes sociais (Facebook, Instagram, LinkedIn)
- Google Maps, YouTube, Vimeo
- Webhooks customizados
- Qualquer API REST ou GraphQL

💰 **Investimento:**
- Setup único: R$530,00 (pago uma vez)
- Mensalidade: R$79,99/mês
- Contrato: 1 ano
- Renovação: Negociável após 1 ano
- ROI: Site paga a si mesmo rapidamente

**COMPARAÇÃO COM OUTROS PLANOS:**

🎁 FREE (R$0,00):
- 100 mensagens/semana
- SEM site profissional
- SEM uso comercial
- SEM hospedagem
- Conversas NÃO salvas

🌱 STARTER (R$320 + R$39,99/mês):
- 1.250 mensagens/mês
- Até 5 páginas apenas
- Design padrão moderno
- SEO básico
- 2 revisões
- SEM blog ou e-commerce

💎 PROFESSIONAL (VOCÊ - R$530 + R$79,99/mês):
- 5.000 mensagens/mês (4x mais!)
- Páginas ILIMITADAS
- Design 100% PERSONALIZADO
- SEO AVANÇADO
- Blog/E-commerce SIM
- IA integrada opcional
- 5 revisões inclusas
- Suporte PRIORITÁRIO
- Integrações ilimitadas

**STACK TECNOLÓGICO AVANÇADO:**
- Frontend: React, Next.js, Vue.js, TypeScript, Tailwind CSS, Framer Motion
- Backend: Node.js, Python, Express.js, Django, Flask, APIs RESTful/GraphQL
- Mobile: React Native (apps iOS/Android nativos)
- Banco de Dados: Supabase, PostgreSQL, MongoDB, MySQL, Firebase
- IA: OpenAI GPT-4, Claude, LangChain, integração completa
- SEO: Schema.org, sitemap.xml, robots.txt, Open Graph, meta tags avançadas
- DevOps: Git, CI/CD, Vercel, Netlify, AWS, Google Cloud
- Analytics: Google Analytics 4, Search Console, Hotjar, heatmaps

**PROCESSO DE DESENVOLVIMENTO PREMIUM:**
1. **Briefing Detalhado** (reunião de 1-2h):
   - Objetivos do negócio
   - Público-alvo
   - Referências visuais
   - Funcionalidades desejadas

2. **Prototipação** (3-5 dias):
   - Wireframes
   - Mockups de design
   - Revisão e aprovação

3. **Desenvolvimento** (10-30 dias):
   - Codificação frontend
   - Backend e integrações
   - Testes em múltiplos dispositivos

4. **Revisões** (até 5 inclusas):
   - Ajustes de design
   - Correções de funcionalidade
   - Refinamentos de UX

5. **Entrega e Treinamento**:
   - Site 100% funcional online
   - Documentação completa
   - Treinamento de uso (se CMS)
   - Suporte pós-lançamento

6. **Suporte Contínuo**:
   - Atualizações de segurança
   - Backups automáticos
   - Monitoramento de performance

**CONTATO PRIORITÁRIO:**
- 📱 WhatsApp: (21) 99282-6074 (atendimento prioritário)
- 📧 Email: borgesnatan09@gmail.com
- 💬 Suporte: Página dedicada na plataforma (resposta rápida)

**PORTFÓLIO PREMIUM DO NATAN:**
- Espaço Familiares (espacofamiliares.com.br) - Site institucional
- NatanSites (natansites.com.br) - Landing page profissional
- MathWork (mathworkftv.netlify.app) - Aplicação web
- TAF Sem Tabu (tafsemtabu.com.br) - Blog + E-commerce

**DIFERENCIAIS PROFESSIONAL:**
✓ Código proprietário e otimizado
✓ Performance de loading < 2 segundos
✓ Score Google PageSpeed > 90
✓ Mobile-first design
✓ Acessibilidade (WCAG 2.1)
✓ SEO técnico avançado
✓ Segurança reforçada
✓ Escalabilidade garantida

REGRAS:
- {config['instrucao']}
- Seja técnico quando apropriado
- Destaque TODOS os benefícios premium
- Sem asteriscos ou formatação markdown
- Tom profissional, consultivo e premium
- Faça o cliente se sentir VIP

Você está conversando com: {nome} (Cliente PROFESSIONAL - Premium TOP TIER 💎)"""

            messages_inicial = [{"role": "system", "content": system_prompt_base}]
            messages_inicial.extend(historico_memoria[-5:])
            messages_inicial.append({"role": "user", "content": mensagem})
            
            response_inicial = client.chat.completions.create(
                model=modelo_inicial,
                messages=messages_inicial,
                max_tokens=max_tokens_inicial,
                temperature=0.7
            )
            
            resposta_inicial = response_inicial.choices[0].message.content.strip()
            tokens_inicial = response_inicial.usage.total_tokens
            
            # Detecta refinamento (Professional tem critérios mais amplos)
            msg_lower = mensagem.lower().strip()
            
            keywords_refinamento = [
                'como funciona', 'me explica', 'detalhes', 'completo', 'diferença', 'comparar',
                'melhor', 'processo', 'etapas', 'tecnologia', 'stack', 'framework', 'prazo',
                'seo', 'hospedagem', 'blog', 'e-commerce', 'design', 'personalização', 'ia',
                'inteligência artificial', 'api', 'integração', 'cms', 'performance', 'otimização',
                'mobile', 'responsivo', 'analytics', 'conversão', 'landing page', 'checkout',
                'pagamento', 'stripe', 'crm', 'automação', 'webhook', 'graphql', 'react',
                'next.js', 'typescript', 'advanced', 'avançado', 'custom', 'customização'
            ]
            
            precisa_refinamento = any(kw in msg_lower for kw in keywords_refinamento)
            
            if not precisa_refinamento or len(resposta_inicial.split()) < 30:
                resposta_final = limpar_formatacao_markdown(resposta_inicial)
                
                return {
                    'resposta': resposta_final,
                    'tokens_usados': tokens_inicial,
                    'tokens_entrada': response_inicial.usage.prompt_tokens,
                    'tokens_saida': response_inicial.usage.completion_tokens,
                    'modelo_usado': f'{modelo_inicial} (direto)',
                    'cached': False,
                    'categoria': categoria,
                    'sistema_hibrido': 'mini_apenas'
                }
            
            # Refinamento com GPT-4O (Professional tem tokens maiores)
            modelo_refinamento = 'gpt-4o'
            max_tokens_refinamento = min(config['max_tokens'] * 2, 800)
            
            prompt_refinamento = f"""Você é NatanAI em modo de refinamento PREMIUM. Melhore e expanda esta resposta com máximo de detalhes técnicos e profissionalismo.

RESPOSTA INICIAL (gpt-4o-mini):
{resposta_inicial}

PERGUNTA DO USUÁRIO:
{mensagem}

CONTEXTO: Cliente Professional (plano premium R$79,99/mês) - TOP TIER 💎

INSTRUÇÕES:
- Mantenha TODAS as informações corretas da resposta inicial
- Adicione DETALHES TÉCNICOS AVANÇADOS
- Seja CONSULTIVO e demonstre expertise
- Mencione benefícios premium quando relevante
- {config['instrucao']} (pode ser extenso, cliente premium merece)
- Sem asteriscos ou formatação markdown
- Tom profissional, consultivo e premium
- Faça o cliente sentir que tem o MELHOR serviço

MELHORE E EXPANDA A RESPOSTA PREMIUM:"""

            messages_refinamento = [{"role": "system", "content": prompt_refinamento}]
            
            response_refinamento = client.chat.completions.create(
                model=modelo_refinamento,
                messages=messages_refinamento,
                max_tokens=max_tokens_refinamento,
                temperature=0.7
            )
            
            resposta_refinada = response_refinamento.choices[0].message.content.strip()
            tokens_refinamento = response_refinamento.usage.total_tokens
            tokens_total = tokens_inicial + tokens_refinamento
            
            resposta_final = limpar_formatacao_markdown(resposta_refinada)
            
            return {
                'resposta': resposta_final,
                'tokens_usados': tokens_total,
                'tokens_entrada': response_inicial.usage.prompt_tokens + response_refinamento.usage.prompt_tokens,
                'tokens_saida': response_inicial.usage.completion_tokens + response_refinamento.usage.completion_tokens,
                'modelo_usado': f'híbrido premium ({modelo_inicial} → {modelo_refinamento})',
                'cached': False,
                'categoria': categoria,
                'sistema_hibrido': 'mini_plus_4o_premium',
                'tokens_mini': tokens_inicial,
                'tokens_4o': tokens_refinamento
            }

        # ==================================================================
        # 👑 ADMIN - GPT-4O PURO + CONHECIMENTO TOTAL DO SISTEMA (CORRIGIDO)
        # ==================================================================
        elif tipo == 'admin':
            modelo = 'gpt-4o'
            max_tokens = 1000
            
            system_prompt = f"""Você é NatanAI no modo ADMINISTRADOR para Natan (criador da plataforma).

**VOCÊ TEM ACESSO TOTAL E IRRESTRITO:**
- Modelo: GPT-4O puro (mais poderoso)
- Mensagens: ILIMITADAS
- Conhecimento: COMPLETO da plataforma + mundo
- Capacidades: Análise, debugging, melhorias, estatísticas

**CONHECIMENTO COMPLETO DA PLATAFORMA NATANSITES:**

🏗️ **ARQUITETURA DO SISTEMA:**

**Frontend:**
- HTML5, CSS3, JavaScript nativo
- Páginas: home.html, login.html, dashboard.html, websites.html, suporte.html, natanai.html, settings.html
- Páginas de cadastro: starter.html, professional.html
- CSS: Space Grotesk (texto), Sora (títulos)
- Tema: Light mode (padrão) + Dark mode (dourado #D4AF37)
- Responsivo: Mobile-first com breakpoints 480px, 768px, 1024px

**Backend:**
- Python Flask (main.py)
- API REST: /api/chat (NatanAI), /api/health, /ping
- Deploy: Render.com (auto-deploy via GitHub)
- Auto-ping: Mantém servidor ativo (5 em 5 minutos)

**Banco de Dados (Supabase):**
- PostgreSQL hospedado no Supabase
- Tabelas principais:
  * `user_accounts`: Dados dos usuários (user_id, user_email, plan_name, plan_type, is_suspended, account_expires_at, first_login_at, dashboard_visits, last_visit_at, created_at)
  * `user_settings`: Configurações personalizadas (user_id, settings JSON, created_at, updated_at)
  * `user_websites`: Sites cadastrados por cliente (id, user_id, user_email, site_name, site_url, image_url, created_at, created_by)
  * `support_messages`: Sistema de suporte (id, sender_email, sender_name, receiver_email, message, read, created_at)
  * `chat_sessions`: Sessões de chat da NatanAI (id, user_id, title, is_active, message_count, created_at, updated_at)
  * `chat_messages`: Mensagens do chat NatanAI (id, session_id, user_id, content, is_user, metadata JSON, created_at)
  * `free_access_config`: Configuração do acesso gratuito permanente (id, is_active, started_at, expires_at, free_account_email, free_account_password, free_account_user_id, created_by, updated_at)
  * `free_access_users`: Usuários usando acesso gratuito (id, user_id, user_email, joined_at, expires_at, is_expired)

**Autenticação:**
- Supabase Auth (email/senha)
- Row Level Security (RLS) ativo
- Admin: natan@natandev.com
- Conta Free padrão: free@natandev.com / natanfree2025

**Sistema de Planos:**
1. **FREE (R$ 0,00 - Permanente)**:
   - Acesso completo ao dashboard
   - NatanAI: 100 mensagens/semana
   - Sites apenas para teste/portfólio
   - SEM uso comercial
   - SEM hospedagem profissional
   - SEM domínio personalizado
   - Conversas NÃO salvas (temporárias)
   - Marca d'água presente
   - Contrato: Permanente enquanto ativo

2. **STARTER (R$ 320 setup + R$ 39,99/mês)**:
   - NatanAI: 1.250 mensagens/mês
   - Site até 5 páginas
   - Design moderno responsivo
   - Hospedagem incluída 1 ano
   - SEO básico otimizado
   - Uso comercial PERMITIDO
   - 2 revisões inclusas
   - Conversas salvas e persistentes
   - Suporte 24/7 via plataforma
   - Contrato: 1 ano

3. **PROFESSIONAL (R$ 530 setup + R$ 79,99/mês)**:
   - NatanAI: 5.000 mensagens/mês
   - Páginas ILIMITADAS
   - Design 100% PERSONALIZADO
   - Hospedagem + Domínio inclusos 1 ano
   - SEO AVANÇADO com keywords
   - Blog/E-commerce opcionais
   - IA integrada no site (opcional)
   - 5 revisões inclusas
   - Suporte PRIORITÁRIO 24/7
   - Conversas salvas e persistentes
   - Uso comercial PERMITIDO
   - Contrato: 1 ano

**Fluxo de Cadastro:**
1. Usuário preenche formulário (starter.html ou professional.html)
2. Dados: Nome completo, Data de nascimento, CPF
3. Pagamento via PIX (QR Code ou código copia-e-cola)
4. Email enviado via EmailJS para borgesnatan09@gmail.com
5. Admin cria conta manualmente em settings.html (seção admin)
6. Prazo: 10 minutos a 2 horas
7. Cliente recebe confirmação e credenciais

**Sistema de Acesso Gratuito Permanente:**
- Admin pode ativar/desativar em settings.html
- Quando ATIVO:
  * Cria automaticamente conta free@natandev.com
  * Senha padrão: natanfree2025
  * Botão "Acessar Gratuitamente" aparece em login.html
  * Qualquer pessoa pode usar SEM cadastro
  * Dashboard completo + IA + Suporte liberados
  * Sites apenas para teste (sem uso comercial)
  * Conversas NÃO são salvas (desaparecem ao sair)
  * Permanece ativo até admin desativar manualmente
- Quando INATIVO:
  * Botão de acesso gratuito some
  * Conta free é deletada automaticamente
  * Apenas clientes pagos/cadastrados podem acessar

**Funcionalidades Admin (settings.html):**
- Criar novas contas (email, senha, nome, plano)
- Buscar e gerenciar contas existentes
- Reativar contas suspensas (adiciona +1 ano)
- Suspender contas manualmente
- Adicionar sites aos clientes (nome, URL, imagem)
- Listar e remover sites cadastrados
- Ativar/Desativar acesso gratuito permanente
- Visualizar estatísticas completas

**NatanAI (natanai.html):**
- Sistema híbrido inteligente:
  * FREE: gpt-4o-mini direto (básico)
  * STARTER/PROFESSIONAL: gpt-4o-mini → gpt-4o (refinamento quando necessário)
  * ADMIN: gpt-4o puro (ilimitado)
- Detecção automática de categoria:
  * Casual: respostas curtas
  * Técnica: detalhadas com contexto
  * Complexa: máximo detalhamento
- Sistema de sessões:
  * FREE: Conversas temporárias (não salvas)
  * STARTER/PROFESSIONAL: Conversas salvas e persistentes
  * Histórico completo acessível
  * Criação de novas sessões
  * Renomear/deletar conversas
- Contexto completo da plataforma incluído
- Validação anti-alucinação ativa
- Metadata de cada resposta (modelo, tokens, tipo usuário)

**Suporte (suporte.html):**
- Sistema de mensagens diretas com admin
- Clientes FREE NÃO têm acesso (apenas dashboard/IA)
- Clientes PAID: Chat direto com Natan
- Admin vê lista de todas as conversas
- Realtime via Supabase + Polling de backup
- Notificações de mensagens não lidas
- Histórico completo salvo no banco

**Dashboard (dashboard.html):**
- Cards informativos:
  * Tempo de uso (calculado desde first_login_at)
  * Plano atual (Free/Starter/Professional)
  * Sites criados (contagem automática)
  * Visitas ao dashboard (contador incremental)
- Alertas:
  * Acesso gratuito ativo (quando FREE)
  * Conta suspensa (se expired ou suspended)
  * Plano expirando (últimos 30 dias)
- Status da conta:
  * FREE: Card verde com "∞ Permanente" ou dias restantes
  * PAID: Cálculo automático de tempo usado/restante
  * SUSPENDED: Card vermelho com alerta

**Websites (websites.html):**
- Lista todos os sites do usuário
- Busca na tabela `user_websites` por user_email
- Empty states diferentes:
  * FREE: Botão WhatsApp para contratar
  * PAID sem sites: Botão para Suporte
- Cards com imagem, nome, URL e botão "Visitar Site"
- Carregamento dinâmico via Supabase

**Settings (settings.html):**
- Configurações gerais:
  * Tema escuro (dark mode com ouro #D4AF37)
  * Sons (digitação, envio, apagar, clique)
  * Notificações desktop
  * Economia de dados
- Sincronização automática:
  * localStorage (local)
  * Supabase user_settings (remoto)
  * Polling 1s para sincronizar entre abas
- Seções Admin (apenas para natan@natandev.com):
  * Criar contas
  * Gerenciar contas
  * Adicionar/remover sites
  * Controlar acesso gratuito permanente

**Login (login.html):**
- Autenticação via Supabase Auth
- Botão "Acessar Gratuitamente" (apenas se FREE ativo)
- Login automático com free@natandev.com ao clicar no botão
- Verificação de email_confirmed (contornada se necessário)
- Redirecionamento para dashboard.html após login

**Proteções de Segurança:**
- Verificação de plano em TODAS as páginas
- FREE bloqueado de: suporte.html
- Redirecionamento automático se acesso negado
- Admin tem acesso TOTAL e IRRESTRITO sempre
- RLS no Supabase protege dados entre usuários

**Tecnologias Stack:**
- Frontend: HTML5, CSS3, JavaScript vanilla
- Backend: Python Flask (main.py - Render.com)
- Banco: Supabase (PostgreSQL + Auth + Realtime)
- IA: OpenAI API (gpt-4o-mini + gpt-4o)
- Email: EmailJS (cadastros)
- Hospedagem: Render (backend), Netlify/Vercel (frontend)

**Endpoints API Python:**
- POST /api/chat: Processa mensagens da NatanAI
- GET /ping: Health check (mantém servidor ativo)
- GET /api/health: Status da API

**Regras de Comportamento Admin:**
- Acesso total e irrestrito
- Conhecimento completo do sistema
- Pode criar/modificar/deletar qualquer recurso
- Respostas técnicas e detalhadas
- Ajuda com debugging e melhorias
- Análise de logs e estatísticas

**Informações de Contato:**
- WhatsApp: (21) 99282-6074
- Email: borgesnatan09@gmail.com
- Site: natansites.com.br

**Portfólio:**
- espacofamiliares.com.br
- natansites.com.br
- mathworkftv.netlify.app
- tafsemtabu.com.br

REGRAS ADMIN:
- Respostas COMPLETAS e BEM FUNDAMENTADAS
- Acesso total ao código-fonte e logs
- Pode sugerir melhorias e otimizações
- Conhecimento técnico profundo
- {config['instrucao']} (pode ser extenso se necessário)
- Sem asteriscos ou formatação markdown
- Tom técnico, direto e profissional

Você está conversando com: Natan (ADMIN - Criador da Plataforma)"""

            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(historico_memoria[-10:])
            messages.append({"role": "user", "content": mensagem})
            
            response = client.chat.completions.create(
                model=modelo,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.7
            )
            
            resposta = response.choices[0].message.content.strip()
            resposta = limpar_formatacao_markdown(resposta)
            
            # ✅ CORREÇÃO: Removida a verificação de precisa_search (variável indefinida)
            # A detecção de necessidade de web search foi removida pois não está implementada
            
            return {
                'resposta': resposta,
                'tokens_usados': response.usage.total_tokens,
                'tokens_entrada': response.usage.prompt_tokens,
                'tokens_saida': response.usage.completion_tokens,
                'modelo_usado': modelo,
                'cached': False,
                'categoria': categoria
            }
        
        # Fallback
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

def verificar_openai():
    try:
        if not OPENAI_API_KEY or len(OPENAI_API_KEY) < 20:
            return False
        if client is None:
            return False
        return True
    except:
        return False

# =============================================================================
# 📨 ENDPOINT PRINCIPAL - /api/chat (CORRIGIDO)
# =============================================================================

@app.route('/api/chat', methods=['POST'])
def chat():
    try:
        data = request.get_json()
        mensagem = data.get('message', '').strip()
        token = request.headers.get('Authorization', '')
        
        print("\n" + "="*80)
        print("📨 NOVA REQUISIÇÃO /api/chat")
        print("="*80)
        print(f"📝 Mensagem: {mensagem[:50]}...")
        print(f"🔐 Token presente: {bool(token)}")
        print(f"📦 Body completo: {data}")
        
        if not mensagem:
            print("❌ Mensagem vazia")
            return jsonify({'error': 'Mensagem vazia'}), 400
        
        # 🆕 NOVA LÓGICA: Aceita user_data do body OU busca via token
        user_data_from_body = data.get('user_data')
        
        if user_data_from_body:
            # Frontend enviou user_data completo no body
            print("✅ Usando user_data do body")
            user_info = type('obj', (object,), {
                'id': user_data_from_body.get('user_id'),
                'email': user_data_from_body.get('email'),
                'user_metadata': {'name': user_data_from_body.get('name', 'Cliente')}
            })()
            
            user_data = {
                'user_id': user_data_from_body.get('user_id'),
                'email': user_data_from_body.get('email'),
                'plan': user_data_from_body.get('plan', 'starter'),
                'plan_type': user_data_from_body.get('plan_type', 'paid'),
                'user_name': user_data_from_body.get('name'),
                'name': user_data_from_body.get('name')
            }
            
        else:
            # Fallback: buscar via token (comportamento antigo)
            print("🔐 Buscando via token Supabase")
            user_info = verificar_token_supabase(token)
            if not user_info:
                print("❌ Token inválido")
                return jsonify({'error': 'Não autenticado'}), 401
            
            user_data = obter_dados_usuario_completos(user_info.id)
            if not user_data:
                print("❌ Usuário não encontrado no banco")
                return jsonify({'error': 'Usuário não encontrado'}), 404
        
        print(f"✅ User ID: {user_data.get('user_id', 'N/A')[:8]}...")
        print(f"✅ Email: {user_data.get('email', 'N/A')}")
        print(f"✅ Plan: {user_data.get('plan', 'N/A')}")
        print(f"✅ Plan Type: {user_data.get('plan_type', 'N/A')}")
        
        # 👤 Dados do usuário
        tipo_usuario = determinar_tipo_usuario(user_data, user_info)
        user_id = obter_user_id(user_info, user_data)
        tipo = tipo_usuario['tipo']
        nome = tipo_usuario['nome_real']
        
        print(f"👤 Tipo: {tipo} | Nome: {nome}")
        
        # 📊 Verifica limite de mensagens
        pode_enviar, msgs_usadas, limite, msgs_restantes = verificar_limite_mensagens(user_id, tipo)
        
        print(f"📊 Mensagens: {msgs_usadas}/{limite} (Restantes: {msgs_restantes})")
        
        if not pode_enviar:
            print("🚫 Limite de mensagens atingido")
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
        
        print(f"🧠 Histórico: {len(historico_memoria)} mensagens em contexto")
        
        # 🤖 Processa com OpenAI
        print("🤖 Processando com OpenAI...")
        resultado = processar_mensagem_openai(mensagem, tipo_usuario, historico_memoria)
        
        resposta = resultado['resposta']
        tokens_usados = resultado['tokens_usados']
        modelo_usado = resultado['modelo_usado']
        
        print(f"✅ Resposta gerada: {len(resposta)} caracteres")
        print(f"📊 Tokens usados: {tokens_usados}")
        print(f"🤖 Modelo: {modelo_usado}")
        
        # 🛡️ Validação anti-alucinação
        valido, problemas = validar_resposta(resposta, tipo)
        if not valido:
            print(f"⚠️ Resposta inválida: {problemas}")
            resposta = f"Desculpe {nome}, detectei informações imprecisas na minha resposta. Por favor, entre em contato: WhatsApp (21) 99282-6074"
        
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
        
        # 📊 Atualiza para próxima verificação
        pode_enviar_prox, msgs_usadas_prox, limite_prox, msgs_restantes_prox = verificar_limite_mensagens(user_id, tipo)
        
        print("✅ Resposta enviada com sucesso")
        print("="*80 + "\n")
        
        # 📤 Resposta final
        return jsonify({
            'response': resposta,
            'user_name': nome,
            'user_type': tipo_usuario['nome_display'],
            'plan': tipo_usuario['plano'],
            'modelo_usado': modelo_usado,
            'tokens_usados': tokens_usados,
            'categoria': resultado.get('categoria', 'geral'),
            'tipo_processamento': resultado.get('sistema_hibrido', 'N/A'),
            'web_search_sugerido': resultado.get('web_search_sugerido', False),
            'mensagens_usadas': msgs_usadas_prox,
            'limite_total': limite_prox if limite_prox != float('inf') else 'ilimitado',
            'mensagens_restantes': msgs_restantes_prox if msgs_restantes_prox != float('inf') else 'ilimitado',
            'limite_atingido': False,
            'timestamp': datetime.now().isoformat()
        })
    
    except Exception as e:
        print("="*80)
        print("❌ ERRO NO ENDPOINT /api/chat")
        print("="*80)
        print(f"Tipo: {type(e).__name__}")
        print(f"Mensagem: {str(e)}")
        print(f"Stack trace:")
        import traceback
        traceback.print_exc()
        print("="*80 + "\n")
        
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
        "sistema": "NatanAI v8.1 - Sistema Híbrido Otimizado",
        "versao": "8.1",
        "openai": verificar_openai(),
        "supabase": supabase is not None,
        "memoria": {
            "usuarios_ativos": usuarios_ativos,
            "total_mensagens_memoria": total_mensagens,
            "max_por_usuario": MAX_MENSAGENS_MEMORIA
        },
        "modelos_por_plano": {
            "free": "gpt-4o-mini (básico)",
            "starter": "híbrido inteligente (gpt-4o-mini + gpt-4o quando necessário)",
            "professional": "híbrido inteligente (gpt-4o-mini + gpt-4o quando necessário)",
            "admin": "gpt-4o puro + web search"
        },
        "economia_sistema_hibrido": {
            "starter_professional": "Usa gpt-4o-mini como base e só refina com gpt-4o quando detecta necessidade",
            "economia_estimada": "60-80% comparado a usar só gpt-4o",
            "criterios_refinamento": ["perguntas complexas", "explicações técnicas", "comparações detalhadas"]
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
            "sistema_hibrido_inteligente_v8_1",
            "free_gpt4omini_basico",
            "starter_hibrido_otimizado",
            "professional_hibrido_otimizado",
            "admin_gpt4o_puro",
            "deteccao_automatica_refinamento",
            "economia_maxima_tokens",
            "memoria_inteligente",
            "controle_limites_por_plano",
            "resposta_alternativa_sem_ia",
            "validacao_anti_alucinacao"
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
