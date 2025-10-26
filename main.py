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

warnings.filterwarnings('ignore')

app = Flask(__name__)
CORS(app)

# Configuração OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = "gpt-4o-mini"

# Inicializa cliente OpenAI apenas se a chave existir
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
                # Garante que a URL tem o protocolo
                url = RENDER_URL if RENDER_URL.startswith('http') else f"https://{RENDER_URL}"
                response = requests.get(f"{url}/health", timeout=10)
                print(f"🏓 Auto-ping OK [{response.status_code}]: {datetime.now().strftime('%H:%M:%S')}")
            else:
                # Se RENDER_URL não estiver configurada, pinga localhost
                requests.get("http://localhost:5000/health", timeout=5)
                print(f"🏓 Auto-ping local: {datetime.now().strftime('%H:%M:%S')}")
        except Exception as e:
            print(f"❌ Erro auto-ping: {e}")
        time.sleep(PING_INTERVAL)

threading.Thread(target=auto_ping, daemon=True).start()

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
    # Preços falsos
    "grátis", "gratuito", "sem custo", "de graça",
    "R$ 0", "0 reais", "free",
    
    # Promessas exageradas
    "garantimos primeiro lugar no Google",
    "100% de conversão",
    "sucesso garantido",
    "site pronto em 1 hora",
    
    # Informações falsas
    "atendimento 24/7",
    "suporte ilimitado gratuito",
    "empresa com 10 anos",
    "prêmio internacional",
    
    # Serviços não oferecidos
    "criamos aplicativos mobile nativos",
    "fazemos blockchain",
    "desenvolvemos jogos AAA",
    
    # Projetos inventados
    "[nome do cliente]",
    "[outro cliente]",
    "cliente X",
    "empresa Y"
]

# Padrões suspeitos que indicam alucinação
PADROES_SUSPEITOS = [
    r'R\$\s*0[,.]?00',  # Preço zero
    r'grát[ui]s',  # Grátis
    r'garantimos?\s+\d+',  # Garantias com números
    r'prêmio\s+\w+',  # Prêmios
    r'\d+\s+anos\s+de\s+experiência',  # Anos de experiência falsos
    r'fundado\s+em\s+\d{4}',  # Data de fundação
    r'certificação\s+ISO',  # Certificações não comprovadas
]

def validar_resposta_anti_alucinacao(resposta):
    """
    Valida resposta para evitar alucinações.
    Retorna (bool_valida, lista_problemas)
    """
    problemas = []
    
    resposta_lower = resposta.lower()
    
    # 1. Verifica palavras proibidas
    for palavra in PALAVRAS_PROIBIDAS:
        if palavra.lower() in resposta_lower:
            problemas.append(f"Palavra proibida: '{palavra}'")
    
    # 2. Verifica padrões suspeitos
    for padrao in PADROES_SUSPEITOS:
        if re.search(padrao, resposta_lower):
            match = re.search(padrao, resposta_lower)
            problemas.append(f"Padrão suspeito: '{match.group()}'")
    
    # 3. Verifica WhatsApp correto
    if "whatsapp" in resposta_lower or "telefone" in resposta_lower:
        if "21 99282-6074" not in resposta and "99282-6074" not in resposta and "(21) 99282-6074" not in resposta:
            if any(num in resposta for num in ["(11)", "(21) 9", "0800"]):
                problemas.append("Número de WhatsApp incorreto")
    
    # 4. Verifica preços corretos
    if "starter" in resposta_lower:
        if "39,99" not in resposta and "39.99" not in resposta:
            problemas.append("Preço Starter incorreto")
    
    if "professional" in resposta_lower:
        if "79,99" not in resposta and "79.99" not in resposta:
            problemas.append("Preço Professional incorreto")
    
    # 5. Verifica nome correto
    if "criador" in resposta_lower or "dono" in resposta_lower or "desenvolvedor" in resposta_lower:
        if "natan" not in resposta_lower:
            problemas.append("Nome do criador não mencionado")
    
    valida = len(problemas) == 0
    return valida, problemas

def limpar_alucinacoes(resposta):
    """
    Remove ou corrige alucinações detectadas na resposta
    """
    resposta_limpa = resposta
    
    # Remove promessas exageradas
    resposta_limpa = re.sub(r'garantimos?\s+\d+%', '', resposta_limpa)
    
    # Remove menções a anos de experiência não confirmados
    resposta_limpa = re.sub(r'\d+\s+anos\s+de\s+experiência', 'experiência comprovada', resposta_limpa)
    
    # Remove certificações não confirmadas
    resposta_limpa = re.sub(r'certificação\s+\w+', '', resposta_limpa)
    
    return resposta_limpa

# =============================================================================
# SISTEMA DE ANÁLISE DE INTENÇÃO
# =============================================================================

def analisar_intencao(pergunta):
    """Analisa a intenção das perguntas sobre serviços"""
    try:
        p = pergunta.lower().strip()
        
        intencoes = {
            "precos": 0,
            "planos": 0,
            "contato": 0,
            "portfolio": 0,
            "criar_site": 0,
            "tipos_sites": 0,
            "tempo_desenvolvimento": 0,
            "como_funciona": 0,
            "tecnologias": 0,
            "responsivo": 0,
            "seo": 0,
            "diferenciais": 0,
            "projetos_especificos": 0,
            "sobre_natan": 0,
            "geral": 0
        }
        
        # PALAVRAS-CHAVE POR CATEGORIA
        
        palavras_conversa_casual = [
            "oi", "olá", "ola", "hey", "bom dia", "boa tarde", "boa noite",
            "tudo bem", "como vai", "e ai", "tchau", "bye", "obrigado", "valeu",
            "como foi", "seu dia", "conta", "piada", "engraçado",
            "quem é você", "o que você é", "você é uma ia", "natanai"
        ]
        
        palavras_sobre_natan = [
            "quem é natan", "quem é o natan", "quem criou", "criador",
            "desenvolvedor", "sobre natan"
        ]
        
        palavras_precos = [
            "preço", "valor", "quanto custa", "custo", "valores",
            "investimento", "orçamento"
        ]
        
        palavras_planos = [
            "plano", "pacote", "planos", "starter", "professional",
            "opções", "tipos de plano"
        ]
        
        palavras_contato = [
            "contato", "whatsapp", "telefone", "falar", "ligar",
            "instagram", "email", "entrar em contato"
        ]
        
        palavras_portfolio = [
            "portfolio", "projetos", "trabalhos", "cases",
            "exemplos", "já fizeram", "feitos"
        ]
        
        palavras_criar_site = [
            "quero criar", "fazer um site", "criar meu site",
            "preciso de um site", "quero um site", "criar site"
        ]
        
        palavras_tipos_sites = [
            "que tipo", "tipos de site", "que sites", "categorias",
            "site comercial", "site interativo"
        ]
        
        palavras_tempo = [
            "quanto tempo", "demora", "prazo", "entrega",
            "rápido", "velocidade"
        ]
        
        palavras_como_funciona = [
            "como funciona", "como faço", "processo", "passo a passo",
            "como contratar", "como começar"
        ]
        
        palavras_tecnologias = [
            "tecnologia", "linguagem", "framework", "usa ia",
            "inteligência artificial", "ferramentas"
        ]
        
        palavras_responsivo = [
            "responsivo", "mobile", "celular", "tablet",
            "funciona no celular", "adapta"
        ]
        
        palavras_seo = [
            "seo", "google", "busca", "aparecer no google",
            "otimização", "ranqueamento"
        ]
        
        palavras_diferenciais = [
            "diferencial", "por que escolher", "vantagem",
            "melhor que", "destaque"
        ]
        
        palavras_projetos = [
            "espaço familiares", "mathwork", "quiz venezuela",
            "alessandra yoga", "delux", "webservico"
        ]
        
        # CONTAGEM COM PESOS
        for palavra in palavras_conversa_casual:
            if palavra in p:
                intencoes["geral"] += 2  # Peso baixo para não sobrepor serviços
        
        for palavra in palavras_sobre_natan:
            if palavra in p:
                intencoes["sobre_natan"] += 6
        
        for palavra in palavras_precos:
            if palavra in p:
                intencoes["precos"] += 7
        
        for palavra in palavras_planos:
            if palavra in p:
                intencoes["planos"] += 7
        
        for palavra in palavras_contato:
            if palavra in p:
                intencoes["contato"] += 6
        
        for palavra in palavras_portfolio:
            if palavra in p:
                intencoes["portfolio"] += 6
        
        for palavra in palavras_criar_site:
            if palavra in p:
                intencoes["criar_site"] += 8
        
        for palavra in palavras_tipos_sites:
            if palavra in p:
                intencoes["tipos_sites"] += 5
        
        for palavra in palavras_tempo:
            if palavra in p:
                intencoes["tempo_desenvolvimento"] += 6
        
        for palavra in palavras_como_funciona:
            if palavra in p:
                intencoes["como_funciona"] += 6
        
        for palavra in palavras_tecnologias:
            if palavra in p:
                intencoes["tecnologias"] += 5
        
        for palavra in palavras_responsivo:
            if palavra in p:
                intencoes["responsivo"] += 5
        
        for palavra in palavras_seo:
            if palavra in p:
                intencoes["seo"] += 5
        
        for palavra in palavras_diferenciais:
            if palavra in p:
                intencoes["diferenciais"] += 5
        
        for palavra in palavras_projetos:
            if palavra in p:
                intencoes["projetos_especificos"] += 6
        
        intencao_principal = max(intencoes, key=intencoes.get)
        score_principal = intencoes[intencao_principal]
        
        return intencao_principal if score_principal > 1 else "geral"
    
    except Exception as e:
        print(f"❌ Erro análise intenção: {e}")
        return "geral"

# =============================================================================
# BUSCA NA BASE ESPECIALIZADA
# =============================================================================

def buscar_resposta_especializada(pergunta):
    """Apenas analisa a intenção - não retorna respostas prontas"""
    try:
        intencao = analisar_intencao(pergunta)
        # Agora só retorna a intenção, sem respostas prontas
        return None, intencao
        
    except Exception as e:
        print(f"❌ Erro análise intenção: {e}")
        return None, "geral"
    
    # =============================================================================
# PROCESSAMENTO HÍBRIDO COM OPENAI + ANTI-ALUCINAÇÃO
# =============================================================================

def verificar_openai():
    """Verifica se OpenAI está disponível"""
    try:
        if not OPENAI_API_KEY or len(OPENAI_API_KEY) < 20:
            return False
        
        if client is None:
            return False
        
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[{"role": "user", "content": "teste"}],
            max_tokens=5
        )
        return True
    except Exception as e:
        print(f"❌ OpenAI indisponível: {e}")
        return False

def processar_openai_hibrido(pergunta, intencao):
    """
    Processa com OpenAI em modo HÍBRIDO com anti-alucinação
    """
    if client is None:
        return None
    
    if not verificar_openai():
        return None
    
    try:
        # Monta prompt RESTRITIVO com informações oficiais
        prompt_sistema = f"""Você é o NatanAI, assistente virtual inteligente, masculino, amigável e empático da NatanDEV!

═══════════════════════════════════════════════════════════════════
INFORMAÇÕES OFICIAIS DO NATANDEV (USE QUANDO RELEVANTE)
═══════════════════════════════════════════════════════════════════

👨‍💻 **SOBRE O CRIADOR (NATAN - O DESENVOLVEDOR):**
- Nome: Natan Borges Alves Nascimento
- Profissão: Web Developer Full-Stack
- Localização: Rio de Janeiro, Brasil
- Atendimento: Todo o Brasil (remoto)
- **IMPORTANTE: Natan é quem DESENVOLVE os sites. Você (NatanAI) é apenas a assistente virtual dele.**

🤖 **SOBRE VOCÊ (NATANAI):**
- Você é a NatanAI, criada POR Natan para ser a assistente virtual dele
- Você NÃO desenvolve sites - você apenas auxilia clientes e responde perguntas
- Sempre fale "o Natan desenvolve", "o Natan cria", "ele pode fazer"
- NUNCA diga "eu desenvolvo", "eu crio" ou "nós fazemos" - você é apenas a assistente!

📞 **CONTATOS OFICIAIS:**
- WhatsApp: (21) 99282-6074 ← PRINCIPAL
- Instagram: @nborges.ofc
- Email: borgesnatan09@gmail.com
- Site: natansites.com.br
- Portfólio: natandev02.netlify.app
- GitHub: github.com/natsongamesoficial551
- LinkedIn: linkedin.com/in/natan-borges-b3a3b5382/
- Facebook: facebook.com/profile.php?id=100076973940954

💰 **PLANOS E PREÇOS:**

🌱 PLANO STARTER - R$ 39,99/mês
   + R$ 350,00 desenvolvimento inicial (pagamento ÚNICO)
   
   Inclui:
   ✅ Site responsivo básico
   ✅ Design moderno e limpo
   ✅ Otimização para mobile
   ✅ Hospedagem inclusa
   ✅ Suporte por WhatsApp/Email
   
   Ideal para: pequenos negócios, profissionais autônomos, cartões de visita digitais

🚀 PLANO PROFESSIONAL - R$ 79,99/mês
   + R$ 530,00 desenvolvimento inicial (pagamento ÚNICO)
   
   Inclui:
   ✅ Design personalizado avançado
   ✅ Animações e interatividade
   ✅ SEO otimizado (apareça no Google!)
   ✅ Integração de APIs
   ✅ Domínio personalizado
   ✅ Formulários de contato
   ✅ Suporte prioritário
   
   OPCIONAL: IA Integrada, opcional, precisa organizar preços com o Natan

💡 IMPORTANTE: Valores de desenvolvimento inicial são pagos UMA VEZ! A mensalidade é só para hospedagem e manutenção.

💼 **PORTFÓLIO (6 PROJETOS DESENVOLVIDOS PELO NATAN):**

1. 🏠 Espaço Familiares (espacofamiliares.com.br)
   → Site para eventos especiais (casamentos, festas, dayuse)

2. 📚 MathWork (mathworkftv.netlify.app)
   → Plataforma educacional de matemática com 10 alunos

3. 🧘 Alessandra Yoga (alessandrayoga.netlify.app)
   → Cartão de visita digital profissional

4. 🎮 DeluxModPack GTAV (deluxgtav.netlify.app)
   → Modpack para GTA V desenvolvido em C# (BETA)

5. 📝 Quiz Venezuela (quizvenezuela.onrender.com)
   → Quiz educacional interativo

6. 🌐 WebServiço (webservico.netlify.app)
   → Página de apresentação de serviços

Portfólio completo: natandev02.netlify.app

🎨 **TIPOS DE SITES QUE O NATAN DESENVOLVE:**
- Sites Comerciais (empresas, consultórios, escritórios, lojas)
- Sites Interativos (animações, 3D, quizzes, calculadoras, jogos educativos)
- Sites Personalizados (funcionalidades exclusivas sob medida)

⏱️ **TEMPO DE DESENVOLVIMENTO DO NATAN:**
- Estrutura base: 3-4 horas (super rápido!)
- Projeto completo simples: 1 semana
- Projeto completo complexo: 2 semanas
- Projetos especiais: sob consulta

📋 **PROCESSO (4 PASSOS):**
1. Contato inicial via WhatsApp: (21) 99282-6074
2. Escolha do plano e planejamento com o Natan
3. Desenvolvimento pelo Natan (estrutura base em 3-4h!)
4. Revisão, ajustes e entrega

💻 **TECNOLOGIAS QUE O NATAN USA:**
- Front-end: HTML5, CSS3, JavaScript, frameworks modernos
- IA: Uso estratégico para criação visual e otimização
- Back-end: APIs modernas, integração com sistemas
- SEO: Otimização para Google (no plano Professional)
- 100% Responsivo: Mobile, tablet e desktop

⭐ **DIFERENCIAIS DO NATAN:**
- Desenvolvimento RÁPIDO (estrutura base em 3-4 horas!)
- Tecnologia de ponta com IA
- Qualidade garantida com revisão de código
- 100% responsivo (mobile-first)
- Design moderno com animações
- Preço justo e acessível
- Atendimento personalizado direto com o desenvolvedor

═══════════════════════════════════════════════════════════════════
REGRAS DE COMPORTAMENTO
═══════════════════════════════════════════════════════════════════

🎯 **CONVERSAS CASUAIS:**
- Se a pessoa falar sobre o dia, fazer piada, bater papo → Responda NATURALMENTE como um amigo!
- Seja empático, descontraído, humano e acolhedor
- Use tom leve e amigável
- NÃO force informações sobre serviços em conversas casuais

💼 **PERGUNTAS SOBRE SERVIÇOS:**
- Quando perguntarem sobre preços, sites, portfólio, contato → Use as informações oficiais acima
- **SEMPRE DEIXE CLARO QUE O NATAN É QUEM DESENVOLVE, NÃO VOCÊ!**
- Use frases como: "O Natan desenvolve...", "Ele pode criar...", "O trabalho dele inclui..."
- Seja claro, direto e entusiasmado
- Destaque os diferenciais quando relevante
- Sempre mencione o WhatsApp: (21) 99282-6074

🔗 **TRANSIÇÃO NATURAL:**
- Após responder algo casual, você PODE mencionar brevemente (1 linha) que também ajuda com sites
- Exemplo: "Aliás, se precisar de um site profissional, o Natan pode ajudar! Sou a assistente dele 😊"
- Mas APENAS se fizer sentido no contexto

❌ **PROIBIDO:**
- NUNCA diga "eu desenvolvo", "eu crio", "eu faço" quando falar de desenvolvimento
- NUNCA invente preços diferentes dos oficiais
- NUNCA invente projetos, clientes ou cases não listados
- NUNCA diga que o serviço é gratuito
- NUNCA prometa "primeiro lugar no Google garantido"
- NUNCA invente anos de experiência ou prêmios
- NUNCA mencione serviços não oferecidos (apps mobile nativos, blockchain, etc)

✅ **SE NÃO SOUBER:**
- Para dúvidas sobre serviços que não estão nas informações acima
- Direcione para contato direto: "Melhor chamar o Natan no WhatsApp: (21) 99282-6074 para tirar essa dúvida!"

🎨 **PERSONALIDADE:**
- Amigável, empática e natural
- Entusiasta quando falar dos serviços DO NATAN
- Use emojis com moderação (2-4 por resposta)
- Seja conciso para conversas casuais (máximo 100 palavras)
- Para perguntas sobre serviços, pode ser mais detalhado (até 250 palavras)
- Use "Vibrações Positivas!" ocasionalmente (30% das respostas, quando fizer sentido)

📊 **FORMATAÇÃO:**
- Conversas casuais: Texto corrido, natural, sem listas
- Perguntas sobre serviços: Pode usar emojis, listas e formatação para clareza
- Sempre organize bem as informações
- **SEMPRE deixe claro que o Natan é o desenvolvedor, não você!**

═══════════════════════════════════════════════════════════════════

**CONTEXTO DA CONVERSA ATUAL:** {intencao}

Responda de forma adequada ao contexto: casual e empática para conversa, ou detalhada e entusiasmada para serviços!

**LEMBRE-SE: Você é a ASSISTENTE do Natan. ELE desenvolve os sites, NÃO VOCÊ!**
"""

        prompt_usuario = f"Responda de forma direta e empolgante: {pergunta}"

        # Chamada OpenAI
        response = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": prompt_sistema},
                {"role": "user", "content": prompt_usuario}
            ],
            max_tokens=300,
            temperature=0.7,
            top_p=0.9,
            presence_penalty=0.1,
            frequency_penalty=0.1
        )
        
        resposta_openai = response.choices[0].message.content.strip()
        
        # VALIDAÇÃO ANTI-ALUCINAÇÃO
        valida, problemas = validar_resposta_anti_alucinacao(resposta_openai)
        
        if not valida:
            print(f"⚠️ Alucinação detectada! Problemas: {problemas}")
            resposta_openai = limpar_alucinacoes(resposta_openai)
            
            valida2, problemas2 = validar_resposta_anti_alucinacao(resposta_openai)
            if len(problemas2) > 2:
                print(f"❌ Resposta OpenAI descartada por múltiplas alucinações")
                return None
        
        # Garante que tem "Vibrações Positivas!" em algumas respostas
        if random.random() < 0.3 and "vibrações positivas" not in resposta_openai.lower():
            resposta_openai += "\n\nVibrações Positivas!"
        
        print(f"✅ Resposta OpenAI híbrida validada")
        return resposta_openai
        
    except Exception as e:
        print(f"❌ Erro OpenAI híbrido: {e}")
        return None

# =============================================================================
# GERADOR PRINCIPAL HÍBRIDO
# =============================================================================

def gerar_resposta_hibrida_otimizada(pergunta):
    """
    Sistema 100% OpenAI com validação anti-alucinação
    """
    try:
        # Cache
        pergunta_hash = hashlib.md5(pergunta.lower().strip().encode()).hexdigest()
        if pergunta_hash in CACHE_RESPOSTAS:
            return CACHE_RESPOSTAS[pergunta_hash], "cache"
        
        # Analisa intenção
        _, intencao = buscar_resposta_especializada(pergunta)
        
        # USA OPENAI PARA TUDO (com as informações oficiais no prompt)
        resposta_openai = processar_openai_hibrido(pergunta, intencao)
        if resposta_openai:
            CACHE_RESPOSTAS[pergunta_hash] = resposta_openai
            return resposta_openai, f"openai_dinamico_{intencao}"
        
        # FALLBACK apenas se OpenAI falhar
        fallback = f"Desculpa, estou com dificuldades técnicas agora. 😅\n\nChama no WhatsApp para te ajudar: (21) 99282-6074\n\nVibrações Positivas!"
        
        return fallback, "fallback_emergency"
        
    except Exception as e:
        print(f"❌ Erro geral: {e}")
        return "Para informações, fale com Natan: (21) 99282-6074\n\nVibrações Positivas!", "erro_emergency"
    
    # =============================================================================
# ROTAS DA API
# =============================================================================

@app.route('/health', methods=['GET'])
@app.route('/api/health', methods=['GET'])
def health():
    try:
        return jsonify({
            "status": "online",
            "sistema": "NatanAI v4.0 HÍBRIDA NATURAL",
            "modo": "OpenAI GPT-4o-mini + Base Especializada + Anti-Alucinação + Conversação Natural",
            "modelo": OPENAI_MODEL,
            "openai_ativo": verificar_openai(),
            "cache_size": len(CACHE_RESPOSTAS),
            "info_servicos": {
                "criador": INFORMACOES_OFICIAIS["criador"],
                "whatsapp": INFORMACOES_OFICIAIS["whatsapp"],
                "site": INFORMACOES_OFICIAIS["site"],
                "portfolio": INFORMACOES_OFICIAIS["portfolio"],
                "projetos_total": len(INFORMACOES_OFICIAIS["projetos"])
            },
            "sistema_anti_alucinacao": {
                "validacao_ativa": True,
                "palavras_proibidas": len(PALAVRAS_PROIBIDAS),
                "padroes_suspeitos": len(PADROES_SUSPEITOS),
                "limpeza_automatica": True
            },
            "funcionalidades": [
                "100% OpenAI - respostas dinâmicas e inteligentes",
                "Informações oficiais embutidas no prompt",
                "Detecção de informações inventadas",
                "Limpeza automática de alucinações",
                "Fallback de emergência",
                "Cache inteligente",
                "Conversação natural e empática"
            ]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/chat', methods=['POST'])
@app.route('/api/chat', methods=['POST'])
def chat_hibrido():
    global HISTORICO_CONVERSAS
    
    try:
        data = request.get_json()
        
        if not data or 'message' not in data and 'pergunta' not in data:
            return jsonify({"error": "Mensagem não fornecida"}), 400
        
        pergunta = data.get('message') or data.get('pergunta', '')
        pergunta = pergunta.strip()
        
        if not pergunta:
            return jsonify({"error": "Mensagem vazia"}), 400
        
        print(f"\n💬 [{datetime.now().strftime('%H:%M:%S')}] Pergunta: {pergunta}")
        
        # Gera resposta HÍBRIDA
        resposta, fonte = gerar_resposta_hibrida_otimizada(pergunta)
        
        # Validação final anti-alucinação
        valida, problemas = validar_resposta_anti_alucinacao(resposta)
        if not valida:
            print(f"⚠️ Validação final: {len(problemas)} problemas detectados")
        
        # Histórico
        with historico_lock:
            HISTORICO_CONVERSAS.append({
                "timestamp": datetime.now().isoformat(),
                "pergunta": pergunta,
                "fonte": fonte,
                "validacao_ok": valida,
                "problemas": len(problemas) if not valida else 0
            })
            
            if len(HISTORICO_CONVERSAS) > 1000:
                HISTORICO_CONVERSAS = HISTORICO_CONVERSAS[-500:]
        
        return jsonify({
            "response": resposta,
            "resposta": resposta,  # Compatibilidade
            "metadata": {
                "fonte": fonte,
                "sistema": "NatanAI v4.0 Híbrida Natural",
                "modelo": OPENAI_MODEL if "openai" in fonte else "Base Especializada",
                "validacao_anti_alucinacao": valida,
                "modo_hibrido": True,
                "confiabilidade": "alta" if valida else "media"
            }
        })
        
    except Exception as e:
        print(f"❌ Erro no chat: {e}")
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
        "versao": "4.0 - Híbrida Natural (OpenAI + Base Especializada + Conversação)",
        "criador": INFORMACOES_OFICIAIS["criador"],
        "profissao": INFORMACOES_OFICIAIS["profissao"],
        "modelo": {
            "nome": OPENAI_MODEL,
            "tipo": "OpenAI GPT-4o-mini",
            "status": "🟢 Online" if verificar_openai() else "🔴 Offline",
            "modo": "Híbrido com anti-alucinação e conversação natural"
        },
        "contato": {
            "whatsapp": INFORMACOES_OFICIAIS["whatsapp"],
            "instagram": INFORMACOES_OFICIAIS["instagram"],
            "email": INFORMACOES_OFICIAIS["email"],
            "site": INFORMACOES_OFICIAIS["site"],
            "portfolio": INFORMACOES_OFICIAIS["portfolio"]
        },
        "planos": INFORMACOES_OFICIAIS["planos"],
        "projetos": INFORMACOES_OFICIAIS["projetos"],
        "localizacao": INFORMACOES_OFICIAIS["localizacao"],
        "atendimento": INFORMACOES_OFICIAIS["atendimento"]
    })

@app.route('/estatisticas', methods=['GET'])
@app.route('/api/estatisticas', methods=['GET'])
def estatisticas():
    try:
        if not HISTORICO_CONVERSAS:
            return jsonify({"message": "Nenhuma conversa registrada"})
        
        fontes_count = {}
        validacoes_ok = 0
        total_problemas = 0
        
        with historico_lock:
            for conv in HISTORICO_CONVERSAS:
                fonte = conv.get("fonte", "unknown")
                fontes_count[fonte] = fontes_count.get(fonte, 0) + 1
                
                if conv.get("validacao_ok", True):
                    validacoes_ok += 1
                total_problemas += conv.get("problemas", 0)
        
        return jsonify({
            "total_conversas": len(HISTORICO_CONVERSAS),
            "distribuicao_fontes": fontes_count,
            "validacao_anti_alucinacao": {
                "respostas_validadas": validacoes_ok,
                "taxa_sucesso": round((validacoes_ok / len(HISTORICO_CONVERSAS)) * 100, 2),
                "total_problemas_detectados": total_problemas,
                "media_problemas": round(total_problemas / len(HISTORICO_CONVERSAS), 2)
            },
            "sistema": "NatanAI v4.0 Híbrida Natural",
            "modo": "OpenAI + Base Especializada + Anti-Alucinação + Conversação"
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
        "dica": "A NatanAI conversa naturalmente E conhece tudo sobre os serviços da NatanDEV! Pergunte qualquer coisa! 🚀",
        "modelo": f"Usando OpenAI {OPENAI_MODEL} com sistema anti-alucinação e conversação natural"
    })

@app.route('/ping', methods=['GET'])
def ping():
    return jsonify({
        "status": "pong",
        "timestamp": datetime.now().isoformat(),
        "sistema": "NatanAI v4.0 Híbrida Natural"
    })

@app.route('/', methods=['GET'])
def home():
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>NatanAI v4.0 - Híbrida Natural</title>
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
                max-width: 850px; 
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
            .badge-hybrid { background: linear-gradient(135deg, #667eea, #764ba2); color: white; }
            .badge-ai { background: #4CAF50; color: white; }
            .badge-safe { background: #2196F3; color: white; }
            .badge-natural { background: #FF6B6B; color: white; }
            
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
            .bot-hybrid {
                background: linear-gradient(135deg, #e3f2fd, #f3e5f5);
                border-left: 4px solid #667eea;
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
            button:active {
                transform: translateY(0);
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
                <h1>🤖 NatanAI v4.0 - HÍBRIDA NATURAL</h1>
                <p style="color: #666; margin: 10px 0;">Assistente Inteligente da NatanDEV</p>
                <div>
                    <span class="badge badge-hybrid">MODO HÍBRIDO</span>
                    <span class="badge badge-ai">OpenAI GPT-4o-mini</span>
                    <span class="badge badge-safe">Anti-Alucinação</span>
                    <span class="badge badge-natural">Conversação Natural</span>
                </div>
            </div>
            
            <div class="info-box">
                <h3>🎯 Sistema Híbrido Natural</h3>
                <ul>
                    <li><strong>Conversação Natural:</strong> Responde perguntas casuais de forma empática e humana</li>
                    <li><strong>Base Especializada:</strong> Respostas 100% confiáveis sobre serviços</li>
                    <li><strong>OpenAI GPT-4o-mini:</strong> Inteligência avançada para qualquer pergunta</li>
                    <li><strong>Validação Anti-Alucinação:</strong> Verifica e corrige informações inventadas</li>
                </ul>
            </div>
            
            <div class="info-box">
                <h3>📍 Sobre a NatanDEV</h3>
                <ul>
                    <li><strong>Criador:</strong> Natan Borges Alves Nascimento - Web Developer Full-Stack</li>
                    <li><strong>WhatsApp:</strong> (21) 99282-6074</li>
                    <li><strong>Planos:</strong> Starter (R$ 39,99/mês) | Professional (R$ 79,99/mês)</li>
                    <li><strong>Projetos:</strong> 6+ sites entregues (Espaço Familiares, MathWork, etc.)</li>
                    <li><strong>Portfólio:</strong> natandev02.netlify.app</li>
                    <li><strong>Diferencial:</strong> Estrutura base em 3-4 horas!</li>
                </ul>
            </div>
            
            <div id="chat-box" class="chat-box">
                <div class="message bot bot-hybrid">
                    <strong>🤖 NatanAI v4.0 Natural:</strong><br><br>
                    Oi! Sou a NatanAI! 😊<br><br>
                    
                    <strong>Posso conversar sobre qualquer coisa:</strong><br>
                    💬 Bater papo casual<br>
                    💰 Informações sobre sites e serviços<br>
                    📞 Contatos e portfólio<br>
                    🚀 Processos de desenvolvimento<br><br>
                    
                    Seja você mesmo! Pergunta o que quiser! 💜<br><br>
                    
                    <strong>Vibrações Positivas!</strong>
                </div>
            </div>
            
            <div class="examples">
                <button class="example-btn" onclick="testar('Oi, tudo bem?')">👋 Saudação</button>
                <button class="example-btn" onclick="testar('Quanto custa um site?')">💰 Preços</button>
                <button class="example-btn" onclick="testar('Quero criar um site')">🚀 Criar Site</button>
                <button class="example-btn" onclick="testar('Como foi seu dia?')">💬 Casual</button>
                <button class="example-btn" onclick="testar('Qual o portfólio?')">💼 Portfólio</button>
                <button class="example-btn" onclick="testar('Conta uma piada')">😄 Piada</button>
            </div>
            
            <div class="input-area">
                <input type="text" id="msg" placeholder="Digite sua pergunta ou só bata um papo..." onkeypress="if(event.key==='Enter') enviar()">
                <button onclick="enviar()">Enviar</button>
            </div>
            
            <div class="footer">
                <p><strong>NatanAI v4.0 - Sistema Híbrido Natural</strong></p>
                <p>OpenAI GPT-4o-mini + Base Especializada + Anti-Alucinação + Conversação Natural</p>
                <p style="margin-top: 10px;">📞 WhatsApp: (21) 99282-6074 | 🌐 natansites.com.br</p>
                <p style="margin-top: 5px;">📸 Instagram: @nborges.ofc | 💼 natandev02.netlify.app</p>
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
            
            // Mensagem do usuário
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
                
                // Determina classe CSS baseada na fonte
                let className = 'message bot';
                if (metadata.fonte && metadata.fonte.includes('openai')) {
                    className += ' bot-hybrid';
                }
                
                // Monta badges de metadata
                let metadataBadges = '';
                if (metadata.fonte) {
                    metadataBadges += `<span class="metadata-badge">📊 ${metadata.fonte}</span>`;
                }
                if (metadata.validacao_anti_alucinacao !== undefined) {
                    const validIcon = metadata.validacao_anti_alucinacao ? '✅' : '⚠️';
                    metadataBadges += `<span class="metadata-badge">${validIcon} Validação</span>`;
                }
                if (metadata.confiabilidade) {
                    metadataBadges += `<span class="metadata-badge">🎯 ${metadata.confiabilidade}</span>`;
                }
                
                // Resposta da IA
                const respText = (data.response || data.resposta).replace(/\n/g, '<br>');
                chatBox.innerHTML += `
                    <div class="${className}">
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
    print("🤖 NATANAI v4.0 - SISTEMA HÍBRIDO NATURAL")
    print("="*80)
    print("👨‍💻 Criador: Natan Borges Alves Nascimento")
    print("🚀 Web Developer Full-Stack")
    print("📞 WhatsApp: (21) 99282-6074")
    print("🌐 Site: natansites.com.br")
    print("💼 Portfólio: natandev02.netlify.app")
    print("="*80)
    
    # Sistema 100% dinâmico com OpenAI
    print("✅ Sistema configurado: 100% OpenAI com informações oficiais no prompt")
    
    # Verifica OpenAI
    openai_status = verificar_openai()
    
    print(f"\n🔧 CONFIGURAÇÃO:")
    print(f"   • Modelo: {OPENAI_MODEL}")
    print(f"   • OpenAI: {'✅ CONECTADO' if openai_status else '⚠️ OFFLINE'}")
    print(f"   • Sistema Anti-Alucinação: ✅ ATIVO")
    print(f"   • Palavras Proibidas: {len(PALAVRAS_PROIBIDAS)}")
    print(f"   • Padrões Suspeitos: {len(PADROES_SUSPEITOS)}")
    
    print(f"\n🎯 MODO 100% DINÂMICO:")
    print(f"   1️⃣ OpenAI cria TODAS as respostas dinamicamente")
    print(f"   2️⃣ Informações oficiais embutidas no prompt do sistema")
    print(f"   3️⃣ Validação anti-alucinação (sempre ativa)")
    print(f"   4️⃣ Sem respostas prontas - 100% inteligente e adaptável")
    
    print(f"\n🛡️ PROTEÇÕES ANTI-ALUCINAÇÃO:")
    print(f"   ✅ Validação de informações oficiais")
    print(f"   ✅ Detecção de palavras proibidas: {len(PALAVRAS_PROIBIDAS)}")
    print(f"   ✅ Detecção de padrões suspeitos: {len(PADROES_SUSPEITOS)}")
    print(f"   ✅ Limpeza automática de alucinações")
    print(f"   ✅ Verificação de preços corretos")
    print(f"   ✅ Verificação de WhatsApp correto")
    print(f"   ✅ Verificação de nome do criador")
    print(f"   ✅ Bloqueio de projetos inventados")
    
    print(f"\n💬 CONVERSAÇÃO NATURAL:")
    print(f"   ✅ Responde perguntas casuais com empatia")
    print(f"   ✅ Bate papo amigável e descontraído")
    print(f"   ✅ Não força informações de serviços")
    print(f"   ✅ Transições naturais quando relevante")
    
    print(f"\n📊 INFORMAÇÕES DOS SERVIÇOS:")
    print(f"   • Planos: Starter (R$ 39,99/mês) | Professional (R$ 79,99/mês)")
    print(f"   • Projetos entregues: {len(INFORMACOES_OFICIAIS['projetos'])}")
    print(f"   • Desenvolvimento base: 3-4 horas")
    print(f"   • Projeto completo: 1-2 semanas")
    
    print(f"\n🚀 SERVIDOR INICIANDO...")
    print(f"   • Porta: 5000")
    print(f"   • Host: 0.0.0.0")
    print(f"   • Debug: False")
    print(f"   • Threaded: True")
    
    print("\n" + "="*80)
    print("📞 CONTATO: WhatsApp (21) 99282-6074")
    print("🌐 SITE: natansites.com.br")
    print("💼 PORTFÓLIO: natandev02.netlify.app")
    print("="*80 + "\n")
    
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=False,
        threaded=True
    )
