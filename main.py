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
# 📊 LIMITES DE MENSAGENS POR PLANO (ATUALIZADOS v7.3)
# ============================================
LIMITES_MENSAGENS = {
    'free': 100,          # 🎁 100 mensagens/semana para teste
    'starter': 1250,      # 🌱 1.250 mensagens/mês
    'professional': 5000, # 💎 5.000 mensagens/mês
    'admin': float('inf') # 👑 Ilimitado
}

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
TREINOS_IA = []
treinos_lock = threading.Lock()
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
    """Reseta o contador de mensagens de um usuário (para renovação mensal/semanal)"""
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
        return f"""Olá! Você atingiu o limite de {limite} mensagens por semana do seu teste grátis.

Para continuar usando a NatanAI sem limites e ter acesso a muito mais recursos, você pode contratar um dos nossos planos:

PLANO STARTER - R$320 (setup) + R$39,99/mês
- 1.250 mensagens por mês com NatanAI
- Site profissional completo
- Hospedagem incluída
- Suporte 24/7

PLANO PROFESSIONAL - R$530 (setup) + R$79,99/mês
- 5.000 mensagens por mês com NatanAI
- Recursos avançados
- Domínio personalizado incluído
- Prioridade no suporte

Para contratar, fale com o Natan no WhatsApp: (21) 99282-6074

Obrigado por testar a NatanAI! ✨"""
    
    elif tipo == 'starter':
        return f"""Você atingiu o limite de {limite} mensagens do seu plano Starter este mês.

Para ter mais mensagens, você pode:

1. Fazer upgrade para o Plano Professional (5.000 mensagens/mês)
2. Aguardar a renovação mensal do seu plano

Para fazer upgrade ou renovar, acesse a página de Suporte e fale com o Natan pessoalmente!

Obrigado por usar a NatanAI! 🚀"""
    
    elif tipo == 'professional':
        return f"""Você atingiu o limite de {limite} mensagens do seu plano Professional este mês.

Isso é bastante uso! Se precisar de mais mensagens, entre em contato com o Natan na página de Suporte para discutirmos uma solução personalizada.

Obrigado pela confiança! 💎"""
    
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
        
        print(f"📊 Tokens registrados: entrada={tokens_entrada}, saída={tokens_saida}, total={tokens_total}")

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
# 🎓 SISTEMA DE TREINAMENTO ADMIN
# =============================================================================

def carregar_treinos_supabase():
    """Carrega treinos ativos do Supabase"""
    try:
        if not supabase:
            return []
        
        response = supabase.table('ai_training').select('*').eq('ativo', True).execute()
        
        if response.data:
            print(f"📚 {len(response.data)} treinos carregados do Supabase")
            return response.data
        return []
    except Exception as e:
        print(f"⚠️ Erro ao carregar treinos: {e}")
        return []

def adicionar_treino(titulo, conteudo, categoria='geral'):
    """Adiciona novo treino no Supabase"""
    try:
        if not supabase:
            return False, "Supabase não disponível"
        
        data = {
            'titulo': titulo,
            'conteudo': conteudo,
            'categoria': categoria,
            'ativo': True
        }
        
        response = supabase.table('ai_training').insert(data).execute()
        
        if response.data:
            # Atualiza cache local
            with treinos_lock:
                global TREINOS_IA
                TREINOS_IA = carregar_treinos_supabase()
            
            print(f"✅ Treino adicionado: {titulo}")
            return True, f"Treino '{titulo}' adicionado com sucesso!"
        
        return False, "Erro ao salvar treino"
        
    except Exception as e:
        print(f"❌ Erro ao adicionar treino: {e}")
        return False, str(e)

def listar_treinos():
    """Lista todos os treinos (ativos e inativos)"""
    try:
        if not supabase:
            return []
        
        response = supabase.table('ai_training').select('*').order('id').execute()
        return response.data if response.data else []
        
    except Exception as e:
        print(f"⚠️ Erro ao listar treinos: {e}")
        return []

def remover_treino(treino_id):
    """Desativa um treino (soft delete)"""
    try:
        if not supabase:
            return False, "Supabase não disponível"
        
        response = supabase.table('ai_training').update({'ativo': False}).eq('id', treino_id).execute()
        
        if response.data:
            # Atualiza cache local
            with treinos_lock:
                global TREINOS_IA
                TREINOS_IA = carregar_treinos_supabase()
            
            print(f"🗑️ Treino {treino_id} desativado")
            return True, f"Treino #{treino_id} removido!"
        
        return False, "Treino não encontrado"
        
    except Exception as e:
        print(f"❌ Erro ao remover treino: {e}")
        return False, str(e)

def editar_treino(treino_id, novo_conteudo=None, novo_titulo=None, nova_categoria=None):
    """Edita um treino existente"""
    try:
        if not supabase:
            return False, "Supabase não disponível"
        
        updates = {'atualizado_em': 'NOW()'}
        if novo_conteudo:
            updates['conteudo'] = novo_conteudo
        if novo_titulo:
            updates['titulo'] = novo_titulo
        if nova_categoria:
            updates['categoria'] = nova_categoria
        
        response = supabase.table('ai_training').update(updates).eq('id', treino_id).execute()
        
        if response.data:
            # Atualiza cache local
            with treinos_lock:
                global TREINOS_IA
                TREINOS_IA = carregar_treinos_supabase()
            
            print(f"✏️ Treino {treino_id} editado")
            return True, f"Treino #{treino_id} atualizado!"
        
        return False, "Treino não encontrado"
        
    except Exception as e:
        print(f"❌ Erro ao editar treino: {e}")
        return False, str(e)

def ativar_treino(treino_id):
    """Reativa um treino desativado"""
    try:
        if not supabase:
            return False, "Supabase não disponível"
        
        response = supabase.table('ai_training').update({'ativo': True}).eq('id', treino_id).execute()
        
        if response.data:
            # Atualiza cache local
            with treinos_lock:
                global TREINOS_IA
                TREINOS_IA = carregar_treinos_supabase()
            
            print(f"✅ Treino {treino_id} reativado")
            return True, f"Treino #{treino_id} reativado!"
        
        return False, "Treino não encontrado"
        
    except Exception as e:
        print(f"❌ Erro ao ativar treino: {e}")
        return False, str(e)

def gerar_contexto_treinos():
    """Gera contexto de treinos para adicionar ao prompt"""
    with treinos_lock:
        if not TREINOS_IA:
            return ""
        
        contexto = "\n\n📚 CONHECIMENTO ADICIONAL TREINADO (Admin):\n\n"
        
        # Agrupa por categoria
        por_categoria = {}
        for treino in TREINOS_IA:
            cat = treino.get('categoria', 'geral')
            if cat not in por_categoria:
                por_categoria[cat] = []
            por_categoria[cat].append(treino)
        
        # Monta contexto organizado
        for categoria, treinos in por_categoria.items():
            contexto += f"📌 {categoria.upper()}:\n"
            for treino in treinos:
                contexto += f"   • {treino['titulo']}: {treino['conteudo']}\n"
            contexto += "\n"
        
        return contexto

# Carrega treinos ao iniciar
TREINOS_IA = carregar_treinos_supabase()
print(f"📚 Sistema de Treinamento: {len(TREINOS_IA)} treinos ativos")

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
                'plano': 'Free (teste)',
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
# ✨ LIMPEZA DE FORMATAÇÃO (REMOVE ASTERISCOS E CARACTERES ESPECIAIS)
# =============================================================================

def limpar_formatacao_markdown(texto):
    """
    Remove asteriscos e outros caracteres especiais de formatação markdown,
    mantendo apenas o texto limpo e natural.
    """
    if not texto:
        return texto
    
    # Remove asteriscos duplos e simples (negrito e itálico)
    texto = re.sub(r'\*\*([^*]+)\*\*', r'\1', texto)  # **texto** -> texto
    texto = re.sub(r'\*([^*]+)\*', r'\1', texto)      # *texto* -> texto
    
    # Remove underscores de formatação
    texto = re.sub(r'__([^_]+)__', r'\1', texto)      # __texto__ -> texto
    texto = re.sub(r'_([^_]+)_', r'\1', texto)        # _texto_ -> texto
    
    # Remove backticks (código)
    texto = re.sub(r'`([^`]+)`', r'\1', texto)        # `texto` -> texto
    
    # Remove outros caracteres especiais problemáticos
    texto = texto.replace('´', '')
    texto = texto.replace('~', '')
    texto = texto.replace('^', '')
    texto = texto.replace('¨', '')
    
    # Limpa múltiplas quebras de linha
    texto = re.sub(r'\n{3,}', '\n\n', texto)
    
    return texto.strip()

# =============================================================================
# 🤖 OPENAI - v7.3 COM PLANOS ATUALIZADOS E MAX_TOKENS=650
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
        
        print(f"\n{'='*80}")
        print(f"🔍 DEBUG PROCESSAR_OPENAI:")
        print(f"   Tipo recebido: '{tipo}' (type: {type(tipo)})")
        print(f"   Nome: '{nome_usuario}'")
        print(f"   Plano: '{plano}'")
        print(f"   Pergunta: '{pergunta[:50]}...'")
        print(f"{'='*80}\n")
        
        # ✅ INSTRUÇÕES SOBRE SUPORTE
        if tipo == 'admin':
            suporte_info = "🔴 ADMIN: Você tem acesso total. Para questões técnicas internas, você é o criador."
        elif tipo == 'free':
            suporte_info = """
🎁 FREE ACCESS - CONTATO EXCLUSIVO VIA WHATSAPP:
- Para QUALQUER ajuda adicional ou dúvida: WhatsApp (21) 99282-6074
- Nunca mencione página de suporte ou acesse o suporte da plataforma
- Nunca mencione email para usuários Free
- Sempre direcione apenas para WhatsApp: (21) 99282-6074
- Frase modelo: Para mais ajuda, entre em contato pelo WhatsApp: (21) 99282-6074
- Se pedir ajuda extra: Fale comigo no WhatsApp para uma ajuda personalizada: (21) 99282-6074
"""
        else:  # starter ou professional (PAGOS)
            suporte_info = """
💼 CLIENTES PAGOS (Starter/Professional) - SUPORTE COM NATAN PESSOALMENTE:
- A página Suporte é onde o NATAN (pessoa real) dá suporte pessoal ao cliente
- Não é chat com IA - é chat direto com o Natan (humano)
- Se perguntar como falar com Natan: Olá {nome_usuario}, para falar diretamente com o Natan, acesse a página Suporte aqui no site! Lá você fala com ele pessoalmente
- Se perguntar preciso de ajuda: Para falar com o Natan pessoalmente, acesse a página Suporte na plataforma! Ele vai te atender diretamente
- Nunca diga falar comigo ou estou aqui - você é a IA, não o Natan
- Sempre deixe claro que a página Suporte é com o NATAN (pessoa real)
"""
        
        # ✅ MONTA CONTEXTO BASEADO NO TIPO
        if tipo == 'admin':
            ctx = f"🔴 ADMIN (Natan): Você está falando com o CRIADOR da NatanSites. Acesso total. Respostas técnicas e dados internos. Trate como seu criador e chefe. Seja pessoal e direto."
        elif tipo == 'free':
            ctx = f"🎁 FREE ACCESS ({nome_usuario}): Acesso grátis por 1 ano com 100 mensagens/semana. IMPORTANTE: Este usuário pode pedir criação de sites (está incluído no free). Contato apenas WhatsApp (21) 99282-6074. Se pedir site, explique educadamente que não está disponível no Free e que pode contratar via WhatsApp."
        elif tipo == 'professional':
            ctx = f"💎 PROFESSIONAL ({nome_usuario}): Cliente premium com plano Professional. 5.000 mensagens/mês. Suporte prioritário, recursos avançados disponíveis. Direcione para página de Suporte para ajuda extra. Seja atencioso e destaque vantagens."
        else:  # starter
            ctx = f"🌱 STARTER ({nome_usuario}): Cliente com plano Starter. 1.250 mensagens/mês. Direcione para página de Suporte para ajuda extra. Seja acolhedor e pessoal. Se relevante, sugira upgrade para Professional."
        
        print(f"✅ Contexto montado para tipo '{tipo}'")
        
        # ✅ INFORMAÇÕES DO USUÁRIO
        info_pessoal = f"""
📋 INFORMAÇÕES DO USUÁRIO:
- Nome: {nome_usuario}
- Plano: {plano}
- Tipo de acesso: {tipo.upper()}

⚠️ COMO RESPONDER PERGUNTAS PESSOAIS:
- Se perguntar qual meu nome?: Responda Seu nome é {nome_usuario}
- Se perguntar qual meu plano?: Responda Você tem o plano {plano}
- Se perguntar sobre seu acesso: Explique o plano {plano} dele
- Seja natural e use o nome dele quando apropriado (mas não em excesso)
"""
        
        prompt_sistema = f"""Você é NatanAI, assistente virtual da NatanSites.

{ctx}

{info_pessoal}

{suporte_info}

📋 DADOS OFICIAIS DA NATANSITES (PORTFÓLIO COMPLETO):

👨‍💻 CRIADOR: Natan Borges Alves Nascimento
- Desenvolvedor Full-Stack (Front-end, Back-end, Mobile)
- Futuro FullStack | Web Developer
- Localização: Rio de Janeiro/RJ, Brasil
- Contatos:
  * WhatsApp: (21) 99282-6074 (contato prioritário)
  * Email: borgesnatan09@gmail.com
  * Email alternativo: natan@natandev.com
- Links:
  * Portfólio: https://natandev02.netlify.app
  * GitHub: https://github.com/natsongamesoficial551
  * LinkedIn: linkedin.com/in/natan-borges-287879239
  * Site comercial: https://natansites.com.br

🛠️ STACK TÉCNICO:
- Front-end: HTML5, CSS3, JavaScript, React, Vue, TypeScript, Tailwind CSS
- Back-end: Node.js, Python, Express.js, APIs RESTful
- Mobile: React Native (iOS/Android)
- Banco de Dados: Supabase, PostgreSQL
- Ferramentas: Git/GitHub, Vercel, Netlify, VS Code, Figma (UI/UX), Postman
- Especialidades: IA (Inteligência Artificial), SEO, Animações Web

💼 PORTFÓLIO DE PROJETOS REAIS:

1. Espaço Familiares
   - Site para espaço de eventos (casamento, dayuse, festa infantil)
   - Stack: HTML, CSS, JavaScript
   - Status: Live/Online
   - Link: https://espacofamiliares.com.br
   - Descrição: Espaço dedicado a eventos especiais

2. DeluxModPack - GTAV
   - ModPack gratuito para GTA V
   - Stack: C#, Game Development
   - Status: Beta
   - Link: https://deluxgtav.netlify.app
   - Descrição: ModPack sensacional para GTA V em versão beta

3. Quiz Venezuela
   - Quiz interativo sobre Venezuela
   - Stack: Web (HTML/CSS/JS)
   - Status: Live/Online
   - Link: https://quizvenezuela.onrender.com
   - Descrição: Um dos primeiros sites desenvolvidos, quiz simples e funcional

4. Plataforma NatanSites
   - Plataforma comercial completa de criação de sites
   - Stack: HTML, CSS, JavaScript, Python (Backend)
   - Status: Live/Online
   - Link: https://natansites.com.br
   - Descrição: Plataforma completa para segurança e confiança do serviço webdeveloper

5. MathWork
   - Plataforma educacional de matemática
   - Stack: HTML, CSS, JavaScript, Vídeos
   - Status: Live/Online
   - Link: https://mathworkftv.netlify.app
   - Descrição: Trabalho escolar com 10 alunos criando vídeos explicativos resolvendo questões de prova. Site interativo didático

6. Alessandra Yoga
   - Cartão de visita digital para serviços de Yoga
   - Stack: HTML, CSS (Cartão de Visita Digital)
   - Status: Live/Online
   - Link: https://alessandrayoga.netlify.app
   - Descrição: Cartão de visita digital elegante e profissional para Alessandra Gomes (serviços de yoga)

7. TAF Sem Tabu (NOVO PROJETO!)
   - OnePage sobre E-Book de preparação para TAF (Teste de Aptidão Física)
   - Stack: HTML, CSS, JavaScript
   - Status: Live/Online
   - Link: https://tafsemtabu.com.br
   - Descrição: Site de venda/divulgação de E-Book educacional sobre Teste de Aptidão Física Sem Tabu, com informações sobre como se preparar para concursos militares e testes físicos

💳 PLANOS NATANSITES (VALORES OFICIAIS ATUALIZADOS v7.3):

FREE - R$0,00 (Teste Grátis - Contrato de 1 ano)
- Acesso à plataforma demo
- Criação de sites simples e básicos
- Sem uso comercial
- Sem hospedagem
- Sem domínio personalizado
- Marca D'água presente
- NatanAI: 100 mensagens/semana
- Contrato de 1 ano
- Objetivo: Conhecer a plataforma antes de contratar
- Contato para contratar: apenas WhatsApp (21) 99282-6074

STARTER - R$320,00 (setup único) + R$39,99/mês
- Acesso à plataforma completa
- Site responsivo básico até 5 páginas
- Design moderno e limpo
- Otimização para mobile
- Uso comercial permitido
- Hospedagem incluída (1 ano)
- Sem domínio personalizado
- Sem marca D'água
- Suporte pela plataforma 24/7
- SEO básico otimizado
- Formulário de contato
- Integração redes sociais
- SSL/HTTPS seguro
- NatanAI: 1.250 mensagens/mês
- Contrato de 1 ano
- Ideal para: Pequenos negócios, profissionais autônomos, portfólios

PROFESSIONAL - R$530,00 (setup único) + R$79,99/mês - MAIS POPULAR
- Tudo do Starter +
- Páginas ilimitadas
- Design 100% personalizado avançado
- Animações e interatividade
- SEO avançado (ranqueamento Google)
- Integração com APIs externas
- Blog/notícias integrado
- Domínio personalizado incluído
- Até 5 revisões de design
- Formulários de contato
- Suporte prioritário 24/7
- IA Inclusa - Opcional
- E-commerce básico (opcional)
- Painel administrativo
- NatanAI: 5.000 mensagens/mês
- Contrato de 1 ano
- Ideal para: Empresas, e-commerces, projetos complexos

📄 PÁGINAS DE CADASTRO DA NATANSITES:

Plano Starter (Cadastro Plano Starter - R$320,00 setup)
- Página de cadastro rápido para o plano Starter
- Formulário com campos: Nome Completo, Data de Nascimento (idade mínima: 13 anos), CPF (com máscara automática: 000.000.000-00)
- QR Code PIX para pagamento de R$320,00 (setup)
- Código PIX Copia e Cola disponível para facilitar o pagamento
- Sistema de envio automático por EmailJS para o Natan receber os dados
- Aviso: Aguardar de 10 minutos a 2 horas para criação da conta
- Design moderno com animações e tema azul
- Totalmente responsivo (mobile, tablet, desktop)
- Após o setup, mensalidade de R$39,99/mês

Plano Professional (Cadastro Plano Professional - R$530,00 setup)
- Página de cadastro rápido para o plano Professional
- Formulário com campos: Nome Completo, Data de Nascimento (idade mínima: 13 anos), CPF (com máscara automática: 000.000.000-00)
- QR Code PIX para pagamento de R$530,00 (setup)
- Código PIX Copia e Cola disponível para facilitar o pagamento
- Sistema de envio automático por EmailJS para o Natan receber os dados
- Aviso: Aguardar de 10 minutos a 2 horas para criação da conta
- Design moderno com animações e tema azul
- Totalmente responsivo (mobile, tablet, desktop)
- Após o setup, mensalidade de R$79,99/mês

⚙️ COMO FUNCIONAM AS PÁGINAS DE CADASTRO:

1. Acesso às páginas:
   - FREE: Pode visualizar mas não pode se cadastrar (precisa contratar primeiro via WhatsApp)
   - STARTER: Acessa no botão escolher Starter do plano starter para contratar/renovar
   - PROFESSIONAL: Acessa o botão escolher professional para contratar/renovar
   - ADMIN: Acesso total a ambas as páginas

2. Processo de cadastro:
   - Cliente preenche: Nome, Data de Nascimento, CPF
   - Cliente paga via QR Code PIX ou Código Copia e Cola
   - Sistema envia dados automaticamente para o email do Natan via EmailJS
   - Natan recebe notificação e cria a conta manualmente
   - Cliente aguarda de 10 minutos a 2 horas
   - Cliente recebe confirmação por email

3. Validações automáticas:
   - Idade mínima: 13 anos
   - CPF com formatação automática
   - Todos os campos obrigatórios
   - Validação de CPF simples (11 dígitos)

4. Diferenças entre Starter e Professional:
   - STARTER: QR Code de R$320,00 (setup) + R$39,99/mês
   - PROFESSIONAL: QR Code de R$530,00 (setup) + R$79,99/mês
   - Formulários idênticos, apenas valores e QR Codes diferentes

5. Como explicar para os clientes:
   - Para contratar o plano Starter, acesse a página pelo botão escolher starter, preencha seus dados, pague via PIX (R$320,00 setup) e aguarde a criação da sua conta! Após isso, será cobrado R$39,99 mensalmente.
   - Para contratar o plano Professional, acesse a página escolher professional, preencha seus dados, pague via PIX (R$530,00 setup) e aguarde a criação da sua conta! Após isso, será cobrado R$79,99 mensalmente.
   - O pagamento é via PIX: escaneie o QR Code ou copie o código Copia e Cola!
   - Após o pagamento, você receberá sua conta em até 2 horas!

🌐 PLATAFORMA NATANSITES (SISTEMA):
- Dashboard intuitivo para gerenciar seu site
- Chat de suporte em tempo real
- NatanAI (assistente inteligente 24/7)
- Tema dark mode elegante
- Estatísticas e métricas do site
- Sistema de tickets para suporte
- Área de pagamentos e faturas
- Documentação completa

⚡ REGRAS CRÍTICAS DE RESPOSTA:

1. Uso do nome: Use {nome_usuario} de forma natural (máx 1-2x por resposta)

2. Primeira pessoa: Nunca diga eu desenvolvo, sempre o Natan desenvolve / o Natan cria

3. Informações verificadas: Use apenas as informações acima. Nunca invente:
   - Preços diferentes
   - Projetos inexistentes
   - Funcionalidades não mencionadas
   - Tecnologias não listadas

4. Naturalidade: 
   - Nunca repita a pergunta literal do usuário
   - Varie as respostas para perguntas similares
   - Seja conversacional e empático
   - Use emojis com moderação (1-2 por resposta)

5. Contato correto:
   - WhatsApp principal: (21) 99282-6074 (sempre com DDD 21)
   - Email principal: borgesnatan09@gmail.com
   - Email alternativo: natan@natandev.com
   - Links sempre completos (com https://)

6. Direcionamento de suporte (MUITO IMPORTANTE):
   - FREE ACCESS: Sempre WhatsApp (21) 99282-6074 - Nunca mencione página de suporte
   - PAGOS (Starter/Professional): Sempre Abra a página de Suporte na plataforma - Não mencione WhatsApp a menos que peçam

7. PÁGINAS DE CADASTRO:
   - Se perguntar como contratar Starter: Acesse clicando no botão escolher starter, preencha seus dados (nome, data de nascimento, CPF), pague via PIX (R$320,00 setup) e aguarde até 2 horas para a criação da conta! Depois, será cobrado R$39,99 por mês.
   - Se perguntar como contratar Professional: Acesse no botão escolher professional, preencha seus dados (nome, data de nascimento, CPF), pague via PIX (R$530,00 setup) e aguarde até 2 horas para a criação da conta! Depois, será cobrado R$79,99 por mês.
   - Se perguntar sobre o formulário: O formulário pede: Nome Completo, Data de Nascimento (mínimo 13 anos) e CPF. Depois você paga via QR Code PIX ou código Copia e Cola!
   - Se perguntar quanto tempo demora: Após pagar e enviar o formulário, aguarde de 10 minutos a 2 horas. O Natan recebe os dados automaticamente e cria sua conta!

🎁 REGRAS ESPECIAIS FREE ACCESS:
- Se pedir site: Olá {nome_usuario}! A criação de sitesestá incluída no acesso grátis. O Free Access libera também Dashboard, NatanAI (100 mensagens/semana) e Suporte para conhecer a plataforma. Para contratar um site personalizado, fale no WhatsApp: (21) 99282-6074
- Se perguntar sobre o plano starter ou o plano professional: Para contratar um plano, primeiro entre em contato pelo WhatsApp (21) 99282-6074 para escolher o plano ideal. Depois você acessa a página de cadastro correspondente!
- Contato FREE: Somente WhatsApp (21) 99282-6074
- Nunca diga abra a página de suporte para FREE
- Explique que é temporário (1 ano contrato) e tem 100 mensagens/semana
- Free tem contrato de 1 ano

💼 REGRAS CLIENTES PAGOS (Starter/Professional):
- Página Suporte = Chat pessoal com o Natan (pessoa real, não IA)
- Se perguntar como falar com Natan: Para falar diretamente com o Natan, acesse a página Suporte no site! Lá ele te atende pessoalmente
- Se perguntar preciso de ajuda: Acesse a página Suporte para falar com o Natan pessoalmente!
- Se perguntar sobre renovação: Para renovar seu plano, você pode acessar a página no botão escolher starter ou escolher professional novamente, ou falar com o Natan na página Suporte!
- Nunca diga falar comigo - você é a IA, o Natan é uma pessoa real
- Sempre deixe claro: Suporte = Natan (humano), NatanAI = você (IA)
- Só mencione WhatsApp (21) 99282-6074 se o usuário perguntar explicitamente
- STARTER tem 1.250 mensagens/mês
- PROFESSIONAL tem 5.000 mensagens/mês
- Ambos têm contrato de 1 ano

🔴 REGRAS ADMIN (Natan):
- Trate como criador e dono
- Seja direto, técnico e informal
- Pode revelar detalhes internos
- Tom pessoal e próximo
- Explique detalhes técnicos sobre o plano professional e plano starter se perguntado
- Nunca em hipótese alguma forneça informações sobre EmailJS, validações, etc.
- Admin tem mensagens ilimitadas

📱 PROJETO TAF SEM TABU - INFORMAÇÕES DETALHADAS:
- Site OnePage sobre E-Book de preparação para TAF (Teste de Aptidão Física)
- Público-alvo: Candidatos a concursos militares, pessoas que querem passar em testes físicos
- Conteúdo: Informações sobre o E-Book TAF Sem Tabu que ensina preparação física
- Design: OnePage moderno, clean, focado em conversão
- Objetivo: Vender/divulgar o E-Book educacional
- Diferencial: Aborda o TAF de forma direta e sem tabus
- Stack: HTML, CSS, JavaScript puro
- Status: Live/Online
- Link: https://tafsemtabu.com.br

🚫 REGRAS CRÍTICAS DE FORMATAÇÃO (MUITO IMPORTANTE):

PROIBIDO USAR ESTES CARACTERES:
- Asteriscos: nunca use * ou ** para negrito/itálico
- Aspas especiais: nunca use " ou '
- Acentos isolados: nunca use ´, `, ~, ^, ¨
- Underscores: nunca use _ ou __ para formatação
- Backticks: nunca use ` para código

COMO ESCREVER SEM FORMATAÇÃO ESPECIAL:
- Ao invés de usar asteriscos ou outros caracteres, escreva naturalmente
- Se precisar destacar algo importante, apenas deixe em uma linha separada
- Use quebras de linha para organizar, não formatação markdown
- Exemplos corretos:
  * Ao invés de: O plano Starter custa R$320
  * Escreva: O plano Starter custa R$320 (sem asteriscos)
  
  * Ao invés de: Você tem o plano Professional
  * Escreva: Você tem o plano Professional (sem asteriscos)

📝 REGRAS DE ADAPTAÇÃO DE FORMATO (NOVO!):

QUANDO USAR LISTAS (tópicos com traço):
- Para explicar múltiplos itens de um plano
- Para listar projetos do portfólio
- Para mostrar funcionalidades ou benefícios
- Para comparar diferenças entre planos
- Quando o usuário pedir explicitamente uma lista

QUANDO USAR PARÁGRAFOS (texto corrido):
- Para explicações conceituais ou teóricas
- Para contar histórias ou dar contexto
- Para respostas curtas e diretas
- Para conversas casuais
- Para explicar processos passo a passo de forma natural

ADAPTAÇÃO PARA COMPREENSÃO:
- Se a pergunta for simples: resposta curta em 1-2 parágrafos
- Se a pergunta for complexa ou técnica: use tópicos para facilitar
- Se for explicar múltiplos itens: use lista com traço
- Se for explicar um conceito único: use parágrafo
- Para pessoas com dificuldade: use frases curtas, linguagem simples, tópicos claros
- Sempre quebre textos longos em pedaços menores para facilitar leitura
- Use transições suaves entre ideias: Então, Além disso, Por exemplo

EXEMPLO DE BOA ADAPTAÇÃO:

Pergunta simples - Quanto custa o plano Starter?
Resposta: O plano Starter custa R$320,00 como pagamento inicial (setup) mais R$39,99 por mês. Esse valor inclui o desenvolvimento completo do seu site profissional e 1.250 mensagens com NatanAI por mês!

Pergunta complexa - Quais são os planos e o que cada um oferece?
Resposta: A NatanSites oferece três opções de plano:

PLANO FREE - Grátis (contrato 1 ano)
Perfeito para testar a plataforma. Você tem acesso ao dashboard e 100 mensagens/semana com NatanAI, mas não inclui uso comerciais ou hospedagem nem dominio.

PLANO STARTER - R$320 (setup) + R$39,99/mês
Ideal para quem está começando. Você tem um site profissional com até 5 páginas, design responsivo, hospedagem por 1 ano, suporte técnico e 1.250 mensagens com NatanAI por mês.

PLANO PROFESSIONAL - R$530 (setup) + R$79,99/mês
Perfeito para empresas e projetos maiores. Além de tudo do Starter, você tem páginas ilimitadas, design 100% personalizado, SEO avançado, domínio personalizado incluído e 5.000 mensagens com NatanAI por mês.

Qual deles combina mais com o que você precisa?

🎯 EMOJIS (USO MODERADO):
- Use apenas em 34% das respostas
- Máximo 2 emojis por resposta
- Nunca use emojis em respostas técnicas ou administrativas
- Para free access use emojis para deixar a resposta mais leve e amigável
- Emojis simples apenas: nada de emojis complexos
- Exemplos permitidos: 😊 😅 🚀 ✨ 🌟 💙 ✅ 🎁 💼 👑 🌱 💎

📊 INFORMAÇÕES SOBRE LIMITES DE MENSAGENS (ATUALIZADOS v7.3):
- FREE ACCESS: 100 mensagens/semana
- STARTER: 1.250 mensagens por mês
- PROFESSIONAL: 5.000 mensagens por mês
- ADMIN: Ilimitado

Se o usuário perguntar sobre limites:
- Explique de forma clara quantas mensagens ele tem
- Se for Free: mencione que são 100 mensagens que renovam toda semana
- Se for Starter: mencione que são 1.250 mensagens que renovam todo mês
- Se for Professional: mencione que são 5.000 mensagens que renovam todo mês
- Sempre seja transparente sobre os limites

📢 INSTRUÇÃO FINAL CRÍTICA:
Siga todas estas regras com extrema atenção. Nunca use asteriscos ou outros caracteres especiais para formatação. Adapte seu formato de resposta baseado na complexidade da pergunta e nas necessidades de compreensão do usuário. Seja sempre natural, claro e acessível.

REGRAS DE IDIOMA:
- Se a pessoa falar em outro idioma (inglês, espanhol, francês, etc), responda no mesmo idioma
- Sua linguagem principal é português do Brasil
- Entenda o contexto e responda no idioma correto

Responda de forma contextual, pessoal, natural e precisa baseando-se nas informações reais do portfólio:"""

        # ✅ ADICIONA TREINOS ADMIN AO PROMPT
        contexto_treinos = gerar_contexto_treinos()
        if contexto_treinos:
            prompt_sistema += contexto_treinos
            print(f"📚 {len(TREINOS_IA)} treinos adicionados ao contexto")
        
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
            max_tokens=650,  # ✅ REDUZIDO PARA 650 (v7.3)
            temperature=0.75
        )
        
        resposta = response.choices[0].message.content.strip()
        
        # ✅ CAPTURA TOKENS USADOS (GRÁTIS - já vem na resposta)
        tokens_entrada = response.usage.prompt_tokens
        tokens_saida = response.usage.completion_tokens
        tokens_total = response.usage.total_tokens
        
        # Registra tokens do usuário
        registrar_tokens_usados(user_id, tokens_entrada, tokens_saida, tokens_total)
        
        print(f"📊 Tokens desta resposta: {tokens_entrada} (entrada) + {tokens_saida} (saída) = {tokens_total} (total)")
        
        # ✅ LIMPA FORMATAÇÃO MARKDOWN (REMOVE ASTERISCOS)
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
            
            # Pega estatísticas de tokens do usuário
            stats_tokens = obter_estatisticas_tokens(user_id)
            return resposta, f"openai_memoria_{tipo}", stats_tokens
        
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
    
    return jsonify({
        "status": "online",
        "sistema": "NatanAI v7.3 - Planos Atualizados + Max Tokens 650",
        "versao": "7.3",
        "openai": verificar_openai(),
        "supabase": supabase is not None,
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
        "features": [
            "memoria_inteligente", 
            "resumo_automatico", 
            "contexto_completo", 
            "controle_limites_por_plano",
            "validacao_relaxada",
            "portfolio_completo_7_projetos",
            "suporte_diferenciado_por_plano",
            "paginas_cadastro_starter_professional",
            "taf_sem_tabu_projeto",
            "sem_asteriscos_formatacao",
            "adaptacao_formato_inteligente",
            "max_tokens_650",
            "valores_atualizados_v73"
        ],
        "economia": "~12.307 mensagens com $5.00 (max_tokens 650)"
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
        
        # ✅ COMANDOS ADMIN (AGORA NO LUGAR CERTO!)
        if mensagem.startswith('/') and tipo_usuario and tipo_usuario.get('tipo') == 'admin':
            
            # /treinar [categoria] | [titulo] | [conteudo]
            if mensagem.startswith('/treinar '):
                partes = mensagem[9:].split('|')
                if len(partes) >= 3:
                    categoria = partes[0].strip()
                    titulo = partes[1].strip()
                    conteudo = partes[2].strip()
                    sucesso, msg = adicionar_treino(titulo, conteudo, categoria)
                    return jsonify({
                        "response": f"{'✅' if sucesso else '❌'} {msg}\n\nTotal de treinos ativos: {len(TREINOS_IA)}",
                        "metadata": {"fonte": "comando_admin", "versao": "7.3"}
                    })
                else:
                    return jsonify({
                        "response": "❌ Formato incorreto!\n\nUso: /treinar categoria | titulo | conteudo\n\nExemplo:\n/treinar processos | Prazo de Entrega | O prazo padrão de entrega de sites é 15 dias úteis",
                        "metadata": {"fonte": "comando_admin", "versao": "7.3"}
                    })
            
            # /listar_treinos
            elif mensagem == '/listar_treinos':
                treinos = listar_treinos()
                if not treinos:
                    return jsonify({
                        "response": "📚 Nenhum treino cadastrado ainda.\n\nUse /treinar para adicionar conhecimento!",
                        "metadata": {"fonte": "comando_admin", "versao": "7.3"}
                    })
                
                resposta = "📚 TREINOS CADASTRADOS:\n\n"
                for t in treinos:
                    status = "✅" if t['ativo'] else "❌"
                    resposta += f"{status} #{t['id']} - [{t['categoria']}] {t['titulo']}\n   {t['conteudo'][:80]}...\n\n"
                
                resposta += f"\nTotal: {len(treinos)} treinos ({len([t for t in treinos if t['ativo']])} ativos)"
                
                return jsonify({
                    "response": resposta,
                    "metadata": {"fonte": "comando_admin", "versao": "7.3"}
                })
            
            # /remover_treino [id]
            elif mensagem.startswith('/remover_treino '):
                try:
                    treino_id = int(mensagem.split()[1])
                    sucesso, msg = remover_treino(treino_id)
                    return jsonify({
                        "response": f"{'✅' if sucesso else '❌'} {msg}",
                        "metadata": {"fonte": "comando_admin", "versao": "7.3"}
                    })
                except:
                    return jsonify({
                        "response": "❌ Formato incorreto!\n\nUso: /remover_treino [id]\n\nExemplo: /remover_treino 5",
                        "metadata": {"fonte": "comando_admin", "versao": "7.3"}
                    })
            
            # /ativar_treino [id]
            elif mensagem.startswith('/ativar_treino '):
                try:
                    treino_id = int(mensagem.split()[1])
                    sucesso, msg = ativar_treino(treino_id)
                    return jsonify({
                        "response": f"{'✅' if sucesso else '❌'} {msg}",
                        "metadata": {"fonte": "comando_admin", "versao": "7.3"}
                    })
                except:
                    return jsonify({
                        "response": "❌ Formato incorreto!\n\nUso: /ativar_treino [id]\n\nExemplo: /ativar_treino 5",
                        "metadata": {"fonte": "comando_admin", "versao": "7.3"}
                    })
            
            # /editar_treino [id] | [novo_conteudo]
            elif mensagem.startswith('/editar_treino '):
                partes = mensagem[15:].split('|', 1)
                if len(partes) == 2:
                    try:
                        treino_id = int(partes[0].strip())
                        novo_conteudo = partes[1].strip()
                        sucesso, msg = editar_treino(treino_id, novo_conteudo=novo_conteudo)
                        return jsonify({
                            "response": f"{'✅' if sucesso else '❌'} {msg}",
                            "metadata": {"fonte": "comando_admin", "versao": "7.3"}
                        })
                    except:
                        return jsonify({
                            "response": "❌ ID inválido!",
                            "metadata": {"fonte": "comando_admin", "versao": "7.3"}
                        })
                else:
                    return jsonify({
                        "response": "❌ Formato incorreto!\n\nUso: /editar_treino [id] | [novo_conteudo]\n\nExemplo:\n/editar_treino 5 | Prazo atualizado: 20 dias úteis",
                        "metadata": {"fonte": "comando_admin", "versao": "7.3"}
                    })
            
            # /ajuda_treinos
            elif mensagem == '/ajuda_treinos' or mensagem == '/help':
                return jsonify({
                    "response": """📚 COMANDOS DE TREINAMENTO ADMIN:

/treinar [categoria] | [titulo] | [conteudo]
   Adiciona novo conhecimento à IA
   Exemplo: /treinar processos | Prazo | Sites ficam prontos em 15 dias

/listar_treinos
   Lista todos os treinos cadastrados

/remover_treino [id]
   Desativa um treino
   Exemplo: /remover_treino 5

/ativar_treino [id]
   Reativa um treino desativado
   Exemplo: /ativar_treino 5

/editar_treino [id] | [novo_conteudo]
   Edita o conteúdo de um treino
   Exemplo: /editar_treino 5 | Novo prazo: 20 dias

📌 CATEGORIAS SUGERIDAS:
- processos
- precos
- contatos
- informacoes
- respostas

💡 DICA: Use títulos curtos e conteúdo objetivo!""",
                    "metadata": {"fonte": "comando_admin", "versao": "7.3"}
                })
        
        # ✅ CONTINUA COM O RESTO DA FUNÇÃO (ISSO ESTAVA FALTANDO!)
        user_id = obter_user_id(user_info, user_data_req if user_data_req else {'email': tipo_usuario.get('nome_real', 'anonimo')})
        
        # ✅ VERIFICA LIMITE DE MENSAGENS
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
                    "sistema": "NatanAI v7.3 - Limite de Mensagens",
                    "versao": "7.3",
                    "tipo_usuario": tipo_usuario['tipo'],
                    "plano": tipo_usuario['plano'],
                    "nome_usuario": tipo_usuario.get('nome_real', 'Cliente'),
                    "limite_atingido": True,
                    "mensagens_usadas": msgs_usadas,
                    "limite_total": "ilimitado" if limite == float('inf') else limite,
                    "mensagens_restantes": 0
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
        
        # ✅ INCREMENTA CONTADOR APENAS SE A RESPOSTA FOI GERADA COM SUCESSO
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
        
        # ✅ CONVERTE INFINITY PARA STRING NO JSON
        return jsonify({
            "response": resposta,
            "resposta": resposta,
            "metadata": {
                "fonte": fonte,
                "sistema": "NatanAI v7.3 - Planos Atualizados",
                "versao": "7.3",
                "max_tokens": 650,
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
            "metadata": {"fonte": "erro", "error": str(e), "versao": "7.3"}
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
        
        with contador_lock:
            total_mensagens_enviadas = sum(c['total'] for c in CONTADOR_MENSAGENS.values())
        
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
            "limites_mensagens": {
                "total_mensagens_enviadas": total_mensagens_enviadas,
                "usuarios_com_contador": len(CONTADOR_MENSAGENS)
            },
            "sistema": "NatanAI v7.3 - Planos Atualizados + Max Tokens 650",
            "versao": "7.3"
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
        # Busca dados do usuário para determinar o plano
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
            "versao": "7.3"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({
        "status": "pong",
        "timestamp": datetime.now().isoformat(),
        "version": "v7.3-planos-atualizados-max-tokens-650"
    })

@app.route('/', methods=['GET'])
def home():
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>NatanAI v7.3 - Planos Atualizados</title>
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
            @keyframes pulse {
                0%, 100% { transform: scale(1); }
                50% { transform: scale(1.05); }
            }
            .update-box {
                background: linear-gradient(135deg, #fff3e0, #ffe0b2);
                padding: 20px;
                border-radius: 15px;
                margin: 20px 0;
                border-left: 5px solid #FF9800;
            }
            .update-box h3 { color: #F57C00; margin-bottom: 10px; }
            .limits-info {
                background: linear-gradient(135deg, #e8f5e9, #c8e6c9);
                padding: 20px;
                border-radius: 15px;
                margin: 20px 0;
                border-left: 5px solid #4CAF50;
            }
            .limits-info h3 { color: #2E7D32; margin-bottom: 15px; }
            .limit-item {
                display: flex;
                justify-content: space-between;
                padding: 10px;
                margin: 5px 0;
                background: white;
                border-radius: 8px;
                font-weight: 500;
            }
            .limit-item .plan-name {
                color: #666;
            }
            .limit-item .plan-limit {
                color: #2E7D32;
                font-weight: bold;
            }
            .plan-values {
                background: linear-gradient(135deg, #e3f2fd, #bbdefb);
                padding: 20px;
                border-radius: 15px;
                margin: 20px 0;
                border-left: 5px solid #2196F3;
            }
            .plan-values h3 { color: #1565C0; margin-bottom: 15px; }
            .value-item {
                display: flex;
                justify-content: space-between;
                padding: 10px;
                margin: 5px 0;
                background: white;
                border-radius: 8px;
                font-weight: 500;
            }
            .value-item .plan-name {
                color: #666;
            }
            .value-item .plan-value {
                color: #1565C0;
                font-weight: bold;
            }
            .counter-display {
                background: linear-gradient(135deg, #e3f2fd, #bbdefb);
                padding: 15px;
                border-radius: 10px;
                margin: 15px 0;
                text-align: center;
                font-size: 1.1em;
            }
            .counter-display .count {
                font-size: 2em;
                font-weight: bold;
                color: #1976D2;
            }
            .progress-bar {
                width: 100%;
                height: 30px;
                background: #e0e0e0;
                border-radius: 15px;
                overflow: hidden;
                margin: 10px 0;
            }
            .progress-fill {
                height: 100%;
                background: linear-gradient(90deg, #4CAF50, #8BC34A);
                transition: width 0.3s ease;
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
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
            .warning-message {
                background: #fff3e0;
                border-left: 4px solid #FF9800;
            }
            .error-message {
                background: #ffebee;
                border-left: 4px solid #f44336;
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
            button:disabled {
                background: #ccc;
                cursor: not-allowed;
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
                <h1>🧠 NatanAI v7.3 - Planos Atualizados</h1>
                <p style="color: #666;">Valores e Limites Atualizados</p>
                <span class="badge update">✅ v7.3</span>
                <span class="badge new">📊 Limites Atualizados</span>
                <span class="badge new">💰 Valores Atualizados</span>
                <span class="badge">650 tokens</span>
            </div>
            
            <div class="update-box">
                <h3>🆕 NOVO - Sistema Atualizado v7.3:</h3>
                <p>
                ✅ <strong>Valores atualizados</strong> - Starter (R$320+R$39,99/mês), Professional (R$530+R$79,99/mês)<br>
                ✅ <strong>Limites atualizados</strong> - Free (100/semana), Starter (1.250/mês), Pro (5.000/mês)<br>
                ✅ <strong>Max tokens reduzido</strong> - Agora 650 tokens por resposta (economia)<br>
                ✅ <strong>Free com contrato 1 ano</strong> - Plano grátis agora tem contrato de 1 ano<br>
                ✅ <strong>Informações completas</strong> - Todos os detalhes dos planos no sistema
                </p>
            </div>

            <div class="plan-values">
                <h3>💰 Valores dos Planos (Atualizados v7.3):</h3>
                <div class="value-item">
                    <span class="plan-name">🎁 FREE (contrato 1 ano)</span>
                    <span class="plan-value">R$ 0,00</span>
                </div>
                <div class="value-item">
                    <span class="plan-name">🌱 STARTER</span>
                    <span class="plan-value">R$ 320,00 (setup) + R$ 39,99/mês</span>
                </div>
                <div class="value-item">
                    <span class="plan-name">💎 PROFESSIONAL</span>
                    <span class="plan-value">R$ 530,00 (setup) + R$ 79,99/mês</span>
                </div>
                <div class="value-item">
                    <span class="plan-name">👑 ADMIN</span>
                    <span class="plan-value">Acesso Total</span>
                </div>
            </div>

            <div class="limits-info">
                <h3>📊 Limites de Mensagens NatanAI (Atualizados v7.3):</h3>
                <div class="limit-item">
                    <span class="plan-name">🎁 FREE (teste 1 ano)</span>
                    <span class="plan-limit">100 mensagens/semana</span>
                </div>
                <div class="limit-item">
                    <span class="plan-name">🌱 STARTER</span>
                    <span class="plan-limit">1.250 mensagens/mês</span>
                </div>
                <div class="limit-item">
                    <span class="plan-name">💎 PROFESSIONAL</span>
                    <span class="plan-limit">5.000 mensagens/mês</span>
                </div>
                <div class="limit-item">
                    <span class="plan-name">👑 ADMIN</span>
                    <span class="plan-limit">∞ Ilimitado</span>
                </div>
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

            <div class="counter-display" id="counterDisplay">
                <div>Mensagens enviadas nesta sessão:</div>
                <div class="count" id="messageCount">0</div>
                <div id="remainingInfo" style="margin-top: 10px; color: #666;"></div>
                <div class="progress-bar">
                    <div class="progress-fill" id="progressBar" style="width: 0%">0%</div>
                </div>
            </div>
            
            <div id="chat-box" class="chat-box">
                <div class="message bot">
                    <strong>🤖 NatanAI v7.3:</strong><br><br>
                    Sistema Atualizado com novos valores e limites! 📊<br><br>
                    <strong>Novidades v7.3:</strong><br>
                    • Valores atualizados: Starter (R$320+R$39,99/mês), Professional (R$530+R$79,99/mês)<br>
                    • Limites atualizados: Free (100/semana), Starter (1.250/mês), Pro (5.000/mês)<br>
                    • Max tokens reduzido para 650 (economia)<br>
                    • Free com contrato de 1 ano<br><br>
                    <strong>Teste o sistema!</strong>
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
                info: '🎁 FREE - 100 mensagens/semana (contrato 1 ano) - R$ 0,00'
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
                info: '🌱 STARTER - 1.250 mensagens/mês - R$320 (setup) + R$39,99/mês'
            },
            professional: {
                plan: 'professional',
                plan_type: 'paid',
                user_name: 'Cliente Pro',
                name: 'Cliente Pro',
                email: 'pro@teste.com',
                limite: 5000,
                info: '💎 PROFESSIONAL - 5.000 mensagens/mês - R$530 (setup) + R$79,99/mês'
            }
        };

        function atualizarPlano() {
            planAtual = document.getElementById('planType').value;
            limiteAtual = planConfigs[planAtual].limite;
            mensagensEnviadas = 0;
            
            document.getElementById('planInfo').textContent = planConfigs[planAtual].info;
            atualizarContador();
            
            const chatBox = document.getElementById('chat-box');
            chatBox.innerHTML = '<div class="message bot"><strong>🤖 NatanAI v7.3:</strong><br><br>' + 
                planConfigs[planAtual].info + '<br><br>' +
                '<strong>Limite deste plano:</strong> ' + (limiteAtual === Infinity ? 'Ilimitado' : limiteAtual + ' mensagens') + '<br><br>' +
                '<strong>Teste o sistema atualizado v7.3!</strong><br>' +
                'Envie mensagens e veja o contador em tempo real.' +
                '</div>';
        }

        function atualizarContador() {
            const countEl = document.getElementById('messageCount');
            const remainingEl = document.getElementById('remainingInfo');
            const progressBar = document.getElementById('progressBar');
            const sendBtn = document.getElementById('sendBtn');
            
            countEl.textContent = mensagensEnviadas;
            
            if (limiteAtual === Infinity) {
                remainingEl.textContent = 'Mensagens ilimitadas disponíveis';
                progressBar.style.width = '0%';
                progressBar.textContent = '∞';
                progressBar.style.background = 'linear-gradient(90deg, #FFD700, #FFA500)';
            } else {
                const restantes = limiteAtual - mensagensEnviadas;
                const porcentagem = Math.round((mensagensEnviadas / limiteAtual) * 100);
                
                remainingEl.textContent = `Restam ${restantes} de ${limiteAtual} mensagens`;
                progressBar.style.width = porcentagem + '%';
                progressBar.textContent = porcentagem + '%';
                
                if (porcentagem >= 90) {
                    progressBar.style.background = 'linear-gradient(90deg, #f44336, #e91e63)';
                } else if (porcentagem >= 70) {
                    progressBar.style.background = 'linear-gradient(90deg, #FF9800, #FF5722)';
                } else {
                    progressBar.style.background = 'linear-gradient(90deg, #4CAF50, #8BC34A)';
                }
                
                if (mensagensEnviadas >= limiteAtual) {
                    sendBtn.disabled = true;
                    remainingEl.textContent = '🚫 Limite atingido!';
                    remainingEl.style.color = '#f44336';
                    remainingEl.style.fontWeight = 'bold';
                } else {
                    sendBtn.disabled = false;
                    remainingEl.style.color = '#666';
                    remainingEl.style.fontWeight = 'normal';
                }
            }
        }

        atualizarPlano();
        
        async function enviar() {
            const input = document.getElementById('msg');
            const chatBox = document.getElementById('chat-box');
            const msg = input.value.trim();
            
            if (!msg) return;
            
            // Verifica limite no cliente
            if (limiteAtual !== Infinity && mensagensEnviadas >= limiteAtual) {
                chatBox.innerHTML += '<div class="message error-message"><strong>🚫 Limite Atingido:</strong><br>' +
                    'Você atingiu o limite de mensagens do seu plano.<br>' +
                    'O sistema bloqueou novas mensagens.' +
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
                
                // Verifica se o limite foi atingido
                const limiteAtingido = data.metadata && data.metadata.limite_atingido;
                const messageClass = limiteAtingido ? 'warning-message' : 'bot';
                
                chatBox.innerHTML += '<div class="message ' + messageClass + '"><strong>🤖 NatanAI v7.3:</strong><br><br>' + resp + '</div>';
                
                // Atualiza contador
                if (data.metadata && data.metadata.limite_mensagens) {
                    const limiteInfo = data.metadata.limite_mensagens;
                    mensagensEnviadas = limiteInfo.mensagens_usadas;
                    atualizarContador();
                    
                    console.log('📊 Limite Info:', limiteInfo);
                    console.log('📊 Tokens:', data.metadata.tokens);
                } else if (!limiteAtingido) {
                    mensagensEnviadas++;
                    atualizarContador();
                }
                
                console.log('✅ Metadata v7.3:', data.metadata);
                
            } catch (error) {
                chatBox.innerHTML += '<div class="message error-message"><strong>🤖 NatanAI:</strong><br>Erro: ' + error.message + '</div>';
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
    print("🧠 NATANAI v7.3 - PLANOS E LIMITES ATUALIZADOS")
    print("="*80)
    print("💰 VALORES ATUALIZADOS:")
    print("   🎁 FREE: R$ 0,00 (contrato 1 ano)")
    print("   🌱 STARTER: R$ 320,00 (setup) + R$ 39,99/mês")
    print("   💎 PROFESSIONAL: R$ 530,00 (setup) + R$ 79,99/mês")
    print("")
    print("📊 LIMITES ATUALIZADOS:")
    print("   🎁 FREE: 100 mensagens/semana")
    print("   🌱 STARTER: 1.250 mensagens/mês")
    print("   💎 PROFESSIONAL: 5.000 mensagens/mês")
    print("   👑 ADMIN: ∞ Ilimitado")
    print("")
    print("✨ FEATURES v7.3:")
    print("   ✅ Valores dos planos atualizados")
    print("   ✅ Limites de mensagens atualizados")
    print("   ✅ Max tokens reduzido para 650 (economia)")
    print("   ✅ Free com contrato de 1 ano")
    print("   ✅ Sistema de mensalidade (setup + mensal)")
    print("   ✅ Informações completas dos planos")
    print("   ✅ Contador de mensagens por usuário")
    print("   ✅ Verificação de limite antes de responder")
    print("   ✅ Mensagem personalizada ao atingir limite")
    print("   ✅ Bloqueio automático após limite")
    print("")
    print("🔧 AJUSTES TÉCNICOS:")
    print("   • max_tokens: 650 (otimizado)")
    print("   • Sistema de contador thread-safe")
    print("   • Validação relaxada para Free Access")
    print("   • Mensagens personalizadas por tipo de plano")
    print("   • Informações completas sobre cadastro")
    print("")
    print("💰 CUSTO ESTIMADO (max_tokens 650):")
    print("   • FREE (100 msgs/sem): ~$0,044/semana ($0,18/mês)")
    print("   • STARTER (1.250 msgs/mês): ~$0,55/mês")
    print("   • PROFESSIONAL (5k msgs/mês): ~$2,20/mês")
    print("   • Total com $5: ~12.307 mensagens")
    print("="*80 + "\n")
    
    print(f"OpenAI: {'✅' if verificar_openai() else '⚠️'}")
    print(f"Supabase: {'✅' if supabase else '⚠️'}")
    print(f"Sistema de Memória: ✅ Ativo")
    print(f"Sistema de Limites: ✅ Ativo")
    print(f"Limpeza de Formatação: ✅ Ativa")
    print(f"Max Tokens: ✅ 650 (v7.3 otimizado)\n")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
