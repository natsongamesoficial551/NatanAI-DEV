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
# 🎯 SISTEMA DE OTIMIZAÇÃO DE TOKENS v7.4
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

# ============================================
# 🆕 SISTEMA DE LEITURA DO SUPABASE v7.5
# ============================================
CACHE_SUPABASE = {
    'site_content': {'data': None, 'ultima_atualizacao': None},
    'plataforma_info': {'data': None, 'ultima_atualizacao': None},
    'repo_content': {'data': None, 'ultima_atualizacao': None},
    'ia_memoria': {'data': None, 'ultima_atualizacao': None}
}
INTERVALO_ATUALIZACAO_CACHE = 300  # 5 minutos
cache_lock = threading.Lock()

def carregar_dados_supabase(tabela):
    try:
        if not supabase:
            return None
        
        # 🆕 ORDENA POR DATA MAIS RECENTE
        if tabela == 'site_content':
            # Para cada página, pega só o registro mais recente
            response = supabase.table(tabela)\
                .select('*')\
                .order('scraped_at', desc=True)\
                .execute()
        else:
            response = supabase.table(tabela).select('*').execute()
        
        if response.data:
            # 🆕 Remove duplicatas, mantém só mais recente
            if tabela == 'site_content':
                dados_unicos = {}
                for item in response.data:
                    page = item.get('page_name')
                    if page not in dados_unicos:
                        dados_unicos[page] = item
                
                response.data = list(dados_unicos.values())
            
            with cache_lock:
                CACHE_SUPABASE[tabela]['data'] = response.data
                CACHE_SUPABASE[tabela]['ultima_atualizacao'] = agora
            
            return response.data
        
        # Busca dados do Supabase
        print(f"🔄 Carregando dados da tabela: {tabela}")
        response = supabase.table(tabela).select('*').execute()
        
        if response.data:
            with cache_lock:
                CACHE_SUPABASE[tabela]['data'] = response.data
                CACHE_SUPABASE[tabela]['ultima_atualizacao'] = agora
            
            print(f"✅ {len(response.data)} registros carregados de {tabela}")
            return response.data
        else:
            print(f"⚠️ Nenhum dado encontrado em {tabela}")
            return None
            
    except Exception as e:
        print(f"❌ Erro ao carregar {tabela}: {e}")
        return None

def formatar_site_content(dados):
    """Formata dados da tabela site_content para o prompt"""
    if not dados:
        return ""
    
    texto = "\n📄 CONTEÚDO DO SITE (natansites.com.br):\n\n"
    
    for item in dados[:20]:  # Limita a 20 páginas para não sobrecarregar
        page = item.get('page_name', 'Desconhecida')
        content = item.get('content', '')
        
        if content:
            # 🆕 AUMENTA LIMITE DE 500 PARA 3000 CARACTERES
            content_resumido = content[:3732] + "..." if len(content) > 3732 else content
            texto += f"Página: {page}\n{content_resumido}\n\n"
    
    return texto

def formatar_plataforma_info(dados):
    """Formata dados da tabela plataforma_info para o prompt"""
    if not dados:
        return ""
    
    texto = "\n💼 INFORMAÇÕES DA PLATAFORMA:\n\n"
    
    for item in dados:
        secao = item.get('secao', 'Desconhecida')
        dados_secao = item.get('dados', {})
        
        if secao == 'planos' and isinstance(dados_secao, dict):
            planos = dados_secao.get('planos', [])
            if planos:
                texto += "PLANOS DISPONÍVEIS:\n"
                for plano in planos:
                    nome = plano.get('nome', '')
                    preco = plano.get('preco', '')
                    if nome and preco:
                        texto += f"- {nome}: {preco}\n"
                texto += "\n"
        
        elif secao == 'promocoes' and isinstance(dados_secao, dict):
            promo_texto = dados_secao.get('texto', '')
            if promo_texto:
                texto += f"PROMOÇÃO ATIVA:\n{promo_texto[:5000]}\n\n"
        
        elif secao == 'contato' and isinstance(dados_secao, dict):
            whatsapp = dados_secao.get('whatsapp', '')
            email = dados_secao.get('email', '')
            if whatsapp or email:
                texto += "CONTATO:\n"
                if whatsapp:
                    texto += f"WhatsApp: {whatsapp}\n"
                if email:
                    texto += f"Email: {email}\n"
                texto += "\n"
    
    return texto

def formatar_repo_content(dados):
    """Formata dados da tabela repo_content para o prompt"""
    if not dados:
        return ""
    
    texto = "\n🗂️ REPOSITÓRIO GITHUB:\n\n"
    
    # Prioriza arquivos importantes
    arquivos_importantes = ['README.md', 'package.json', 'index.html']
    
    for item in dados[:10]:  # Limita a 10 arquivos
        file_path = item.get('file_path', '')
        content = item.get('content', '')
        
        # Prioriza arquivos importantes
        if any(arq in file_path for arq in arquivos_importantes):
            if content:
                content_resumido = content[:4000] + "..." if len(content) > 4000 else content
                texto += f"Arquivo: {file_path}\n{content_resumido}\n\n"
    
    return texto

def formatar_ia_memoria(dados):
    """Formata dados da tabela ia_memoria para o prompt"""
    if not dados:
        return ""
    
    texto = "\n🧠 MEMÓRIA DA IA (Atualizações Recentes):\n\n"
    
    # Ordena por data (mais recentes primeiro)
    dados_ordenados = sorted(
        dados, 
        key=lambda x: x.get('criado_em', ''), 
        reverse=True
    )
    
    for item in dados_ordenados[:15]:  # Últimas 15 memórias
        texto_memoria = item.get('texto', '')
        origem = item.get('origem', 'desconhecida')
        
        if texto_memoria:
            texto += f"[{origem}] {texto_memoria}\n"
    
    texto += "\n"
    return texto

def gerar_contexto_supabase():
    """Gera contexto completo do Supabase para o prompt"""
    contexto = "\n" + "="*80 + "\n"
    contexto += "📊 DADOS ATUALIZADOS DO SITE E PLATAFORMA\n"
    contexto += "="*80 + "\n"
    
    # Carrega e formata cada tabela
    site_content = carregar_dados_supabase('site_content')
    if site_content:
        contexto += formatar_site_content(site_content)
    
    plataforma_info = carregar_dados_supabase('plataforma_info')
    if plataforma_info:
        contexto += formatar_plataforma_info(plataforma_info)
    
    repo_content = carregar_dados_supabase('repo_content')
    if repo_content:
        contexto += formatar_repo_content(repo_content)
    
    ia_memoria = carregar_dados_supabase('ia_memoria')
    if ia_memoria:
        contexto += formatar_ia_memoria(ia_memoria)
    
    contexto += "="*80 + "\n"
    contexto += "⚠️ USE ESTAS INFORMAÇÕES ATUALIZADAS DO SITE REAL!\n"
    contexto += "="*80 + "\n\n"
    
    return contexto

# Thread de atualização automática do cache
def thread_atualizacao_cache():
    """Atualiza cache do Supabase periodicamente"""
    while True:
        try:
            time.sleep(INTERVALO_ATUALIZACAO_CACHE)
            print(f"\n🔄 Atualizando cache Supabase... ({datetime.now().strftime('%H:%M:%S')})")
            
            for tabela in ['site_content', 'plataforma_info', 'repo_content', 'ia_memoria']:
                carregar_dados_supabase(tabela)
            
            print("✅ Cache atualizado com sucesso!\n")
        except Exception as e:
            print(f"⚠️ Erro na atualização do cache: {e}")

# Inicializa Supabase
supabase: Client = None
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase conectado")
    
    # Carrega dados iniciais
    print("🔄 Carregando dados iniciais do Supabase...")
    for tabela in ['site_content', 'plataforma_info', 'repo_content', 'ia_memoria']:
        carregar_dados_supabase(tabela)
    
    # Inicia thread de atualização
    threading.Thread(target=thread_atualizacao_cache, daemon=True).start()
    print("✅ Sistema de cache Supabase iniciado")
    
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

Para continuar, contrate um dos planos:

STARTER - R$320 (setup) + R$39,99/mês
1.250 mensagens/mês + site profissional

PROFESSIONAL - R$530 (setup) + R$79,99/mês
5.000 mensagens/mês + recursos avançados

WhatsApp: (21) 99282-6074"""
    
    elif tipo == 'starter':
        return f"""Você atingiu o limite de {limite} mensagens do plano Starter.

Para mais mensagens:
1. Upgrade para Professional (5.000 msgs/mês)
2. Aguarde renovação mensal

Acesse Suporte para ajuda!"""
    
    elif tipo == 'professional':
        return f"""Limite de {limite} mensagens atingido no plano Professional.

Para soluções personalizadas, acesse a página Suporte!"""
    
    return "Limite de mensagens atingido. Entre em contato com o suporte."

# =============================================================================
# 📊 SISTEMA DE CONTAGEM DE TOKENS
# =============================================================================

def registrar_tokens_usados(user_id, tokens_entrada, tokens_saida, tokens_total):
    """Registra tokens usados por um usuário"""
    with tokens_lock:
        if user_id not in CONTADOR_TOKENS:
            CONTADOR_TOKENS[user_id] = {
                'total_entrada': 0,
                'total_saida': 0,
                'total_geral': 0,
                'mensagens_processadas': 0
            }
        
        CONTADOR_TOKENS[user_id]['total_entrada'] += tokens_entrada
        CONTADOR_TOKENS[user_id]['total_saida'] += tokens_saida
        CONTADOR_TOKENS[user_id]['total_geral'] += tokens_total
        CONTADOR_TOKENS[user_id]['mensagens_processadas'] += 1

def obter_estatisticas_tokens(user_id):
    """Retorna estatísticas de tokens de um usuário"""
    with tokens_lock:
        if user_id not in CONTADOR_TOKENS:
            return {
                'total_entrada': 0,
                'total_saida': 0,
                'total_geral': 0,
                'mensagens_processadas': 0,
                'media_por_mensagem': 0
            }
        
        stats = CONTADOR_TOKENS[user_id].copy()
        if stats['mensagens_processadas'] > 0:
            stats['media_por_mensagem'] = round(stats['total_geral'] / stats['mensagens_processadas'], 2)
        else:
            stats['media_por_mensagem'] = 0
        
        return stats

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
                'nome_real': 'Natan'
            }
        
        # FREE ACCESS
        if plan_type == 'free':
            return {
                'tipo': 'free',
                'nome_display': 'Free Access',
                'plano': 'Free (teste)',
                'nome_real': nome
            }
        
        # PROFESSIONAL
        if plan == 'professional':
            return {
                'tipo': 'professional',
                'nome_display': 'Professional',
                'plano': 'Professional',
                'nome_real': nome
            }
        
        # STARTER (padrão)
        return {
            'tipo': 'starter',
            'nome_display': 'Starter',
            'plano': 'Starter',
            'nome_real': nome
        }
        
    except Exception as e:
        print(f"⚠️ Erro em determinar_tipo_usuario: {e}")
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
# 🤖 OPENAI - v7.5 COM LEITURA DO SUPABASE
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
        tipo = str(tipo_usuario.get('tipo', 'starter')).lower().strip()
        plano = tipo_usuario.get('plano', 'Starter')
        
        # 🎯 DETECTA CATEGORIA DA MENSAGEM
        categoria, config = detectar_categoria_mensagem(pergunta)
        max_tokens = config['max_tokens']
        instrucao_tamanho = config['instrucao']
        
        print(f"\n{'='*80}")
        print(f"🎯 OTIMIZAÇÃO INTELIGENTE:")
        print(f"   Categoria: {categoria}")
        print(f"   Max Tokens: {max_tokens}")
        print(f"   Instrução: {instrucao_tamanho}")
        print(f"   Tipo: {tipo}")
        print(f"   Nome: {nome_usuario}")
        print(f"{'='*80}\n")
        
        # 🆕 GERA CONTEXTO DO SUPABASE
        contexto_supabase = gerar_contexto_supabase()
        
        # INSTRUÇÕES SOBRE SUPORTE
        if tipo == 'admin':
            suporte_info = "ADMIN: Você tem acesso total."
        elif tipo == 'free':
            suporte_info = "FREE: Direcione para WhatsApp (21) 99282-6074 para ajuda extra."
        else:
            suporte_info = "PAGOS: Direcione para página Suporte para falar com Natan pessoalmente."
        
        # CONTEXTO BASEADO NO TIPO
        if tipo == 'admin':
            ctx = f"ADMIN (Natan): Você está falando com o CRIADOR da NatanSites. Seja pessoal e direto."
        elif tipo == 'free':
            ctx = f"FREE ({nome_usuario}): Teste grátis com 100 mensagens/semana. Contato: WhatsApp (21) 99282-6074."
        elif tipo == 'professional':
            ctx = f"PROFESSIONAL ({nome_usuario}): Cliente premium com 5.000 mensagens/mês. Suporte pela página Suporte."
        else:
            ctx = f"STARTER ({nome_usuario}): Cliente com 1.250 mensagens/mês. Suporte pela página Suporte."
        
        info_pessoal = f"""
INFORMAÇÕES DO USUÁRIO:
- Nome: {nome_usuario}
- Plano: {plano}
- Tipo: {tipo.upper()}

COMO RESPONDER:
- Se perguntar qual meu nome: Seu nome é {nome_usuario}
- Se perguntar qual meu plano: Você tem o plano {plano}
- Use o nome dele naturalmente quando apropriado
"""
        
        prompt_sistema = f"""Você é NatanAI, assistente virtual da NatanSites.

{ctx}

{info_pessoal}

{suporte_info}

⚡ INSTRUÇÃO DE TAMANHO CRÍTICA - OBRIGATÓRIA:
{instrucao_tamanho}

CATEGORIA DETECTADA: "{categoria}"
SEJA EXTREMAMENTE OBJETIVO E DIRETO NESTA CATEGORIA.

{contexto_supabase}

DADOS OFICIAIS DA NATANSITES:

CRIADOR: Natan Borges Alves Nascimento
- Desenvolvedor Full-Stack
- WhatsApp: (21) 99282-6074
- Email: borgesnatan09@gmail.com
- Site: https://natansites.com.br

STACK TÉCNICO:
Front-end: HTML5, CSS3, JavaScript, React, Vue, TypeScript, Tailwind
Back-end: Node.js, Python, Express.js, APIs
Mobile: React Native
Banco: Supabase, PostgreSQL
Especialidades: IA, SEO, Animações Web

PORTFÓLIO (7 PROJETOS):

1. Espaço Familiares - espacofamiliares.com.br
   Site para espaço de eventos

2. DeluxModPack - deluxgtav.netlify.app
   ModPack gratuito para GTA V

3. Quiz Venezuela - quizvenezuela.onrender.com
   Quiz interativo educacional

4. NatanSites - natansites.com.br
   Plataforma comercial completa

5. MathWork - mathworkftv.netlify.app
   Plataforma educacional de matemática

6. Alessandra Yoga - alessandrayoga.netlify.app
   Cartão de visita digital para yoga

7. TAF Sem Tabu - tafsemtabu.com.br
   Site sobre E-Book de preparação física

PLANOS NATANSITES:

FREE - R$0,00 (contrato 1 ano)
- Acesso demo à plataforma
- Sites simples/básicos
- Sem uso comercial
- Sem hospedagem/domínio
- Marca d'água presente
- 100 mensagens/semana NatanAI

STARTER - R$320 (setup) + R$39,99/mês
- Site responsivo até 5 páginas
- Design moderno
- Uso comercial
- Hospedagem 1 ano incluída
- Sem marca d'água
- Suporte 24/7
- SEO básico
- 1.250 mensagens/mês NatanAI

PROFESSIONAL - R$530 (setup) + R$79,99/mês
- Páginas ilimitadas
- Design 100% personalizado
- SEO avançado
- Domínio incluído
- Suporte prioritário
- Blog integrado (opcional)
- E-commerce básico (opcional)
- 5.000 mensagens/mês NatanAI

PÁGINAS DE CADASTRO:
- Starter: Formulário (Nome, Data Nasc, CPF) + PIX R$320
- Professional: Formulário (Nome, Data Nasc, CPF) + PIX R$530
- Envio automático via EmailJS
- Aguardar 10min a 2h para criação da conta

REGRAS CRÍTICAS:

1. TAMANHO DA RESPOSTA (MUITO IMPORTANTE):
   - Siga RIGOROSAMENTE a instrução: {instrucao_tamanho}
   - Saudações: 1-2 frases curtas
   - Despedidas: 1-2 frases cordiais
   - Confirmações: 1-2 frases
   - Casuais/Bobeiras: 2-3 frases naturais
   - Explicações simples: 3-5 frases curtas e diretas
   - Planos/Valores: 5-6 frases objetivas
   - Técnico: 6-7 frases simplificadas
   - Complexo: máx 8-10 frases organizadas

2. Uso do nome: Use {nome_usuario} naturalmente (máx 1-2x)

3. Primeira pessoa: Nunca diga eu desenvolvo, sempre o Natan desenvolve

4. Informações verificadas: Use PRIORITARIAMENTE as informações do contexto Supabase acima, que são dados reais e atualizados do site

5. Naturalidade:
   - Nunca repita a pergunta do usuário
   - Varie as respostas
   - Seja conversacional
   - Emojis moderados (1-2 por resposta em apenas 34% das respostas)

6. Contato correto:
   - WhatsApp: (21) 99282-6074
   - Email: borgesnatan09@gmail.com
   - Links completos (com https://)

7. Direcionamento de suporte:
   - FREE: Sempre WhatsApp (21) 99282-6074
   - PAGOS: Sempre página Suporte (chat com Natan pessoa real)

8. FORMATAÇÃO:
   - PROIBIDO usar asteriscos ou underscores
   - PROIBIDO usar acentos isolados
   - PROIBIDO usar backticks
   - Escreva naturalmente sem formatação markdown

9. ADAPTAÇÃO DE FORMATO:
   - Saudações/Despedidas/Confirmações: 1-2 frases diretas
   - Casual/Bobeiras: 2-3 frases naturais
   - Listas quando necessário: use traço (-)
   - Parágrafos para explicações conceituais
   - Adapte baseado na complexidade da pergunta

10. EMOJIS (USO MODERADO):
    - Use apenas em 34% das respostas
    - Máximo 2 emojis por resposta
    - Nunca em respostas técnicas
    - Simples apenas: 😊 😅 🚀 ✨ 🌟 💙 ✅ 🎁 💼 👑 🌱 💎

Responda de forma contextual, pessoal, natural e precisa baseando-se nas informações reais do site e do portfólio."""

        contexto_memoria = obter_contexto_memoria(user_id)
        
        messages = [
            {"role": "system", "content": prompt_sistema}
        ]
        
        messages.extend(contexto_memoria)
        messages.append({"role": "user", "content": pergunta})
        
        print(f"📤 Enviando para OpenAI - Categoria: {categoria} | Max Tokens: {max_tokens}")
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.75
        )
        
        resposta = response.choices[0].message.content.strip()
        
        # Captura tokens usados
        tokens_entrada = response.usage.prompt_tokens
        tokens_saida = response.usage.completion_tokens
        tokens_total = response.usage.total_tokens
        
        registrar_tokens_usados(user_id, tokens_entrada, tokens_saida, tokens_total)
        
        print(f"📊 Tokens: {tokens_entrada} (entrada) + {tokens_saida} (saída) = {tokens_total} (total)")
        print(f"🎯 Economia: Categoria '{categoria}' usou {max_tokens} tokens max ao invés de 650")
        
        # Limpa formatação markdown
        resposta = limpar_formatacao_markdown(resposta)

        adicionar_mensagem_memoria(user_id, 'user', pergunta)
        adicionar_mensagem_memoria(user_id, 'assistant', resposta)
        
        valida, problemas = validar_resposta(resposta, tipo)
        if not valida:
            print(f"⚠️ Validação falhou: {problemas}")
            return None
        
        if random.random() < 0.1:
            frases = [
                "\n\nVibrações Positivas! ✨",
                "\n\nSucesso no seu projeto! 💙",
                "\n\nVamos juntos nessa! 🚀",
                "\n\nConte sempre comigo! 🌟"
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
        palavras_cache = ['preço', 'quanto custa', 'plano', 'contato', 'whatsapp', 'cadastro', 'starter', 'professional']
        usar_cache = any(palavra in pergunta.lower() for palavra in palavras_cache)
        
        tipo = str(tipo_usuario.get('tipo', 'starter')).lower().strip()
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
            
            stats_tokens = obter_estatisticas_tokens(user_id)
            return resposta, f"openai_memoria_{tipo}_supabase", stats_tokens
        
        print(f"⚠️ OpenAI retornou None, usando fallback")
        nome = tipo_usuario.get('nome_real', 'Cliente')
        return f"Desculpa {nome}, estou com dificuldades técnicas no momento.\n\nPor favor, fale diretamente com o Natan no WhatsApp: (21) 99282-6074", "fallback", {}
        
    except Exception as e:
        print(f"❌ Erro gerar_resposta: {e}")
        import traceback
        traceback.print_exc()
        return "Ops, erro técnico! Fale com Natan: (21) 99282-6074\n\nVibrações Positivas! ✨", "erro", {}

# =============================================================================
# 📡 ROTAS
# =============================================================================

@app.route('/health', methods=['GET'])
@app.route('/api/health', methods=['GET'])
def health():
    with memoria_lock:
        usuarios_ativos = len(MEMORIA_USUARIOS)
        total_mensagens = sum(len(m['mensagens']) for m in MEMORIA_USUARIOS.values())
    
    with tokens_lock:
        total_tokens_usados = sum(c['total_geral'] for c in CONTADOR_TOKENS.values())

    with contador_lock:
        total_mensagens_enviadas = sum(c['total'] for c in CONTADOR_MENSAGENS.values())
    
    # 🆕 Status do cache Supabase
    with cache_lock:
        cache_status = {}
        for tabela, info in CACHE_SUPABASE.items():
            cache_status[tabela] = {
                'carregado': info['data'] is not None,
                'registros': len(info['data']) if info['data'] else 0,
                'ultima_atualizacao': info['ultima_atualizacao'].isoformat() if info['ultima_atualizacao'] else None
            }
    
    return jsonify({
        "status": "online",
        "sistema": "NatanAI v7.5 - Leitura Supabase + Otimização Inteligente",
        "versao": "7.5",
        "openai": verificar_openai(),
        "supabase": supabase is not None,
        "supabase_cache": cache_status,
        "memoria": {
            "usuarios_ativos": usuarios_ativos,
            "total_mensagens_memoria": total_mensagens,
            "max_por_usuario": MAX_MENSAGENS_MEMORIA
        },
        "limites": {
            "free": f"{LIMITES_MENSAGENS['free']} mensagens/semana",
            "starter": f"{LIMITES_MENSAGENS['starter']} mensagens/mês",
            "professional": f"{LIMITES_MENSAGENS['professional']} mensagens/mês",
            "admin": "Ilimitado",
            "total_mensagens_enviadas": total_mensagens_enviadas,
            "total_tokens_usados": total_tokens_usados
        },
        "planos_valores": {
            "free": "R$0,00 (teste 1 ano)",
            "starter": "R$320,00 (setup) + R$39,99/mês",
            "professional": "R$530,00 (setup) + R$79,99/mês"
        },
        "otimizacao_tokens": {
            "saudacao": "80 tokens",
            "despedida": "60 tokens",
            "casual": "80 tokens",
            "confirmacao": "60 tokens",
            "explicacao_simples": "200 tokens",
            "planos_valores": "250 tokens",
            "tecnico": "300 tokens",
            "complexo": "400 tokens"
        },
        "features": [
            "leitura_automatica_supabase",
            "cache_inteligente_5min",
            "site_content_integration",
            "plataforma_info_integration",
            "repo_content_integration",
            "ia_memoria_integration",
            "otimizacao_inteligente_tokens",
            "deteccao_categoria_mensagem",
            "memoria_inteligente", 
            "resumo_automatico", 
            "contexto_completo", 
            "controle_limites_por_plano",
            "validacao_relaxada",
            "portfolio_completo_7_projetos",
            "suporte_diferenciado_por_plano",
            "paginas_cadastro_starter_professional",
            "sem_asteriscos_formatacao",
            "adaptacao_formato_inteligente",
            "economia_maxima_tokens"
        ],
        "economia": "Economia de até 85% em tokens + Leitura automática do site real"
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
        
        # Autenticação via token
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
        
        # Fallback para user_data
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
        
        # Verifica limite de mensagens
        tipo_plano = tipo_usuario.get('tipo', 'starter')
        pode_enviar, msgs_usadas, limite, msgs_restantes = verificar_limite_mensagens(user_id, tipo_plano)
        
        if not pode_enviar:
            print(f"🚫 Limite atingido: {msgs_usadas}/{limite}")
            mensagem_limite = gerar_mensagem_limite_atingido(tipo_plano, msgs_usadas, limite)
            
            return jsonify({
                "response": mensagem_limite,
                "resposta": mensagem_limite,
                "metadata": {
                    "fonte": "limite_atingido",
                    "sistema": "NatanAI v7.5 - Supabase + Otimização",
                    "versao": "7.5",
                    "tipo_usuario": tipo_usuario['tipo'],
                    "plano": tipo_usuario['plano'],
                    "nome_usuario": tipo_usuario.get('nome_real', 'Cliente'),
                    "limite_atingido": True,
                    "mensagens_usadas": msgs_usadas,
                    "limite_total": "ilimitado" if limite == float('inf') else limite,
                    "mensagens_restantes": 0,
                    "supabase_integration": True
                }
            })
        
        inicializar_memoria_usuario(user_id)
        
        nome_usuario = tipo_usuario.get('nome_real', 'Cliente')
        tipo_str = tipo_usuario.get('tipo', 'starter')
        
        print(f"\n{'='*80}")
        print(f"💬 [{datetime.now().strftime('%H:%M:%S')}] {nome_usuario} ({tipo_usuario['nome_display']}) - TIPO: '{tipo_str}'")
        print(f"📊 Mensagens: {msgs_usadas + 1}/{limite if limite != float('inf') else 'ilimitado'} (restantes: {msgs_restantes if msgs_restantes != float('inf') else 'ilimitado'})")
        print(f"📝 Mensagem: {mensagem[:100]}...")
        print(f"{'='*80}\n")
        
        resposta, fonte, stats_tokens = gerar_resposta(mensagem, tipo_usuario, user_id)
        valida, _ = validar_resposta(resposta, tipo_str)
        
        # Incrementa contador apenas se resposta gerada com sucesso
        if fonte != "erro" and fonte != "fallback":
            nova_contagem = incrementar_contador(user_id, tipo_plano)
            msgs_restantes = limite - nova_contagem if limite != float('inf') else float('inf')
            print(f"📊 Contador atualizado: {nova_contagem}/{limite if limite != float('inf') else 'ilimitado'}")
        
        with historico_lock:
            HISTORICO_CONVERSAS.append({
                "timestamp": datetime.now().isoformat(),
                "tipo": tipo_usuario['tipo'],
                "nome": nome_usuario,
                "fonte": fonte,
                "validacao": valida,
                "com_memoria": 'memoria' in fonte,
                "com_supabase": 'supabase' in fonte
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
                "sistema": "NatanAI v7.5 - Supabase + Otimização",
                "versao": "7.5",
                "supabase_integration": True,
                "otimizacao_tokens": True,
                "tokens": stats_tokens,
                "tipo_usuario": tipo_usuario['tipo'],
                "plano": tipo_usuario['plano'],
                "nome_usuario": nome_usuario,
                "validacao": valida,
                "autenticado": user_info is not None,
                "memoria": memoria_info,
                "is_free_access": tipo_usuario['tipo'] == 'free',
                "validacao_anti_alucinacao": valida,
                "formatacao_limpa": True,
                "limite_mensagens": {
                    "mensagens_usadas": nova_contagem if fonte not in ["erro", "fallback"] else msgs_usadas,
                    "limite_total": "ilimitado" if limite == float('inf') else limite,
                    "mensagens_restantes": "ilimitado" if msgs_restantes == float('inf') else max(0, msgs_restantes),
                    "porcentagem_uso": 0 if limite == float('inf') else round((nova_contagem / limite * 100) if fonte not in ["erro", "fallback"] else (msgs_usadas / limite * 100), 2)
                }
            }
        })
        
    except Exception as e:
        print(f"❌ Erro no endpoint /chat: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "response": "Erro técnico. Fale com Natan: (21) 99282-6074\n\nVibrações Positivas! ✨",
            "resposta": "Erro técnico. Fale com Natan: (21) 99282-6074\n\nVibrações Positivas! ✨",
            "metadata": {"fonte": "erro", "error": str(e), "versao": "7.5"}
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
        com_supabase = 0
        
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
                if c.get("com_supabase", False):
                    com_supabase += 1
        
        with memoria_lock:
            usuarios_memoria = len(MEMORIA_USUARIOS)
            total_msgs_memoria = sum(len(m['mensagens']) for m in MEMORIA_USUARIOS.values())
        
        with contador_lock:
            total_mensagens_enviadas = sum(c['total'] for c in CONTADOR_MENSAGENS.values())
        
        with cache_lock:
            cache_info = {}
            for tabela, info in CACHE_SUPABASE.items():
                cache_info[tabela] = {
                    'registros': len(info['data']) if info['data'] else 0,
                    'carregado': info['data'] is not None
                }
        
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
            "supabase": {
                "conversas_com_dados_site": com_supabase,
                "taxa_uso_supabase": round((com_supabase / len(HISTORICO_CONVERSAS)) * 100, 2),
                "cache": cache_info
            },
            "limites_mensagens": {
                "total_mensagens_enviadas": total_mensagens_enviadas,
                "usuarios_com_contador": len(CONTADOR_MENSAGENS)
            },
            "sistema": "NatanAI v7.5 - Supabase + Otimização Inteligente",
            "versao": "7.5"
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

@app.route('/resetar_contador/<user_id>', methods=['POST'])
def resetar_contador_endpoint(user_id):
    """Endpoint para resetar contador de mensagens de um usuário"""
    if resetar_contador_usuario(user_id):
        return jsonify({
            "message": f"Contador resetado para user: {user_id[:8]}...",
            "novo_contador": obter_contador_mensagens(user_id)
        })
    return jsonify({"message": "Usuário não encontrado"}), 404

@app.route('/verificar_limite/<user_id>', methods=['GET'])
def verificar_limite_endpoint(user_id):
    """Endpoint para verificar limite de mensagens de um usuário"""
    try:
        user_data = obter_dados_usuario_completos(user_id)
        if not user_data:
            return jsonify({"error": "Usuário não encontrado"}), 404
        
        tipo_info = determinar_tipo_usuario(user_data)
        tipo_plano = tipo_info.get('tipo', 'starter')
        
        pode_enviar, msgs_usadas, limite, msgs_restantes = verificar_limite_mensagens(user_id, tipo_plano)
        
        return jsonify({
            "user_id": user_id[:8] + "...",
            "tipo_plano": tipo_plano,
            "plano_display": tipo_info.get('plano', 'Starter'),
            "pode_enviar": pode_enviar,
            "mensagens_usadas": msgs_usadas,
            "limite_total": limite if limite != float('inf') else "Ilimitado",
            "mensagens_restantes": msgs_restantes if msgs_restantes != float('inf') else "Ilimitado",
            "porcentagem_uso": round((msgs_usadas / limite * 100) if limite != float('inf') else 0, 2),
            "versao": "7.5"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/atualizar_cache', methods=['POST'])
def atualizar_cache_manual():
    """Endpoint para forçar atualização do cache Supabase"""
    try:
        auth_header = request.headers.get('Authorization', '')
        if not auth_header:
            return jsonify({"error": "Autorização necessária"}), 401
        
        print(f"\n🔄 Atualização manual do cache solicitada")
        
        resultados = {}
        for tabela in ['site_content', 'plataforma_info', 'repo_content', 'ia_memoria']:
            dados = carregar_dados_supabase(tabela)
            resultados[tabela] = {
                'sucesso': dados is not None,
                'registros': len(dados) if dados else 0
            }
        
        return jsonify({
            "message": "Cache atualizado manualmente",
            "timestamp": datetime.now().isoformat(),
            "resultados": resultados,
            "versao": "7.5"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/cache_status', methods=['GET'])
def cache_status():
    """Endpoint para verificar status do cache Supabase"""
    try:
        with cache_lock:
            status = {}
            for tabela, info in CACHE_SUPABASE.items():
                status[tabela] = {
                    'carregado': info['data'] is not None,
                    'registros': len(info['data']) if info['data'] else 0,
                    'ultima_atualizacao': info['ultima_atualizacao'].isoformat() if info['ultima_atualizacao'] else None,
                    'tempo_desde_atualizacao': None
                }
                
                if info['ultima_atualizacao']:
                    diferenca = (datetime.now() - info['ultima_atualizacao']).total_seconds()
                    status[tabela]['tempo_desde_atualizacao'] = f"{int(diferenca)}s"
        
        return jsonify({
            "status": status,
            "intervalo_atualizacao": f"{INTERVALO_ATUALIZACAO_CACHE}s",
            "timestamp": datetime.now().isoformat(),
            "versao": "7.5"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({
        "status": "pong",
        "timestamp": datetime.now().isoformat(),
        "version": "v7.5-supabase-integration"
    })

@app.route('/', methods=['GET'])
def home():
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>NatanAI v7.5 - Integração Supabase</title>
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
            .badge.update {
                background: #2196F3;
                animation: pulse 2s infinite;
            }
            .badge.new {
                background: #FF5722;
            }
            .badge.supabase {
                background: #3ECF8E;
            }
            @keyframes pulse {
                0%, 100% { transform: scale(1); }
                50% { transform: scale(1.05); }
            }
            .update-box {
                background: linear-gradient(135deg, #e8f5e9, #c8e6c9);
                padding: 20px;
                border-radius: 15px;
                margin: 20px 0;
                border-left: 5px solid #4CAF50;
            }
            .update-box h3 { color: #2E7D32; margin-bottom: 10px; }
            .supabase-info {
                background: linear-gradient(135deg, #e3f2fd, #bbdefb);
                padding: 20px;
                border-radius: 15px;
                margin: 20px 0;
                border-left: 5px solid #2196F3;
            }
            .supabase-info h3 { color: #1565C0; margin-bottom: 15px; }
            .table-item {
                display: flex;
                justify-content: space-between;
                padding: 10px;
                margin: 5px 0;
                background: white;
                border-radius: 8px;
                font-weight: 500;
            }
            .table-item .table-name {
                color: #666;
            }
            .table-item .table-status {
                color: #2E7D32;
                font-weight: bold;
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
                <h1>🧠 NatanAI v7.5 - Integração Supabase</h1>
                <p style="color: #666;">Leitura Automática do Site Real + Otimização Inteligente</p>
                <span class="badge update">✅ v7.5</span>
                <span class="badge supabase">🗄️ Supabase Integration</span>
                <span class="badge new">📊 Dados Reais do Site</span>
                <span class="badge">Cache Inteligente</span>
            </div>
            
            <div class="update-box">
                <h3>🆕 NOVO v7.5 - Integração com Supabase:</h3>
                <p>
                ✅ <strong>Leitura automática de 4 tabelas:</strong> site_content, plataforma_info, repo_content, ia_memoria<br>
                ✅ <strong>Cache inteligente:</strong> Atualização automática a cada 5 minutos<br>
                ✅ <strong>Dados reais do site:</strong> IA responde com informações atualizadas do natansites.com.br<br>
                ✅ <strong>Sincronização automática:</strong> Quando o webhook atualiza, IA recebe novos dados<br>
                ✅ <strong>Contexto completo:</strong> Planos, promoções, contatos e mudanças do site<br>
                ✅ <strong>Mantém tudo v7.4:</strong> Otimização de tokens + Memória inteligente
                </p>
            </div>

            <div class="supabase-info">
                <h3>🗄️ Tabelas Supabase Integradas:</h3>
                <div class="table-item">
                    <span class="table-name">📄 site_content</span>
                    <span class="table-status">Conteúdo das páginas do site</span>
                </div>
                <div class="table-item">
                    <span class="table-name">💼 plataforma_info</span>
                    <span class="table-status">Planos, promoções e contatos</span>
                </div>
                <div class="table-item">
                    <span class="table-name">🗂️ repo_content</span>
                    <span class="table-status">Arquivos do repositório GitHub</span>
                </div>
                <div class="table-item">
                    <span class="table-name">🧠 ia_memoria</span>
                    <span class="table-status">Atualizações e mudanças recentes</span>
                </div>
                <p style="margin-top: 15px; color: #666; font-size: 0.9em;">
                    <strong>Atualização:</strong> Cache renovado automaticamente a cada 5 minutos<br>
                    <strong>Endpoint:</strong> POST /atualizar_cache para forçar atualização manual
                </p>
            </div>

            <div class="select-plan">
                <strong>🎭 Testar como:</strong>
                <select id="planType" onchange="atualizarPlano()">
                    <option value="free">🎁 Free (100 mensagens/semana)</option>
                    <option value="starter">🌱 Starter (1.250 mensagens/mês)</option>
                    <option value="professional">💎 Professional (5.000 mensagens/mês)</option>
                    <option value="admin">👑 Admin (Ilimitado)</option>
                </select>
                <p id="planInfo" style="margin-top: 10px; color: #666;"></p>
            </div>
            
            <div id="chat-box" class="chat-box">
                <div class="message bot">
                    <strong>🤖 NatanAI v7.5:</strong><br><br>
                    Sistema com Integração Supabase Ativa! 🗄️<br><br>
                    <strong>Novidade:</strong> Agora eu leio dados reais do seu site!<br><br>
                    Teste perguntando sobre:<br>
                    • Informações do site natansites.com.br<br>
                    • Planos e promoções atualizadas<br>
                    • Conteúdo das páginas<br>
                    • Mudanças recentes na plataforma<br><br>
                    <strong>Mantém todas as features v7.4:</strong><br>
                    • Otimização inteligente de tokens<br>
                    • Memória de conversas<br>
                    • Respostas personalizadas por plano
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
                info: '🎁 FREE - 100 mensagens/semana - R$ 0,00'
            },
            admin: {
                plan: 'admin',
                plan_type: 'paid',
                user_name: 'Natan',
                name: 'Natan',
                email: 'natan@natandev.com',
                limite: Infinity,
                info: '👑 ADMIN (Natan) - Mensagens ilimitadas'
            },
            starter: {
                plan: 'starter',
                plan_type: 'paid',
                user_name: 'Cliente Starter',
                name: 'Cliente Starter',
                email: 'starter@teste.com',
                limite: 1250,
                info: '🌱 STARTER - 1.250 mensagens/mês - R$320 + R$39,99/mês'
            },
            professional: {
                plan: 'professional',
                plan_type: 'paid',
                user_name: 'Cliente Pro',
                name: 'Cliente Pro',
                email: 'pro@teste.com',
                limite: 5000,
                info: '💎 PROFESSIONAL - 5.000 mensagens/mês - R$530 + R$79,99/mês'
            }
        };

        function atualizarPlano() {
            planAtual = document.getElementById('planType').value;
            limiteAtual = planConfigs[planAtual].limite;
            mensagensEnviadas = 0;
            
            document.getElementById('planInfo').textContent = planConfigs[planAtual].info;
            
            const chatBox = document.getElementById('chat-box');
            chatBox.innerHTML = '<div class="message bot"><strong>🤖 NatanAI v7.5:</strong><br><br>' + 
                planConfigs[planAtual].info + '<br><br>' +
                '<strong>Limite:</strong> ' + (limiteAtual === Infinity ? 'Ilimitado' : limiteAtual + ' mensagens') + '<br><br>' +
                '<strong>Integração Supabase Ativa! 🗄️</strong><br>' +
                'Pergunte sobre informações do site real!<br><br>' +
                'Exemplos:<br>' +
                '• "Me fale sobre os planos"<br>' +
                '• "Qual o conteúdo da página inicial?"<br>' +
                '• "Tem alguma promoção?"<br>' +
                '• "Como faço para contratar?"' +
                '</div>';
        }

        atualizarPlano();
        
        async function enviar() {
            const input = document.getElementById('msg');
            const chatBox = document.getElementById('chat-box');
            const msg = input.value.trim();
            
            if (!msg) return;
            
            if (limiteAtual !== Infinity && mensagensEnviadas >= limiteAtual) {
                chatBox.innerHTML += '<div class="message bot" style="background: #ffebee; border-left-color: #f44336;"><strong>🚫 Limite Atingido:</strong><br>' +
                    'Você atingiu o limite de mensagens do seu plano.' +
                    '</div>';
                chatBox.scrollTop = chatBox.scrollHeight;
                return;
            }
            
            chatBox.innerHTML += '<div class="message user"><strong>Você:</strong><br>' + msg + '</div>';
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
                const resp = (data.response || data.resposta).replace(/\n/g, '<br>');
                
                const limiteAtingido = data.metadata && data.metadata.limite_atingido;
                const messageClass = limiteAtingido ? 'bot" style="background: #fff3e0; border-left-color: #FF9800;' : 'bot';
                
                let tokensInfo = '';
                if (data.metadata && data.metadata.tokens) {
                    const tokens = data.metadata.tokens;
                    tokensInfo = `<br><br><small style="color: #666;">📊 Tokens: ${tokens.total_geral || 'N/A'} | 🗄️ Supabase: ${data.metadata.supabase_integration ? 'Ativo' : 'Inativo'}</small>`;
                }
                
                chatBox.innerHTML += '<div class="message ' + messageClass + '"><strong>🤖 NatanAI v7.5:</strong><br><br>' + resp + tokensInfo + '</div>';
                
                if (data.metadata && data.metadata.limite_mensagens && !limiteAtingido) {
                    mensagensEnviadas = data.metadata.limite_mensagens.mensagens_usadas;
                } else if (!limiteAtingido) {
                    mensagensEnviadas++;
                }
                
                console.log('✅ Metadata v7.5:', data.metadata);
                
            } catch (error) {
                chatBox.innerHTML += '<div class="message bot" style="background: #ffebee; border-left-color: #f44336;"><strong>🤖 NatanAI:</strong><br>Erro: ' + error.message + '</div>';
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
    print("🧠 NATANAI v7.5 - INTEGRAÇÃO SUPABASE + OTIMIZAÇÃO INTELIGENTE")
    print("="*80)
    print("💰 VALORES:")
    print("   🎁 FREE: R$ 0,00 (contrato 1 ano)")
    print("   🌱 STARTER: R$ 320,00 (setup) + R$ 39,99/mês")
    print("   💎 PROFESSIONAL: R$ 530,00 (setup) + R$ 79,99/mês")
    print("")
    print("📊 LIMITES:")
    print("   🎁 FREE: 100 mensagens/semana")
    print("   🌱 STARTER: 1.250 mensagens/mês")
    print("   💎 PROFESSIONAL: 5.000 mensagens/mês")
    print("   👑 ADMIN: ∞ Ilimitado")
    print("")
    print("🗄️ INTEGRAÇÃO SUPABASE v7.5:")
    print("   📄 site_content: Conteúdo das páginas")
    print("   💼 plataforma_info: Planos e promoções")
    print("   🗂️ repo_content: Arquivos GitHub")
    print("   🧠 ia_memoria: Atualizações recentes")
    print("   ⏱️ Cache: Atualização a cada 5 minutos")
    print("")
    print("🎯 OTIMIZAÇÃO DE TOKENS v7.4 (mantida):")
    print("   👋 Saudações: 80 tokens")
    print("   👋 Despedidas: 60 tokens")
    print("   💬 Casual: 80 tokens")
    print("   ✅ Confirmações: 60 tokens")
    print("   ❓ Explicações: 200 tokens")
    print("   💰 Planos: 250 tokens")
    print("   🔧 Técnico: 300 tokens")
    print("   📚 Complexo: 400 tokens")
    print("")
    print("✨ FEATURES v7.5:")
    print("   ✅ Leitura automática do Supabase")
    print("   ✅ Cache inteligente (5 minutos)")
    print("   ✅ Dados reais do site natansites.com.br")
    print("   ✅ Sincronização com webhook atualizar-ia.js")
    print("   ✅ Contexto completo (site + planos + GitHub)")
    print("   ✅ Todas features v7.4 mantidas")
    print("   ✅ Otimização de tokens por categoria")
    print("   ✅ Sistema de memória inteligente")
    print("   ✅ Validação e segurança")
    print("="*80 + "\n")
    
    print(f"OpenAI: {'✅' if verificar_openai() else '⚠️'}")
    print(f"Supabase: {'✅' if supabase else '⚠️'}")
    print(f"Sistema de Memória: ✅ Ativo")
    print(f"Sistema de Limites: ✅ Ativo")
    print(f"Limpeza de Formatação: ✅ Ativa")
    print(f"Otimização Inteligente: ✅ Ativa (v7.4)")
    print(f"Integração Supabase: ✅ Ativa (v7.5)")
    print(f"Cache Automático: ✅ Ativo (5 min)\n")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
    
