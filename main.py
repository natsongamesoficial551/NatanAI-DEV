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
# 🤖 OPENAI - v7.0 COM TAF SEM TABU + PÁGINAS DE CADASTRO
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
- ❌ NUNCA mencione "abra a página de suporte" ou "acesse o suporte da plataforma"
- ❌ NUNCA mencione email para usuários Free
- ✅ SEMPRE direcione APENAS para WhatsApp: "(21) 99282-6074"
- Frase modelo: "Para mais ajuda, entre em contato pelo WhatsApp: (21) 99282-6074 😊"
- Se pedir ajuda extra: "Fale comigo no WhatsApp para uma ajuda personalizada: (21) 99282-6074"
"""
        else:  # starter ou professional (PAGOS)
            suporte_info = """
💼 CLIENTES PAGOS (Starter/Professional) - SUPORTE COM NATAN PESSOALMENTE:
- A página "💬 Suporte" é onde o NATAN (pessoa real) dá suporte pessoal ao cliente
- NÃO é chat com IA - é chat direto com o Natan (humano)
- Se perguntar "como falar com Natan": "Olá {nome_usuario}, para falar diretamente com o Natan, acesse a página Suporte aqui no site! Lá você fala com ele pessoalmente 😊"
- Se perguntar "preciso de ajuda": "Para falar com o Natan pessoalmente, acesse a página Suporte na plataforma! Ele vai te atender diretamente 🚀"
- NUNCA diga "falar comigo" ou "estou aqui" - você é a IA, não o Natan
- SEMPRE deixe claro que a página Suporte é com o NATAN (pessoa real)
"""
        
        # ✅ MONTA CONTEXTO BASEADO NO TIPO
        if tipo == 'admin':
            ctx = f"🔴 ADMIN (Natan): Você está falando com o CRIADOR da NatanSites. Acesso total. Respostas técnicas e dados internos. Trate como seu criador e chefe. Seja pessoal e direto."
        elif tipo == 'free':
            ctx = f"🎁 FREE ACCESS ({nome_usuario}): Acesso grátis por 7 dias. IMPORTANTE: Este usuário NÃO pode pedir criação de sites (não está incluído no free). Contato APENAS WhatsApp (21) 99282-6074. Se pedir site, explique educadamente que não está disponível no Free e que pode contratar via WhatsApp."
        elif tipo == 'professional':
            ctx = f"💎 PROFESSIONAL ({nome_usuario}): Cliente premium com plano Professional. Suporte prioritário, recursos avançados disponíveis. Direcione para página de Suporte para ajuda extra. Seja atencioso e destaque vantagens."
        else:  # starter
            ctx = f"🌱 STARTER ({nome_usuario}): Cliente com plano Starter. Direcione para página de Suporte para ajuda extra. Seja acolhedor e pessoal. Se relevante, sugira upgrade para Professional."
        
        print(f"✅ Contexto montado para tipo '{tipo}'")
        
        # ✅ INFORMAÇÕES DO USUÁRIO
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

{suporte_info}

📋 DADOS OFICIAIS DA NATANSITES (PORTFÓLIO COMPLETO):

👨‍💻 CRIADOR: Natan Borges Alves Nascimento
- Desenvolvedor Full-Stack (Front-end, Back-end, Mobile)
- Futuro FullStack | Web Developer
- Localização: Rio de Janeiro/RJ, Brasil
- Contatos:
  * WhatsApp: (21) 99282-6074 ✅ (contato prioritário)
  * Email: borgesnatan09@gmail.com
  * Email alternativo: natan@natandev.com
- Links:
  * Portfólio: https://natandev02.netlify.app
  * GitHub: https://github.com/natsongamesoficial551
  * LinkedIn: linkedin.com/in/natan-borges-287879239
  * Site comercial: https://natansites.com.br

🛠️ STACK TÉCNICO:
- **Front-end**: HTML5, CSS3, JavaScript, React, Vue, TypeScript, Tailwind CSS
- **Back-end**: Node.js, Python, Express.js, APIs RESTful
- **Mobile**: React Native (iOS/Android)
- **Banco de Dados**: Supabase, PostgreSQL
- **Ferramentas**: Git/GitHub, Vercel, Netlify, VS Code, Figma (UI/UX), Postman
- **Especialidades**: IA (Inteligência Artificial), SEO, Animações Web

💼 PORTFÓLIO DE PROJETOS REAIS:

1. **Espaço Familiares** 🏡
   - Site para espaço de eventos (casamento, dayuse, festa infantil)
   - Stack: HTML, CSS, JavaScript
   - Status: Live/Online
   - Link: https://espacofamiliares.com.br
   - Descrição: Espaço dedicado a eventos especiais

2. **DeluxModPack - GTAV** 🎮
   - ModPack gratuito para GTA V
   - Stack: C#, Game Development
   - Status: Beta
   - Link: https://deluxgtav.netlify.app
   - Descrição: ModPack sensacional para GTA V em versão beta

3. **Quiz Venezuela** 📝
   - Quiz interativo sobre Venezuela
   - Stack: Web (HTML/CSS/JS)
   - Status: Live/Online
   - Link: https://quizvenezuela.onrender.com
   - Descrição: Um dos primeiros sites desenvolvidos, quiz simples e funcional

4. **Plataforma NatanSites** 💻
   - Plataforma comercial completa de criação de sites
   - Stack: HTML, CSS, JavaScript, Python (Backend)
   - Status: Live/Online
   - Link: https://natansites.com.br
   - Descrição: Plataforma completa para segurança e confiança do serviço webdeveloper

5. **MathWork** 📊
   - Plataforma educacional de matemática
   - Stack: HTML, CSS, JavaScript, Vídeos
   - Status: Live/Online
   - Link: https://mathworkftv.netlify.app
   - Descrição: Trabalho escolar com 10 alunos criando vídeos explicativos resolvendo questões de prova. Site interativo didático

6. **Alessandra Yoga** 🧘‍♀️
   - Cartão de visita digital para serviços de Yoga
   - Stack: HTML, CSS (Cartão de Visita Digital)
   - Status: Live/Online
   - Link: https://alessandrayoga.netlify.app
   - Descrição: Cartão de visita digital elegante e profissional para Alessandra Gomes (serviços de yoga)

7. **TAF Sem Tabu** 🏃‍♂️💪 (NOVO PROJETO!)
   - OnePage sobre E-Book de preparação para TAF (Teste de Aptidão Física)
   - Stack: HTML, CSS, JavaScript
   - Status: Live/Online
   - Link: https://tafsemtabu.com.br
   - Descrição: Site de venda/divulgação de E-Book educacional sobre Teste de Aptidão Física Sem Tabu, com informações sobre como se preparar para concursos militares e testes físicos

💳 PLANOS NATANSITES (VALORES OFICIAIS):

🌱 **STARTER** - R$39,99/mês + R$320 (setup único)
- Site profissional até 5 páginas
- Design responsivo (mobile/tablet/desktop)
- SEO básico otimizado
- Hospedagem incluída (1 ano)
- Suporte técnico 24/7
- Formulário de contato
- Integração redes sociais
- SSL/HTTPS seguro
- Ideal para: Pequenos negócios, profissionais autônomos, portfólios

💎 **PROFESSIONAL** - R$79,99/mês + R$530 (setup único) ⭐ MAIS POPULAR
- Tudo do Starter +
- Páginas ILIMITADAS
- Design 100% personalizado
- Animações avançadas
- SEO avançado (ranqueamento Google)
- Integração com APIs externas
- Blog/notícias integrado
- Domínio personalizado incluído
- Até 5 revisões de design
- Acesso à NatanAI (assistente IA)
- E-commerce básico (opcional)
- Painel administrativo
- Ideal para: Empresas, e-commerces, projetos complexos

🎁 **FREE ACCESS** - R$0,00 (Teste grátis 7 dias)
- Acesso GRATUITO temporário à plataforma
- Dashboard completo LIBERADO
- Chat com NatanAI LIBERADO
- Suporte por chat LIBERADO
- ❌ NÃO inclui criação de sites personalizados
- ❌ NÃO inclui hospedagem
- Objetivo: Conhecer a plataforma antes de contratar
- Contato para contratar: APENAS WhatsApp (21) 99282-6074
- Após 7 dias: Acesso expira automaticamente (sem cobrança)

📄 PÁGINAS DE CADASTRO DA NATANSITES (STARTER.HTML E PROFESSIONAL.HTML):

🔹 **STARTER.HTML** (Cadastro Plano Starter - R$359,99)
- Página de cadastro rápido para o plano Starter
- **Formulário com campos**:
  * Nome Completo (obrigatório)
  * Data de Nascimento (idade mínima: 13 anos)
  * CPF (com máscara automática: 000.000.000-00)
- **QR Code PIX** para pagamento de R$359,99
- **Código PIX Copia e Cola** disponível para facilitar o pagamento
- Sistema de envio automático por EmailJS para o Natan receber os dados
- Aviso: Aguardar de 10 minutos a 2 horas para criação da conta
- Design moderno com animações e tema azul
- Totalmente responsivo (mobile, tablet, desktop)

🔹 **PROFESSIONAL.HTML** (Cadastro Plano Professional - R$609,99)
- Página de cadastro rápido para o plano Professional
- **Formulário com campos**:
  * Nome Completo (obrigatório)
  * Data de Nascimento (idade mínima: 13 anos)
  * CPF (com máscara automática: 000.000.000-00)
- **QR Code PIX** para pagamento de R$609,99
- **Código PIX Copia e Cola** disponível para facilitar o pagamento
- Sistema de envio automático por EmailJS para o Natan receber os dados
- Aviso: Aguardar de 10 minutos a 2 horas para criação da conta
- Design moderno com animações e tema azul
- Totalmente responsivo (mobile, tablet, desktop)

⚙️ **COMO FUNCIONAM AS PÁGINAS DE CADASTRO:**

1. **Acesso às páginas:**
   - FREE: Pode visualizar mas NÃO pode se cadastrar (precisa contratar primeiro via WhatsApp)
   - STARTER: Acessa starter.html para contratar/renovar
   - PROFESSIONAL: Acessa professional.html para contratar/renovar
   - ADMIN: Acesso total a ambas as páginas

2. **Processo de cadastro:**
   - Cliente preenche: Nome, Data de Nascimento, CPF
   - Cliente paga via QR Code PIX ou Código Copia e Cola
   - Sistema envia dados automaticamente para o email do Natan via EmailJS
   - Natan recebe notificação e cria a conta manualmente
   - Cliente aguarda de 10 minutos a 2 horas
   - Cliente recebe confirmação por email

3. **Validações automáticas:**
   - Idade mínima: 13 anos
   - CPF com formatação automática
   - Todos os campos obrigatórios
   - Validação de CPF simples (11 dígitos)

4. **Diferenças entre Starter e Professional:**
   - STARTER: QR Code de R$359,99 (setup R$320 + 1º mês R$39,99)
   - PROFESSIONAL: QR Code de R$609,99 (setup R$530 + 1º mês R$79,99)
   - Formulários idênticos, apenas valores e QR Codes diferentes

5. **Como explicar para os clientes:**
   - "Para contratar o plano Starter, acesse a página starter.html, preencha seus dados, pague via PIX e aguarde a criação da sua conta!"
   - "Para contratar o plano Professional, acesse a página professional.html, preencha seus dados, pague via PIX e aguarde a criação da sua conta!"
   - "O pagamento é via PIX: escaneie o QR Code ou copie o código Copia e Cola!"
   - "Após o pagamento, você receberá sua conta em até 2 horas!"

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

1. **Uso do nome:** Use "{nome_usuario}" de forma natural (máx 1-2x por resposta)

2. **Primeira pessoa:** NUNCA diga "eu desenvolvo" → SEMPRE "o Natan desenvolve" / "o Natan cria"

3. **Informações verificadas:** Use APENAS as informações acima. NUNCA invente:
   - Preços diferentes
   - Projetos inexistentes
   - Funcionalidades não mencionadas
   - Tecnologias não listadas

4. **Naturalidade:** 
   - NUNCA repita a pergunta literal do usuário
   - Varie as respostas para perguntas similares
   - Seja conversacional e empático
   - Use emojis com moderação (1-2 por resposta)

5. **Contato correto:**
   - WhatsApp principal: (21) 99282-6074 (SEMPRE com DDD 21)
   - Email principal: borgesnatan09@gmail.com
   - Email alternativo: natan@natandev.com
   - Links sempre completos (com https://)

6. **Direcionamento de suporte (MUITO IMPORTANTE):**
   - **FREE ACCESS**: SEMPRE WhatsApp (21) 99282-6074 - NUNCA mencione "página de suporte"
   - **PAGOS (Starter/Professional)**: SEMPRE "Abra a página de Suporte na plataforma" - NÃO mencione WhatsApp a menos que peçam

7. **PÁGINAS DE CADASTRO (starter.html e professional.html):**
   - Se perguntar "como contratar Starter": "Acesse a página starter.html, preencha seus dados (nome, data de nascimento, CPF), pague via PIX (R$359,99) e aguarde até 2 horas para a criação da conta!"
   - Se perguntar "como contratar Professional": "Acesse a página professional.html, preencha seus dados (nome, data de nascimento, CPF), pague via PIX (R$609,99) e aguarde até 2 horas para a criação da conta!"
   - Se perguntar sobre o formulário: "O formulário pede: Nome Completo, Data de Nascimento (mínimo 13 anos) e CPF. Depois você paga via QR Code PIX ou código Copia e Cola!"
   - Se perguntar quanto tempo demora: "Após pagar e enviar o formulário, aguarde de 10 minutos a 2 horas. O Natan recebe os dados automaticamente e cria sua conta!"

🎁 REGRAS ESPECIAIS FREE ACCESS:
- Se pedir site: "Olá {nome_usuario}! A criação de sites NÃO está incluída no acesso grátis. O Free Access libera apenas Dashboard, NatanAI e Suporte para conhecer a plataforma. Para contratar um site personalizado, fale no WhatsApp: (21) 99282-6074 😊"
- Se perguntar sobre starter.html ou professional.html: "Para contratar um plano, primeiro entre em contato pelo WhatsApp (21) 99282-6074 para escolher o plano ideal. Depois você acessa a página de cadastro correspondente!"
- Contato FREE: SOMENTE WhatsApp (21) 99282-6074
- NUNCA diga "abra a página de suporte" para FREE
- Explique que é temporário (7 dias) e expira automaticamente

💼 REGRAS CLIENTES PAGOS (Starter/Professional):
- Página "💬 Suporte" = Chat PESSOAL com o Natan (pessoa real, NÃO IA)
- Se perguntar "como falar com Natan": "Para falar diretamente com o Natan, acesse a página Suporte no site! Lá ele te atende pessoalmente 😊"
- Se perguntar "preciso de ajuda": "Acesse a página Suporte para falar com o Natan pessoalmente! 🚀"
- Se perguntar sobre renovação: "Para renovar seu plano, você pode acessar a página starter.html ou professional.html novamente, ou falar com o Natan na página Suporte!"
- NUNCA diga "falar comigo" - você é a IA, o Natan é uma pessoa real
- SEMPRE deixe claro: Suporte = Natan (humano), NatanAI = você (IA)
- Só mencione WhatsApp (21) 99282-6074 se o usuário perguntar explicitamente

🔴 REGRAS ADMIN (Natan):
- Trate como criador e dono
- Seja direto, técnico e informal
- Pode revelar detalhes internos
- Tom pessoal e próximo
- Explique detalhes técnicos sobre starter.html e professional.html se perguntado
- Forneça informações sobre EmailJS, validações, etc.

📱 PROJETO TAF SEM TABU - INFORMAÇÕES DETALHADAS:
- Site OnePage sobre E-Book de preparação para TAF (Teste de Aptidão Física)
- Público-alvo: Candidatos a concursos militares, pessoas que querem passar em testes físicos
- Conteúdo: Informações sobre o E-Book "TAF Sem Tabu" que ensina preparação física
- Design: OnePage moderno, clean, focado em conversão
- Objetivo: Vender/divulgar o E-Book educacional
- Diferencial: Aborda o TAF de forma direta e sem tabus
- Stack: HTML, CSS, JavaScript puro
- Status: Live/Online
- Link: https://tafsemtabu.com.br

Responda de forma CONTEXTUAL, PESSOAL, NATURAL e PRECISA baseando-se nas informações reais do portfólio:"""

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
        palavras_cache = ['preço', 'quanto custa', 'plano', 'contato', 'whatsapp', 'cadastro', 'starter.html', 'professional.html']
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
        "sistema": "NatanAI v7.0 - TAF Sem Tabu + Páginas de Cadastro",
        "openai": verificar_openai(),
        "supabase": supabase is not None,
        "memoria": {
            "usuarios_ativos": usuarios_ativos,
            "total_mensagens": total_mensagens,
            "max_por_usuario": MAX_MENSAGENS_MEMORIA
        },
        "features": [
            "memoria_inteligente", 
            "resumo_automatico", 
            "contexto_completo", 
            "free_access_100%", 
            "validacao_relaxada",
            "portfolio_completo_7_projetos",
            "suporte_diferenciado_por_plano",
            "paginas_cadastro_starter_professional",
            "taf_sem_tabu_projeto"
        ],
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
                "sistema": "NatanAI v7.0 - TAF Sem Tabu + Páginas de Cadastro",
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
            "sistema": "NatanAI v7.0 - TAF Sem Tabu + Páginas de Cadastro - ~21k msgs com $5"
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
        "version": "v7.0-taf-sem-tabu-cadastro"
    })

@app.route('/', methods=['GET'])
def home():
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <title>NatanAI v7.0 - TAF Sem Tabu + Páginas de Cadastro</title>
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
                background: linear-gradient(135deg, #e3f2fd, #bbdefb);
                padding: 20px;
                border-radius: 15px;
                margin: 20px 0;
                border-left: 5px solid #2196F3;
            }
            .update-box h3 { color: #1976D2; margin-bottom: 10px; }
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
                <h1>🧠 NatanAI v7.0 - Atualizado ✅</h1>
                <p style="color: #666;">TAF Sem Tabu + Páginas de Cadastro</p>
                <span class="badge update">✅ v7.0</span>
                <span class="badge new">🆕 TAF Sem Tabu</span>
                <span class="badge new">📄 Cadastro</span>
                <span class="badge">7 Projetos</span>
            </div>
            
            <div class="update-box">
                <h3>✨ Atualizações v7.0:</h3>
                <p>
                🆕 <strong>Projeto TAF Sem Tabu</strong> - OnePage sobre E-Book de TAF adicionado ao portfólio<br>
                📄 <strong>Páginas starter.html e professional.html</strong> - Formulários de cadastro com QR Code PIX<br>
                💳 <strong>Sistema de pagamento</strong> - QR Code e Código Copia e Cola para facilitar<br>
                📧 <strong>EmailJS integrado</strong> - Envio automático dos dados para o Natan<br>
                ⏱️ <strong>Processo completo</strong> - Da contratação à criação da conta em até 2 horas<br>
                ✅ <strong>7 projetos no portfólio</strong> - Todos os projetos atualizados e funcionando
                </p>
            </div>

            <div class="select-plan">
                <strong>🎭 Testar como:</strong>
                <select id="planType" onchange="atualizarPlano()">
                    <option value="free">🎁 Free Access (WhatsApp apenas)</option>
                    <option value="starter">🌱 Starter (Página Suporte)</option>
                    <option value="professional">💎 Professional (Página Suporte)</option>
                    <option value="admin">👑 Admin (Natan - Criador)</option>
                </select>
                <p id="planInfo" style="margin-top: 10px; color: #666;"></p>
            </div>
            
            <div id="chat-box" class="chat-box">
                <div class="message bot">
                    <strong>🤖 NatanAI v7.0:</strong><br><br>
                    Todas as informações atualizadas! ✅<br><br>
                    <strong>✨ Novidades:</strong><br>
                    • Projeto TAF Sem Tabu no portfólio<br>
                    • Páginas de cadastro (starter.html e professional.html)<br>
                    • Sistema de pagamento via PIX com QR Code<br>
                    • 7 projetos completos no portfólio<br><br>
                    <strong>Teste perguntas sobre cadastro, TAF Sem Tabu e mais!</strong>
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
                info: '🎁 FREE ACCESS - Contato apenas WhatsApp (21) 99282-6074'
            },
            admin: {
                plan: 'admin',
                plan_type: 'paid',
                user_name: 'Natan',
                name: 'Natan',
                email: 'natan@natandev.com',
                info: '👑 ADMIN (Natan - Criador da NatanSites)'
            },
            starter: {
                plan: 'starter',
                plan_type: 'paid',
                user_name: 'Cliente Starter',
                name: 'Cliente Starter',
                email: 'starter@teste.com',
                info: '🌱 STARTER - Suporte via página de Suporte da plataforma'
            },
            professional: {
                plan: 'professional',
                plan_type: 'paid',
                user_name: 'Cliente Pro',
                name: 'Cliente Pro',
                email: 'pro@teste.com',
                info: '💎 PROFESSIONAL - Suporte via página de Suporte da plataforma'
            }
        };

        function atualizarPlano() {
            planAtual = document.getElementById('planType').value;
            document.getElementById('planInfo').textContent = planConfigs[planAtual].info;
            const chatBox = document.getElementById('chat-box');
            chatBox.innerHTML = '<div class="message bot"><strong>🤖 NatanAI v7.0:</strong><br><br>' + 
                planConfigs[planAtual].info + '<br><br>' +
                '<strong>Teste perguntas como:</strong><br>' +
                '• "O que é o projeto TAF Sem Tabu?"<br>' +
                '• "Como faço para contratar o plano Starter?"<br>' +
                '• "Como funciona o starter.html?"<br>' +
                '• "Qual a diferença entre starter.html e professional.html?"<br>' +
                '• "Quais são os 7 projetos do portfólio?"<br>' +
                '• "Quanto tempo demora para criar minha conta?"' +
                '</div>';
        }

        atualizarPlano();
        
        async function enviar() {
            const input = document.getElementById('msg');
            const chatBox = document.getElementById('chat-box');
            const msg = input.value.trim();
            
            if (!msg) return;
            
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
                const resp = (data.response || data.resposta).replace(/\\n/g, '<br>');
                
                chatBox.innerHTML += '<div class="message bot"><strong>🤖 NatanAI v7.0:</strong><br><br>' + resp + '</div>';
                
                console.log('✅ Metadata:', data.metadata);
                
            } catch (error) {
                chatBox.innerHTML += '<div class="message bot"><strong>🤖 NatanAI:</strong><br>Erro: ' + error.message + '</div>';
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
    print("🧠 NATANAI v7.0 - TAF SEM TABU + PÁGINAS DE CADASTRO")
    print("="*80)
    print("✨ ATUALIZAÇÕES v7.0:")
    print("   🆕 Projeto TAF Sem Tabu:")
    print("      - OnePage sobre E-Book de preparação para TAF")
    print("      - Link: https://tafsemtabu.com.br")
    print("      - Stack: HTML, CSS, JavaScript")
    print("")
    print("   📄 Páginas de Cadastro:")
    print("      - starter.html: Cadastro Plano Starter (R$359,99)")
    print("      - professional.html: Cadastro Plano Professional (R$609,99)")
    print("      - Formulário: Nome, Data Nascimento, CPF")
    print("      - Pagamento: QR Code PIX + Código Copia e Cola")
    print("      - Envio automático via EmailJS")
    print("      - Tempo de criação: 10min a 2h")
    print("")
    print("   ✅ Portfólio completo com 7 projetos:")
    print("      1. Espaço Familiares")
    print("      2. DeluxModPack - GTAV")
    print("      3. Quiz Venezuela")
    print("      4. Plataforma NatanSites")
    print("      5. MathWork")
    print("      6. Alessandra Yoga")
    print("      7. TAF Sem Tabu (NOVO!)")
    print("")
    print("   📋 Informações Completas:")
    print("      - Contatos: WhatsApp (21) 99282-6074, borgesnatan09@gmail.com")
    print("      - GitHub: natsongamesoficial551")
    print("      - Stack: HTML, CSS, JS, React, Node, Python, C#")
    print("")
    print("🎁 Free Access: WhatsApp (21) 99282-6074 exclusivo")
    print("💼 Starter/Professional: Página de Suporte prioritária")
    print("📄 Cadastro: starter.html e professional.html explicados")
    print("👑 Admin: Reconhece Natan como criador")
    print("✨ Sistema de memória contextual (10 mensagens)")
    print("📝 Resumo automático a cada 5 mensagens")
    print("💰 Custo: ~$0.00024/msg = 21.000 mensagens com $5")
    print("="*80 + "\n")
    
    print(f"OpenAI: {'✅' if verificar_openai() else '⚠️'}")
    print(f"Supabase: {'✅' if supabase else '⚠️'}")
    print(f"Sistema de Memória: ✅ Ativo")
    print(f"Portfólio: ✅ Atualizado com 7 projetos (incluindo TAF Sem Tabu)")
    print(f"Páginas de Cadastro: ✅ starter.html e professional.html configurados")
    print(f"Suporte Diferenciado: ✅ Free=WhatsApp | Pagos=Página Suporte\n")
    
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
