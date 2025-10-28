import os
import time
import requests
import warnings
import hashlib
import random
import re
import json
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
from openai import OpenAI
from supabase import create_client, Client

warnings.filterwarnings('ignore')

app = Flask(__name__)
CORS(app)

# ============================================
# 🔧 CONFIGURAÇÃO SUPABASE
# ============================================
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
ADMIN_EMAIL = "natan@natandev.com"

# Inicializa Supabase
supabase: Client = None
try:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase conectado com sucesso!")
except Exception as e:
    print(f"⚠️ Erro ao conectar Supabase: {e}")

# ============================================
# 🔧 CONFIGURAÇÃO OPENAI
# ============================================
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = "gpt-4o-mini"

# Inicializa cliente OpenAI
if OPENAI_API_KEY:
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        print("✅ Cliente OpenAI inicializado com sucesso")
    except Exception as e:
        print(f"⚠️ Erro ao inicializar OpenAI: {e}")
        client = None
else:
    client = None
    print("⚠️ OPENAI_API_KEY não configurada - modo fallback ativo")

RENDER_URL = os.getenv("RENDER_URL", "")

# Cache e dados
CACHE_RESPOSTAS = {}
KNOWLEDGE_BASE = {}
HISTORICO_CONVERSAS = []
PING_INTERVAL = 300

# Lock para thread safety
historico_lock = threading.Lock()

# Auto-ping para manter servidor ativo
def auto_ping():
    while True:
        try:
            if RENDER_URL:
                url = RENDER_URL if RENDER_URL.startswith('http') else f"https://{RENDER_URL}"
                response = requests.get(f"{url}/health", timeout=10)
                print(f"🏓 Auto-ping OK [{response.status_code}]: {datetime.now().strftime('%H:%M:%S')}")
            else:
                requests.get("http://localhost:5000/health", timeout=5)
                print(f"🏓 Auto-ping local: {datetime.now().strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"❌ Erro auto-ping: {e}")
        time.sleep(PING_INTERVAL)

threading.Thread(target=auto_ping, daemon=True).start()

# =============================================================================
# 🔐 FUNÇÕES DE AUTENTICAÇÃO E AUTORIZAÇÃO
# =============================================================================

def verificar_token_supabase(token):
    """Verifica token do Supabase e retorna dados do usuário"""
    try:
        if not token or not supabase:
            return None
        
        # Remove "Bearer " se presente
        if token.startswith("Bearer "):
            token = token[7:]
        
        # Verifica o usuário usando o token
        response = supabase.auth.get_user(token)
        
        if response and response.user:
            return response.user
        
        return None
        
    except Exception as e:
        print(f"❌ Erro ao verificar token: {e}")
        return None

def obter_dados_usuario_completos(user_id):
    """Busca dados completos do usuário no Supabase"""
    try:
        if not supabase:
            return None
        
        # Busca dados da conta do usuário
        response = supabase.table('user_accounts').select('*').eq('user_id', user_id).single().execute()
        
        if response.data:
            return response.data
        
        return None
        
    except Exception as e:
        print(f"⚠️ Erro ao buscar dados do usuário: {e}")
        return None

def determinar_tipo_usuario(user_data):
    """Determina se é Admin, Professional, Starter baseado nos dados"""
    try:
        email = user_data.get('email', '')
        plan = user_data.get('plan', 'starter')
        
        # Verifica se é Admin
        if email == ADMIN_EMAIL:
            return {
                'tipo': 'admin',
                'nome': 'Admin (Natan)',
                'descricao': 'Dono e desenvolvedor da NatanDEV',
                'permissoes': 'total',
                'plano': 'Admin'
            }
        
        # Verifica plano Professional
        if plan == 'professional':
            return {
                'tipo': 'professional',
                'nome': 'Cliente Professional',
                'descricao': 'Cliente com plano Professional (R$ 79,99/mês)',
                'permissoes': 'avancadas',
                'plano': 'Professional'
            }
        
        # Padrão: Starter
        return {
            'tipo': 'starter',
            'nome': 'Cliente Starter',
            'descricao': 'Cliente com plano Starter (R$ 39,99/mês)',
            'permissoes': 'basicas',
            'plano': 'Starter'
        }
        
    except Exception as e:
        print(f"❌ Erro ao determinar tipo de usuário: {e}")
        return {
            'tipo': 'starter',
            'nome': 'Cliente',
            'descricao': 'Cliente padrão',
            'permissoes': 'basicas',
            'plano': 'Starter'
        }

# =============================================================================
# SISTEMA ANTI-ALUCINAÇÃO - VALIDAÇÃO DE RESPOSTAS
# =============================================================================

# Informações OFICIAIS da NatanDEV (fonte da verdade)
INFORMACOES_OFICIAIS = {
    "criador": "Natan Borges Alves Nascimento",
    "profissao": "Web Developer Full-Stack",
    "localizacao": "Rio de Janeiro, Brasil",
    "atendimento": "Todo o Brasil (remoto)",
    "whatsapp": "(21) 99282-6074",
    "instagram": "@nborges.ofc",
    "email": "borgesnatan09@gmail.com",
    "site": "natansites.com.br",
    "portfolio": "natandev02.netlify.app",
    "github": "github.com/natsongamesoficial551",
    "linkedin": "linkedin.com/in/natan-borges-b3a3b5382/",
    "facebook": "facebook.com/profile.php?id=100076973940954",
    
    "planos": {
        "starter": {
            "mensalidade": "R$ 39,99",
            "desenvolvimento_inicial": "R$ 350,00",
            "descricao": "Site responsivo básico, design moderno, hospedagem inclusa"
        },
        "professional": {
            "mensalidade": "R$ 79,99",
            "desenvolvimento_inicial": "R$ 530,00",
            "ia_opcional": "Opcional, precisa organizar preços com o Natan",
            "descricao": "Design personalizado avançado, SEO, APIs, domínio personalizado"
        }
    },
    
    "diferenciais": [
        "Desenvolvimento rápido (estrutura base em 3-4 horas)",
        "Tecnologia de ponta com IA",
        "Qualidade garantida com revisão de código",
        "100% responsivo (mobile, tablet, desktop)",
        "Design moderno com animações"
    ],
    
    "tipos_sites": [
        "Sites comerciais (empresas, consultórios, lojas)",
        "Sites interativos (animações, 3D, quizzes)",
        "Sites personalizados (funcionalidades exclusivas)"
    ],
    
    "projetos": [
        {
            "nome": "Espaço Familiares",
            "url": "espacofamiliares.com.br",
            "tipo": "Site para eventos especiais"
        },
        {
            "nome": "DeluxModPack GTAV",
            "url": "deluxgtav.netlify.app",
            "tipo": "Modpack para GTA V (C#)"
        },
        {
            "nome": "Quiz Venezuela",
            "url": "quizvenezuela.onrender.com",
            "tipo": "Quiz educacional interativo"
        },
        {
            "nome": "WebServiço",
            "url": "webservico.netlify.app",
            "tipo": "Página de serviços"
        },
        {
            "nome": "MathWork",
            "url": "mathworkftv.netlify.app",
            "tipo": "Plataforma educacional de matemática"
        },
        {
            "nome": "Alessandra Yoga",
            "url": "alessandrayoga.netlify.app",
            "tipo": "Cartão de visita digital"
        }
    ],
    
    "tempo_desenvolvimento": "Estrutura base: 3-4 horas | Projeto completo: 1-2 semanas",
    
    "nao_oferecemos": [
        "Sites prontos/templates básicos",
        "Suporte gratuito ilimitado após entrega",
        "Hospedagem gratuita permanente"
    ]
}

# Palavras/frases proibidas (alucinações comuns)
PALAVRAS_PROIBIDAS = [
    "grátis", "gratuito", "sem custo", "de graça", "R$ 0", "0 reais", "free",
    "garantimos primeiro lugar no Google", "100% de conversão", "sucesso garantido",
    "site pronto em 1 hora", "atendimento 24/7", "suporte ilimitado gratuito",
    "empresa com 10 anos", "prêmio internacional"
]

# Padrões suspeitos que indicam alucinação
PADROES_SUSPEITOS = [
    r'R\$\s*0[,.]?00',
    r'grát[ui]s',
    r'garantimos?\s+\d+',
    r'prêmio\s+\w+',
    r'\d+\s+anos\s+de\s+experiência',
    r'fundado\s+em\s+\d{4}',
    r'certificação\s+ISO',
]

def validar_resposta_anti_alucinacao(resposta):
    """Valida resposta para evitar alucinações"""
    problemas = []
    resposta_lower = resposta.lower()
    
    # Verifica palavras proibidas
    for palavra in PALAVRAS_PROIBIDAS:
        if palavra.lower() in resposta_lower:
            problemas.append(f"Palavra proibida: '{palavra}'")
    
    # Verifica padrões suspeitos
    for padrao in PADROES_SUSPEITOS:
        if re.search(padrao, resposta_lower):
            match = re.search(padrao, resposta_lower)
            problemas.append(f"Padrão suspeito: '{match.group()}'")
    
    # Verifica WhatsApp correto
    if "whatsapp" in resposta_lower or "telefone" in resposta_lower:
        if "21 99282-6074" not in resposta and "99282-6074" not in resposta and "(21) 99282-6074" not in resposta:
            if any(num in resposta for num in ["(11)", "(21) 9", "0800"]):
                problemas.append("Número de WhatsApp incorreto")
    
    # Verifica preços corretos
    if "starter" in resposta_lower:
        if "39,99" not in resposta and "39.99" not in resposta:
            problemas.append("Preço Starter incorreto")
    
    if "professional" in resposta_lower:
        if "79,99" not in resposta and "79.99" not in resposta:
            problemas.append("Preço Professional incorreto")
    
    valida = len(problemas) == 0
    return valida, problemas

def limpar_alucinacoes(resposta):
    """Remove ou corrige alucinações detectadas na resposta"""
    resposta_limpa = resposta
    resposta_limpa = re.sub(r'garantimos?\s+\d+%', '', resposta_limpa)
    resposta_limpa = re.sub(r'\d+\s+anos\s+de\s+experiência', 'experiência comprovada', resposta_limpa)
    resposta_limpa = re.sub(r'certificação\s+\w+', '', resposta_limpa)
    return resposta_limpa

# =============================================================================
# ANÁLISE DE INTENÇÃO
# =============================================================================

def analisar_intencao(pergunta):
    """Analisa a intenção das perguntas"""
    try:
        p = pergunta.lower().strip()
        
        intencoes = {
            "precos": 0, "planos": 0, "contato": 0, "portfolio": 0,
            "criar_site": 0, "tipos_sites": 0, "tempo_desenvolvimento": 0,
            "como_funciona": 0, "tecnologias": 0, "responsivo": 0,
            "seo": 0, "diferenciais": 0, "projetos_especificos": 0,
            "sobre_natan": 0, "geral": 0
        }
        
        # Análise de palavras-chave (simplificado)
        if any(word in p for word in ["preço", "quanto custa", "valor", "custo"]):
            intencoes["precos"] += 7
        if any(word in p for word in ["plano", "pacote", "starter", "professional"]):
            intencoes["planos"] += 7
        if any(word in p for word in ["contato", "whatsapp", "telefone", "falar"]):
            intencoes["contato"] += 6
        if any(word in p for word in ["oi", "olá", "tudo bem", "como vai"]):
            intencoes["geral"] += 2
        
        intencao_principal = max(intencoes, key=intencoes.get)
        score_principal = intencoes[intencao_principal]
        
        return intencao_principal if score_principal > 1 else "geral"
    
    except Exception as e:
        print(f"❌ Erro análise intenção: {e}")
        return "geral"

# =============================================================================
# PROCESSAMENTO COM OPENAI + CONTEXTO DO USUÁRIO
# =============================================================================

openai_status_cache = {"status": None, "timestamp": None}

def verificar_openai():
    """Verifica se OpenAI está disponível (COM CACHE de 1 hora)"""
    global openai_status_cache
    
    # Cache de 1 hora
    if openai_status_cache["status"] is not None and openai_status_cache["timestamp"]:
        tempo_passado = (datetime.now() - openai_status_cache["timestamp"]).seconds
        if tempo_passado < 3600:  # 1 hora
            print(f"✅ Usando cache OpenAI (válido por mais {3600-tempo_passado}s)")
            return openai_status_cache["status"]
    
    # Só verifica de verdade se passou 1 hora
    print("🔄 Verificando OpenAI pela primeira vez em 1h...")
    try:
        if not OPENAI_API_KEY or len(OPENAI_API_KEY) < 20:
            status = False
        elif client is None:
            status = False
        else:
            # GASTA créditos aqui, mas só 1x por hora
            client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=[{"role": "user", "content": "ok"}],
                max_tokens=3
            )
            status = True
        
        openai_status_cache = {"status": status, "timestamp": datetime.now()}
        return status
    except Exception as e:
        print(f"❌ OpenAI erro: {e}")
        openai_status_cache = {"status": False, "timestamp": datetime.now()}
        return False
    
def processar_openai_com_contexto(pergunta, intencao, tipo_usuario):
    """Processa com OpenAI incluindo contexto do tipo de usuário"""
    if client is None or not verificar_openai():
        return None
    
    try:
        # PROMPT PERSONALIZADO BASEADO NO TIPO DE USUÁRIO
        contexto_usuario = ""
        
        if tipo_usuario['tipo'] == 'admin':
            contexto_usuario = """
═══════════════════════════════════════════════════════════════════
🔴 ATENÇÃO: VOCÊ ESTÁ FALANDO COM O NATAN (DONO/ADMIN)
═══════════════════════════════════════════════════════════════════

- Este é o NATAN, o criador e desenvolvedor da NatanDEV
- Trate com MÁXIMO RESPEITO e profissionalismo
- Ele TEM ACESSO TOTAL a todas as informações
- Ele pode saber TUDO sobre clientes, planos, projetos internos
- Seja mais técnico e detalhado nas respostas
- Pode dar informações privilegiadas sobre o sistema
- Chame ele de "Natan" ou "chefe"

Exemplos de como responder:
- "Olá Natan! Como posso te ajudar hoje?"
- "Claro, chefe! Aqui estão os dados que você pediu..."
- "Natan, todos os sistemas estão funcionando perfeitamente!"
"""
        
        elif tipo_usuario['tipo'] == 'professional':
            contexto_usuario = """
═══════════════════════════════════════════════════════════════════
💎 CLIENTE PROFESSIONAL (R$ 79,99/mês)
═══════════════════════════════════════════════════════════════════

- Este cliente tem o plano PROFESSIONAL
- Ele pagou R$ 79,99/mês + R$ 530 desenvolvimento inicial
- Benefícios do plano dele:
  ✅ Design personalizado avançado
  ✅ SEO otimizado avançado
  ✅ Integração de APIs
  ✅ Domínio personalizado
  ✅ Suporte prioritário
  ✅ IA inclusa (opcional)
  
- Dê PRIORIDADE nas respostas
- Seja mais detalhado e técnico
- Mencione os benefícios do plano dele quando relevante
- Ofereça suporte extra

Exemplos de como responder:
- "Como cliente Professional, você tem acesso a..."
- "Pelo seu plano Professional, posso te ajudar com..."
- "Vou priorizar seu atendimento! Como cliente Professional..."
"""
        
        else:  # starter
            contexto_usuario = """
═══════════════════════════════════════════════════════════════════
🌱 CLIENTE STARTER (R$ 39,99/mês)
═══════════════════════════════════════════════════════════════════

- Este cliente tem o plano STARTER
- Ele pagou R$ 39,99/mês + R$ 350 desenvolvimento inicial
- Benefícios do plano dele:
  ✅ Site responsivo básico
  ✅ Design moderno e limpo
  ✅ Otimização para mobile
  ✅ Hospedagem inclusa
  ✅ Suporte por WhatsApp/Email
  
- Seja prestativo e educado
- Pode sugerir UPGRADE para Professional se fizer sentido
- Mencione os benefícios do plano dele

Exemplos de como responder:
- "Seu plano Starter inclui..."
- "Como cliente Starter, você tem acesso a..."
- "Se precisar de recursos mais avançados, temos o plano Professional!"
"""

        # Monta prompt com contexto do usuário
        prompt_sistema = f"""Você é o NatanAI, assistente virtual inteligente, masculino, amigável e empático da NatanDEV!

{contexto_usuario}

═══════════════════════════════════════════════════════════════════
INFORMAÇÕES OFICIAIS DO NATANDEV
═══════════════════════════════════════════════════════════════════

👨‍💻 **SOBRE O CRIADOR (NATAN):**
- Nome: Natan Borges Alves Nascimento
- Profissão: Web Developer Full-Stack
- Localização: Rio de Janeiro, Brasil
- WhatsApp: (21) 99282-6074

🤖 **SOBRE VOCÊ (NATANAI):**
- Você é a assistente virtual criada POR Natan
- Você NÃO desenvolve sites - você apenas auxilia
- Sempre diga "o Natan desenvolve", "ele pode fazer"

💰 **PLANOS:**
- Starter: R$ 39,99/mês + R$ 350,00 inicial
- Professional: R$ 79,99/mês + R$ 530,00 inicial

📞 **CONTATOS:**
- WhatsApp: (21) 99282-6074
- Instagram: @nborges.ofc
- Email: borgesnatan09@gmail.com
- Site: natansites.com.br
- Portfólio: natandev02.netlify.app

═══════════════════════════════════════════════════════════════════
REGRAS
═══════════════════════════════════════════════════════════════════

✅ Seja empático e natural
✅ Use o contexto do tipo de usuário acima
✅ NUNCA diga "eu desenvolvo" - sempre "o Natan desenvolve"
✅ NUNCA invente preços ou informações
✅ Sempre use informações oficiais acima

**CONTEXTO DA CONVERSA:** {intencao}

Responda adequadamente considerando o TIPO DE USUÁRIO!
"""

        prompt_usuario = f"Responda: {pergunta}"

        # Chamada OpenAI
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": prompt_usuario}
            ],
            max_tokens=350,
            temperature=0.7,
            top_p=0.9
        )
        
        resposta_openai = response.choices[0].message.content.strip()
        
        # Validação anti-alucinação
        valida, problemas = validar_resposta_anti_alucinacao(resposta_openai)
        
        if not valida:
            print(f"⚠️ Alucinação detectada! Problemas: {problemas}")
            resposta_openai = limpar_alucinacoes(resposta_openai)
            
            valida2, problemas2 = validar_resposta_anti_alucinacao(resposta_openai)
            if len(problemas2) > 2:
                print(f"❌ Resposta OpenAI descartada")
                return None
        
        # Adiciona "Vibrações Positivas!" ocasionalmente
        if random.random() < 0.3 and "vibrações positivas" not in resposta_openai.lower():
            resposta_openai += "\n\nVibrações Positivas!"
        
        print(f"✅ Resposta OpenAI validada para {tipo_usuario['tipo']}")
        return resposta_openai
        
    except Exception as e:
        print(f"❌ Erro OpenAI: {e}")
        return None

# =============================================================================
# GERADOR PRINCIPAL
# =============================================================================

def gerar_resposta_com_contexto_usuario(pergunta, tipo_usuario):
    """Sistema principal com contexto do usuário"""
    try:
        # Cache
        pergunta_hash = hashlib.md5(f"{pergunta}_{tipo_usuario['tipo']}".encode()).hexdigest()
        if pergunta_hash in CACHE_RESPOSTAS:
            return CACHE_RESPOSTAS[pergunta_hash], "cache"
        
        # Analisa intenção
        intencao = analisar_intencao(pergunta)
        
        # Processa com OpenAI incluindo contexto do usuário
        resposta_openai = processar_openai_com_contexto(pergunta, intencao, tipo_usuario)
        if resposta_openai:
            CACHE_RESPOSTAS[pergunta_hash] = resposta_openai
            return resposta_openai, f"openai_{tipo_usuario['tipo']}_{intencao}"
        
        # Fallback
        fallback = f"Desculpa, estou com dificuldades técnicas agora. 😅\n\nChama no WhatsApp: (21) 99282-6074\n\nVibrações Positivas!"
        return fallback, "fallback"
        
    except Exception as e:
        print(f"❌ Erro geral: {e}")
        return "Para informações, fale com Natan: (21) 99282-6074\n\nVibrações Positivas!", "erro"

# =============================================================================
# ROTAS DA API
# =============================================================================

@app.route('/health', methods=['GET'])
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({
        "status": "online",
        "sistema": "NatanAI v5.0 - Integrada com Supabase",
        "modo": "OpenAI + Supabase + Contextual por usuário",
        "modelo": OPENAI_MODEL,
        "openai_ativo": verificar_openai(),
        "supabase_ativo": supabase is not None,
        "cache_size": len(CACHE_RESPOSTAS)
    })

@app.route('/chat', methods=['POST'])
@app.route('/api/chat', methods=['POST'])
def chat():
    global HISTORICO_CONVERSAS
    
    try:
        data = request.get_json()
        
        # Valida dados básicos
        if not data:
            return jsonify({"error": "Dados não fornecidos"}), 400
        
        # Pega mensagem
        mensagem = data.get('message') or data.get('pergunta', '')
        if not mensagem or not mensagem.strip():
            return jsonify({"error": "Mensagem vazia"}), 400
        
        mensagem = mensagem.strip()
        
        # 🔐 VERIFICA TOKEN DE AUTENTICAÇÃO
        auth_header = request.headers.get('Authorization', '')
        user_data_from_request = data.get('user_data', {})
        
        tipo_usuario = None
        user_info = None
        
        # Tenta autenticar via token
        if auth_header:
            user_info = verificar_token_supabase(auth_header)
            
            if user_info:
                # Busca dados completos do usuário
                dados_completos = obter_dados_usuario_completos(user_info.id)
                
                # Monta dados do usuário combinando auth + database
                user_full_data = {
                    'email': user_info.email,
                    'user_id': user_info.id,
                    'plan': user_info.user_metadata.get('plan', 'starter') if user_info.user_metadata else 'starter',
                    'name': user_info.user_metadata.get('name', '') if user_info.user_metadata else ''
                }
                
                if dados_completos:
                    user_full_data.update(dados_completos)
                
                # Determina tipo de usuário
                tipo_usuario = determinar_tipo_usuario(user_full_data)
                
                print(f"✅ Usuário autenticado: {user_info.email} | Tipo: {tipo_usuario['tipo']}")
            else:
                print("⚠️ Token inválido ou expirado")
        
        # Se não conseguiu autenticar, usa dados enviados na requisição ou padrão
        if not tipo_usuario:
            if user_data_from_request:
                tipo_usuario = determinar_tipo_usuario(user_data_from_request)
                print(f"⚠️ Usando dados da requisição | Tipo: {tipo_usuario['tipo']}")
            else:
                # Usuário padrão (starter)
                tipo_usuario = {
                    'tipo': 'starter',
                    'nome': 'Cliente',
                    'descricao': 'Cliente padrão',
                    'permissoes': 'basicas',
                    'plano': 'Starter'
                }
                print("⚠️ Usuário não autenticado - usando padrão Starter")
        
        print(f"\n💬 [{datetime.now().strftime('%H:%M:%S')}] Pergunta de {tipo_usuario['nome']}: {mensagem}")
        
        # Gera resposta com contexto do usuário
        resposta, fonte = gerar_resposta_com_contexto_usuario(mensagem, tipo_usuario)
        
        # Validação final
        valida, problemas = validar_resposta_anti_alucinacao(resposta)
        
        # Histórico
        with historico_lock:
            HISTORICO_CONVERSAS.append({
                "timestamp": datetime.now().isoformat(),
                "pergunta": mensagem,
                "tipo_usuario": tipo_usuario['tipo'],
                "plano": tipo_usuario['plano'],
                "fonte": fonte,
                "validacao_ok": valida
            })
            
            if len(HISTORICO_CONVERSAS) > 1000:
                HISTORICO_CONVERSAS = HISTORICO_CONVERSAS[-500:]
        
        return jsonify({
            "response": resposta,
            "resposta": resposta,
            "metadata": {
                "fonte": fonte,
                "sistema": "NatanAI v5.0 Supabase",
                "modelo": OPENAI_MODEL,
                "tipo_usuario": tipo_usuario['tipo'],
                "plano_usuario": tipo_usuario['plano'],
                "validacao_anti_alucinacao": valida,
                "autenticado": user_info is not None
            }
        })
        
    except Exception as e:
        print(f"❌ Erro no chat: {e}")
        import traceback
        traceback.print_exc()
        
        return jsonify({
            "response": "Para informações, fale com Natan: (21) 99282-6074\n\nVibrações Positivas!",
            "resposta": "Para informações, fale com Natan: (21) 99282-6074\n\nVibrações Positivas!",
            "metadata": {
                "fonte": "erro_emergency",
                "error": str(e)
            }
        }), 500

@app.route('/api/info', methods=['GET'])
def info():
    """Retorna informações sobre a NatanAI"""
    return jsonify({
        "nome": "NatanAI",
        "versao": "5.0 - Integrada com Supabase",
        "criador": INFORMACOES_OFICIAIS["criador"],
        "profissao": INFORMACOES_OFICIAIS["profissao"],
        "modelo": {
            "nome": OPENAI_MODEL,
            "tipo": "OpenAI GPT-4o-mini",
            "status": "🟢 Online" if verificar_openai() else "🔴 Offline",
            "modo": "Contextual por tipo de usuário"
        },
        "supabase": {
            "status": "🟢 Conectado" if supabase else "🔴 Desconectado",
            "funcionalidades": [
                "Autenticação de usuários",
                "Identificação de plano (Starter/Professional/Admin)",
                "Respostas personalizadas por tipo de usuário",
                "Controle de permissões"
            ]
        },
        "contato": {
            "whatsapp": INFORMACOES_OFICIAIS["whatsapp"],
            "instagram": INFORMACOES_OFICIAIS["instagram"],
            "email": INFORMACOES_OFICIAIS["email"],
            "site": INFORMACOES_OFICIAIS["site"],
            "portfolio": INFORMACOES_OFICIAIS["portfolio"]
        },
        "tipos_usuario": {
            "admin": "Natan - Acesso total e informações privilegiadas",
            "professional": "Cliente Professional - Suporte prioritário e recursos avançados",
            "starter": "Cliente Starter - Suporte padrão"
        }
    })

@app.route('/estatisticas', methods=['GET'])
@app.route('/api/estatisticas', methods=['GET'])
def estatisticas():
    try:
        if not HISTORICO_CONVERSAS:
            return jsonify({"message": "Nenhuma conversa registrada"})
        
        fontes_count = {}
        tipos_usuario_count = {}
        planos_count = {}
        validacoes_ok = 0
        
        with historico_lock:
            for conv in HISTORICO_CONVERSAS:
                fonte = conv.get("fonte", "unknown")
                fontes_count[fonte] = fontes_count.get(fonte, 0) + 1
                
                tipo = conv.get("tipo_usuario", "unknown")
                tipos_usuario_count[tipo] = tipos_usuario_count.get(tipo, 0) + 1
                
                plano = conv.get("plano", "unknown")
                planos_count[plano] = planos_count.get(plano, 0) + 1
                
                if conv.get("validacao_ok", True):
                    validacoes_ok += 1
        
        return jsonify({
            "total_conversas": len(HISTORICO_CONVERSAS),
            "distribuicao_fontes": fontes_count,
            "distribuicao_tipos_usuario": tipos_usuario_count,
            "distribuicao_planos": planos_count,
            "validacao_anti_alucinacao": {
                "respostas_validadas": validacoes_ok,
                "taxa_sucesso": round((validacoes_ok / len(HISTORICO_CONVERSAS)) * 100, 2)
            },
            "sistema": "NatanAI v5.0 - Supabase Integrado",
            "funcionalidades": [
                "Autenticação via Supabase",
                "Respostas contextuais por tipo de usuário",
                "Admin tem acesso privilegiado",
                "Professional tem suporte prioritário",
                "Starter tem suporte padrão"
            ]
        })
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/exemplos', methods=['GET'])
def exemplos():
    """Retorna exemplos de perguntas"""
    return jsonify({
        "exemplos_perguntas": [
            "Quanto custa um site?",
            "Quem é o Natan?",
            "Quero criar um site para minha empresa",
            "Quanto tempo demora para fazer um site?",
            "O site fica responsivo?",
            "Vocês usam IA?",
            "Quais projetos já fizeram?",
            "Como entro em contato?",
            "Qual o WhatsApp?",
            "Quero ver o portfólio",
            "Me fale sobre os planos",
            "Qual a diferença entre Starter e Professional?",
            "Como funciona o processo?",
            "Faz site com SEO?",
            "Atende em qual cidade?",
            "Oi, tudo bem?",
            "Como foi seu dia?",
            "Conta uma piada"
        ],
        "dica": "A NatanAI agora reconhece seu tipo de usuário e personaliza as respostas! 🚀",
        "modelo": f"Usando OpenAI {OPENAI_MODEL} + Supabase com sistema anti-alucinação",
        "personalizacao": {
            "admin": "Respostas técnicas, detalhadas e com informações privilegiadas",
            "professional": "Respostas prioritárias com recursos avançados",
            "starter": "Respostas completas com sugestão de upgrade quando relevante"
        }
    })

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({
        "status": "pong",
        "timestamp": datetime.now().isoformat(),
        "sistema": "NatanAI v5.0 - Supabase Integrado"
    })

@app.route('/', methods=['GET'])
def home():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>NatanAI v5.0 - Supabase Integrado</title>
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
            }
            .badge-supabase { background: linear-gradient(135deg, #3ECF8E, #2EBD7E); color: white; }
            .badge-ai { background: #4CAF50; color: white; }
            .badge-contextual { background: #FF6B6B; color: white; }
            
            .info-box {
                background: linear-gradient(135deg, #e3f2fd, #f3e5f5);
                padding: 20px;
                border-radius: 15px;
                margin: 20px 0;
                border-left: 5px solid #667eea;
            }
            .info-box h3 { color: #667eea; margin-bottom: 10px; }
            .info-box ul { list-style: none; padding-left: 0; }
            .info-box li { 
                padding: 8px 0; 
                padding-left: 25px;
                position: relative;
            }
            .info-box li:before {
                content: "✓";
                position: absolute;
                left: 0;
                color: #4CAF50;
                font-weight: bold;
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
                border-bottom-right-radius: 5px;
            }
            .bot { 
                background: #e8f5e9;
                margin-right: 20%;
                border-bottom-left-radius: 5px;
                border-left: 4px solid #4CAF50;
            }
            .metadata {
                font-size: 0.75em;
                color: #666;
                margin-top: 8px;
                padding-top: 8px;
                border-top: 1px solid rgba(0,0,0,0.1);
            }
            .metadata-badge {
                display: inline-block;
                padding: 3px 8px;
                margin: 2px;
                background: rgba(0,0,0,0.1);
                border-radius: 10px;
                font-size: 0.9em;
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
                transition: all 0.3s;
            }
            input:focus {
                outline: none;
                border-color: #667eea;
                box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
            }
            button { 
                padding: 15px 30px;
                background: linear-gradient(135deg, #667eea, #764ba2);
                color: white; 
                border: none;
                border-radius: 25px;
                cursor: pointer;
                font-weight: bold;
                transition: all 0.3s;
            }
            button:hover { 
                transform: translateY(-2px);
                box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
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
                transition: all 0.3s;
            }
            .example-btn:hover {
                background: #667eea;
                color: white;
            }
            
            .footer {
                text-align: center;
                margin-top: 30px;
                padding-top: 20px;
                border-top: 2px solid #e0e0e0;
                color: #666;
                font-size: 0.9em;
            }
            
            @media (max-width: 600px) {
                .container { padding: 15px; }
                .header h1 { font-size: 1.5em; }
                .message { margin: 10px 0; padding: 10px; }
                .user, .bot { margin-left: 0; margin-right: 0; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🤖 NatanAI v5.0 - SUPABASE INTEGRADO</h1>
                <p style="color: #666; margin: 10px 0;">Assistente Inteligente Contextual da NatanDEV</p>
                <div>
                    <span class="badge badge-supabase">SUPABASE</span>
                    <span class="badge badge-ai">OpenAI GPT-4o-mini</span>
                    <span class="badge badge-contextual">CONTEXTUAL</span>
                </div>
            </div>
            
            <div class="info-box">
                <h3>🎯 Sistema Contextual Inteligente</h3>
                <ul>
                    <li><strong>Autenticação Supabase:</strong> Identifica automaticamente seu usuário e plano</li>
                    <li><strong>Admin (Natan):</strong> Respostas técnicas e informações privilegiadas</li>
                    <li><strong>Professional:</strong> Suporte prioritário e recursos avançados</li>
                    <li><strong>Starter:</strong> Respostas completas com sugestão de upgrade</li>
                    <li><strong>Anti-Alucinação:</strong> Validação rigorosa de todas as respostas</li>
                </ul>
            </div>
            
            <div class="info-box">
                <h3>📍 Sobre a NatanDEV</h3>
                <ul>
                    <li><strong>Criador:</strong> Natan Borges - Web Developer Full-Stack</li>
                    <li><strong>WhatsApp:</strong> (21) 99282-6074</li>
                    <li><strong>Plano Starter:</strong> R$ 39,99/mês + R$ 350 inicial</li>
                    <li><strong>Plano Professional:</strong> R$ 79,99/mês + R$ 530 inicial</li>
                    <li><strong>Portfólio:</strong> natandev02.netlify.app</li>
                </ul>
            </div>
            
            <div id="chat-box" class="chat-box">
                <div class="message bot">
                    <strong>🤖 NatanAI v5.0:</strong><br><br>
                    Olá! Agora estou integrada com Supabase! 🚀<br><br>
                    
                    <strong>Reconheço automaticamente:</strong><br>
                    👑 Admin (Natan) - Respostas técnicas e privilegiadas<br>
                    💎 Professional - Suporte prioritário<br>
                    🌱 Starter - Suporte padrão<br><br>
                    
                    Pergunta o que quiser! Vou responder baseado no seu perfil! 😊<br><br>
                    
                    <strong>Vibrações Positivas!</strong>
                </div>
            </div>
            
            <div class="examples">
                <button class="example-btn" onclick="testar('Oi, tudo bem?')">👋 Saudação</button>
                <button class="example-btn" onclick="testar('Quanto custa um site?')">💰 Preços</button>
                <button class="example-btn" onclick="testar('Quero criar um site')">🚀 Criar Site</button>
                <button class="example-btn" onclick="testar('Qual meu plano?')">💎 Meu Plano</button>
                <button class="example-btn" onclick="testar('Qual o portfólio?')">💼 Portfólio</button>
            </div>
            
            <div class="input-area">
                <input type="text" id="msg" placeholder="Digite sua pergunta..." onkeypress="if(event.key==='Enter') enviar()">
                <button onclick="enviar()">Enviar</button>
            </div>
            
            <div class="footer">
                <p><strong>NatanAI v5.0 - Supabase Integrado</strong></p>
                <p>OpenAI GPT-4o-mini + Supabase + Respostas Contextuais por Usuário</p>
                <p style="margin-top: 10px;">📞 WhatsApp: (21) 99282-6074 | 🌐 natansites.com.br</p>
            </div>
        </div>

        <script>
        function testar(msg) {
            document.getElementById('msg').value = msg;
            enviar();
        }
        
        async function enviar() {
            const input = document.getElementById('msg');
            const chatBox = document.getElementById('chat-box');
            const msg = input.value.trim();
            
            if (!msg) return;
            
            chatBox.innerHTML += `
                <div class="message user">
                    <strong>Você:</strong><br>${msg}
                </div>
            `;
            input.value = '';
            chatBox.scrollTop = chatBox.scrollHeight;
            
            try {
                const response = await fetch('/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: msg })
                });
                
                const data = await response.json();
                const metadata = data.metadata || {};
                
                let metadataBadges = '';
                if (metadata.tipo_usuario) {
                    const icons = {admin: '👑', professional: '💎', starter: '🌱'};
                    metadataBadges += `<span class="metadata-badge">${icons[metadata.tipo_usuario] || '👤'} ${metadata.tipo_usuario}</span>`;
                }
                if (metadata.plano_usuario) {
                    metadataBadges += `<span class="metadata-badge">📋 ${metadata.plano_usuario}</span>`;
                }
                if (metadata.fonte) {
                    metadataBadges += `<span class="metadata-badge">📊 ${metadata.fonte}</span>`;
                }
                if (metadata.validacao_anti_alucinacao !== undefined) {
                    const validIcon = metadata.validacao_anti_alucinacao ? '✅' : '⚠️';
                    metadataBadges += `<span class="metadata-badge">${validIcon} Validação</span>`;
                }
                
                const respText = (data.response || data.resposta).replace(/\n/g, '<br>');
                chatBox.innerHTML += `
                    <div class="message bot">
                        <strong>🤖 NatanAI:</strong><br>${respText}
                        ${metadataBadges ? `<div class="metadata">${metadataBadges}</div>` : ''}
                    </div>
                `;
                
            } catch (error) {
                chatBox.innerHTML += `
                    <div class="message bot">
                        <strong>🤖 NatanAI:</strong><br>
                        Erro de conexão. Fale com Natan: (21) 99282-6074<br><br>
                        Vibrações Positivas!
                    </div>
                `;
            }
            
            chatBox.scrollTop = chatBox.scrollHeight;
        }
        </script>
    </body>
    </html>
    """
    return render_template_string(html)

# =============================================================================
# INICIALIZAÇÃO
# =============================================================================

if __name__ == '__main__':
    print("\n" + "="*80)
    print("🤖 NATANAI v5.0 - SUPABASE INTEGRADO")
    print("="*80)
    print("👨‍💻 Criador: Natan Borges Alves Nascimento")
    print("🚀 Web Developer Full-Stack")
    print("📞 WhatsApp: (21) 99282-6074")
    print("🌐 Site: natansites.com.br")
    print("💼 Portfólio: natandev02.netlify.app")
    print("="*80)
    
    # Verifica conexões
    openai_status = verificar_openai()
    supabase_status = supabase is not None
    
    print(f"\n🔧 CONFIGURAÇÃO:")
    print(f"   • Modelo OpenAI: {OPENAI_MODEL}")
    print(f"   • OpenAI: {'✅ CONECTADO' if openai_status else '⚠️ OFFLINE'}")
    print(f"   • Supabase: {'✅ CONECTADO' if supabase_status else '⚠️ OFFLINE'}")
    print(f"   • Sistema Anti-Alucinação: ✅ ATIVO")
    
    print(f"\n🎯 SISTEMA CONTEXTUAL:")
    print(f"   👑 ADMIN (Natan): Informações privilegiadas e respostas técnicas")
    print(f"   💎 PROFESSIONAL: Suporte prioritário e recursos avançados")
    print(f"   🌱 STARTER: Suporte padrão com sugestões de upgrade")
    
    print(f"\n🛡️ PROTEÇÕES:")
    print(f"   ✅ Autenticação via Supabase")
    print(f"   ✅ Identificação automática de plano")
    print(f"   ✅ Respostas personalizadas por tipo")
    print(f"   ✅ Validação anti-alucinação")
    print(f"   ✅ Cache inteligente por usuário")
    
    print(f"\n🚀 SERVIDOR INICIANDO...")
    print(f"   • Porta: 5000")
    print(f"   • Host: 0.0.0.0")
    
    print("\n" + "="*80)
    print("📞 CONTATO: WhatsApp (21) 99282-6074")
    print("🌐 SITE: natansites.com.br")
    print("="*80 + "\n")
    
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,
        threaded=True
    )
